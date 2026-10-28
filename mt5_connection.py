"""
MT5 Persistent Connection Manager & Market Structure Engine.

Maintains a long-lived connection to the MT5 Manager API via a background
daemon thread that refreshes an in-memory cache at configurable intervals.
Frontend endpoints read from the cache for sub-second response times.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import heapq
from contextvars import ContextVar
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import MT5Manager

LOGGER = logging.getLogger("metafive.mt5")

# ---------------------------------------------------------------------------
# Polling configuration constants
# ---------------------------------------------------------------------------
ENGINE_LOOP_INTERVAL_SECONDS = max(float(os.getenv("MT5_ENGINE_LOOP_SECONDS", "0.25")), 0.05)
# Account data is the slowest core request. Two seconds keeps the UI responsive
# without turning every browser poll into a native Manager request.
USER_REFRESH_INTERVAL_SECONDS = max(float(os.getenv("MT5_USER_REFRESH_SECONDS", "2")), 1.0)
POSITION_REFRESH_INTERVAL_SECONDS = max(float(os.getenv("MT5_POSITION_REFRESH_SECONDS", "1")), 0.25)
METRICS_REFRESH_INTERVAL_SECONDS = POSITION_REFRESH_INTERVAL_SECONDS
PANELS_REFRESH_INTERVAL_SECONDS = max(float(os.getenv("MT5_PANELS_REFRESH_SECONDS", "2")), 1.0)
SYMBOL_REFRESH_INTERVAL_SECONDS = max(float(os.getenv("MT5_SYMBOL_REFRESH_SECONDS", "60")), 10.0)
GROUP_REFRESH_INTERVAL_SECONDS = max(float(os.getenv("MT5_GROUP_REFRESH_SECONDS", "30")), 5.0)
WATCHDOG_INTERVAL_SECONDS = max(float(os.getenv("MT5_WATCHDOG_SECONDS", "10")), 3.0)
RECONNECT_BACKOFF_SECONDS = max(float(os.getenv("MT5_RECONNECT_BACKOFF_SECONDS", "5")), 1.0)
TICK_REFRESH_INTERVAL_SECONDS = max(float(os.getenv("MT5_TICK_REFRESH_SECONDS", "0.2")), 0.1)
TICK_REFRESH_BATCH_SIZE = max(int(os.getenv("MT5_TICK_REFRESH_BATCH_SIZE", "160")), 1)
TICK_LIVE_MAX_AGE_SECONDS = max(int(os.getenv("MT5_TICK_LIVE_MAX_AGE_SECONDS", "30")), 1)
# Fetching every user, account metric, and position is not viable on large
# installations.  Those deployments use the focused live-risk route instead.
FULL_SNAPSHOT_ENABLED = os.getenv("MT5_FULL_SNAPSHOT_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}

# SDK constant placeholders
USER_RIGHT_ENABLED = 1
POSITION_BUY = 0
POSITION_SELL = 1


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
def coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_field(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1e11:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def quote_reference_time(server_time_msc: Any, checked_at: Any, now: datetime | None = None) -> datetime:
    """Compare broker tick times with the broker clock, accounting for its zone."""
    now = now or datetime.now(timezone.utc)
    server_time = to_datetime(server_time_msc)
    checked_time = to_datetime(checked_at)
    if server_time and checked_time:
        age = (now - checked_time).total_seconds()
        if 0 <= age <= 60:
            return server_time + timedelta(seconds=age)
    return now


def _last_error_text() -> str:
    try:
        err = MT5Manager.LastError()
        if err:
            return str(err)
    except Exception:
        pass
    return ""


def _as_list(data: Any) -> list:
    if data is None:
        return []
    if isinstance(data, (list, tuple)):
        return list(data)
    try:
        return list(data)
    except Exception:
        return [data]


def _response_payload(data: Any) -> Any:
    if isinstance(data, tuple) and len(data) == 2 and (data[1] is None or isinstance(data[1], str)):
        return data[0]
    return data


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    """Flexible attribute/key lookup matching the main api.py helper."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        for name in names:
            for key in (name, str(name).lower(), str(name).upper()):
                if key in obj:
                    return obj.get(key, default)
    if hasattr(obj, "_asdict"):
        try:
            data = obj._asdict()
            if isinstance(data, dict):
                for name in names:
                    for key in (name, str(name).lower(), str(name).upper()):
                        if key in data:
                            return data.get(key, default)
        except Exception:
            pass
    for name in names:
        for candidate in (name, str(name).lower(), str(name).upper()):
            try:
                val = getattr(obj, candidate)
                return val() if callable(val) else val
            except Exception:
                continue
    return default


# ---------------------------------------------------------------------------
# Snapshot dataclass
# ---------------------------------------------------------------------------
@dataclass
class MT5ConnectionSnapshot:
    users: List[Dict[str, Any]]
    positions: List[Dict[str, Any]]
    orders: Dict[int, List[Dict[str, Any]]]
    summary_rows: List[Dict[str, Any]]
    exposure_rows: List[Dict[str, Any]]
    metrics: Dict[str, Any]
    symbol_cache: Dict[str, Any]
    tick_cache: Dict[str, Any]
    groups: List[str]
    last_update: float
    status: str
    errors: List[str]
    refresh_phase: str = "unknown"


# ---------------------------------------------------------------------------
# MT5ConnectionManager
# ---------------------------------------------------------------------------
class MT5ConnectionManager:
    """Persistent connection to MT5 Manager API with background cache refresh."""

    def __init__(self, server: str, login: int, password: str):
        self.server = server
        self.manager_login = login
        self.manager_password = password
        self.api: Any = None
        self.native_mode = bool(os.getenv("RMS_NATIVE_URL"))
        self.native_bridge = None
        self.capabilities: Dict[str, bool] = {}
        self.stop_event = threading.Event()
        self.api_lock = threading.Lock()
        self.cache_lock = threading.Lock()
        self.monitoring_lock = threading.Lock()

        self.all_logins: List[int] = []
        now = time.time()
        self.last_refresh: Dict[str, float] = {
            # Delay optional/slow native calls until after the core live cache
            # has had a chance to publish at least once.
            "watchdog": now,
            "symbols": now,
            "users": 0.0,
            "positions": 0.0,
            "panels": 0.0,
            "metrics": 0.0,
            "ticks": now,
            "groups": 0.0,
        }
        self.monitoring: Dict[str, Any] = {
            "api_calls": {},
            "last_latency_ms": {},
            "error_count": 0,
            "reconnects": 0,
            "refresh_cycles": 0,
            "last_cycle_ms": 0.0,
        }
        self.tick_cursor = 0
        self.priority_ticks: set[str] = set()
        self.thread: Optional[threading.Thread] = None
        self.tick_thread: Optional[threading.Thread] = None
        self.cache: Dict[str, Any] = self._fresh_cache()

    # -- cache helpers -------------------------------------------------------

    def _fresh_cache(self) -> Dict[str, Any]:
        return {
            "users": [],
            "positions": [],
            "orders": {},
            "summary_rows": [],
            "exposure_rows": [],
            "metrics": {},
            "symbol_cache": {},
            "tick_cache": {},
            "groups": [],
            "users_by_login": {},
            "positions_by_login": {},
            "last_update": 0.0,
            "server_time_msc": 0,
            "server_time_checked_at": None,
            "errors": [],
            "status": "disconnected",
            "refresh_phase": "not_started",
        }

    def _mark_refresh_phase(self, phase: str) -> None:
        with self.cache_lock:
            self.cache["refresh_phase"] = phase

    def group_cache_view(self) -> List[str]:
        """Return the authoritative configured-group catalog."""
        with self.cache_lock:
            return list(self.cache.get("groups", []))

    # -- connection lifecycle ------------------------------------------------

    def _create_connected_api(self) -> Any:
        """Create a fully connected SDK handle without mutating live state."""
        api_handle = MT5Manager.ManagerAPI()
        modes = getattr(MT5Manager.ManagerAPI, "EnPumpModes", None)
        # In native mode the C++ collector owns the live user/position/order
        # book. Keep only report metadata here; reports use explicit requests.
        pump_names = ("PUMP_MODE_GROUPS", "PUMP_MODE_SYMBOLS", "PUMP_MODE_TIME") if self.native_mode else (
            "PUMP_MODE_USERS", "PUMP_MODE_GROUPS", "PUMP_MODE_SYMBOLS",
            "PUMP_MODE_TIME", "PUMP_MODE_POSITIONS", "PUMP_MODE_ORDERS",
        )
        pump_mask = 0
        for name in pump_names:
            pump_mask |= int(getattr(modes, name, 0)) if modes is not None else 0

        attempts = [
            (self.server, self.manager_login, self.manager_password, pump_mask or 1, 30000),
            (self.server, self.manager_login, self.manager_password, 30000),
            (self.server, self.manager_login, self.manager_password),
        ]
        for args in attempts:
            try:
                result = api_handle.Connect(*args)
            except TypeError:
                continue
            except Exception as exc:
                try:
                    api_handle.Disconnect()
                except Exception:
                    pass
                raise RuntimeError(_last_error_text() or str(exc)) from exc
            if bool(result):
                return api_handle
            break

        try:
            api_handle.Disconnect()
        except Exception:
            pass
        raise RuntimeError(_last_error_text() or "Unable to connect to MT5 server.")

    def connect(self) -> None:
        """Establish MT5 connection and start background refresh loop."""
        if self.is_connected:
            return
        api_handle = self._create_connected_api()

        with self.api_lock:
            self.api = api_handle

        # Probe capabilities
        self._probe_capabilities()

        # Start the background daemon
        self.stop_event.clear()
        if self.native_mode:
            from services.native_bridge import NativeBridge
            self.native_bridge = NativeBridge(self)
            self.thread = threading.Thread(target=self.native_bridge.run, name="rms-native-feed", daemon=True)
            self.thread.start()
            LOGGER.info("Native Go/C++ feed enabled; Python connection is reserved for existing analytics")
            return
        self.thread = threading.Thread(
            target=self._refresh_loop, name="metafive-refresh", daemon=True
        )
        self.thread.start()
        self.tick_thread = threading.Thread(
            target=self._tick_loop, name="metafive-ticks", daemon=True
        )
        self.tick_thread.start()
        LOGGER.info("MT5ConnectionManager started for %s (login %s)", self.server, self.manager_login)

    def disconnect(self) -> None:
        """Stop background loop and disconnect."""
        self.stop_event.set()
        if self.native_bridge is not None:
            self.native_bridge.close()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5.0)
        if self.tick_thread and self.tick_thread.is_alive():
            self.tick_thread.join(timeout=5.0)
        with self.api_lock:
            if self.api is not None:
                try:
                    self.api.Disconnect()
                except Exception:
                    pass
                self.api = None
        with self.cache_lock:
            self.cache = self._fresh_cache()
        LOGGER.info("MT5ConnectionManager disconnected.")

    @property
    def is_connected(self) -> bool:
        # Connection state is replaced atomically. Do not wait behind a long
        # native SDK refresh just to answer health checks or route requests.
        return self.api is not None and not self.stop_event.is_set()

    # -- snapshot ------------------------------------------------------------

    @contextmanager
    def _cache_generation(self):
        # Native publication replaces immutable collections. Readers can retain
        # that generation while copying/filtering it without blocking the feed.
        if self.native_mode:
            with self.cache_lock:
                generation = dict(self.cache)
            yield generation
        else:
            # The legacy polling path still has in-place writers.
            with self.cache_lock:
                yield self.cache

    def snapshot(self) -> MT5ConnectionSnapshot:
        with self._cache_generation() as cache:
            if self.native_mode:
                # The collector schema contains flat account/position/quote
                # records. Copy returned rows without recursively traversing
                # the full cache and its unused login indexes on every report.
                data = dict(cache)
                for key in ("users", "positions", "orders", "summary_rows", "exposure_rows"):
                    data[key] = [dict(row) for row in cache.get(key, [])]
                for key in ("symbol_cache", "tick_cache"):
                    data[key] = {name: dict(row) for name, row in cache.get(key, {}).items()}
                data["metrics"] = copy.deepcopy(cache.get("metrics", {}))
                data["groups"] = list(cache.get("groups", []))
                data["errors"] = list(cache.get("errors", []))
            else:
                data = copy.deepcopy(cache)
        return MT5ConnectionSnapshot(
            users=data["users"],
            positions=data["positions"],
            orders=data["orders"],
            summary_rows=data["summary_rows"],
            exposure_rows=data["exposure_rows"],
            metrics=data["metrics"],
            symbol_cache=data["symbol_cache"],
            tick_cache=data["tick_cache"],
            groups=data.get("groups", []),
            last_update=data["last_update"],
            status=data["status"],
            errors=data["errors"],
            refresh_phase=data.get("refresh_phase", "unknown"),
        )

    def update_marker(self) -> float:
        """Read the published generation without copying large account caches."""
        with self.cache_lock:
            return float(self.cache.get("last_update", 0.0))

    def live_view_marker(self):
        """Track published data versions independently of observation freshness."""
        with self.cache_lock:
            if self.native_mode and self.cache.get("native_sequence") is not None:
                return self.cache.get("native_epoch"), self.cache["native_sequence"]
            return float(self.cache.get("last_update", 0.0))

    def market_cache_status(self) -> Dict[str, Any]:
        """Small quote-health view without copying account or position data."""
        with self.cache_lock:
            symbols = len(self.cache.get("symbol_cache", {}))
            ticks = list(self.cache.get("tick_cache", {}).values())
            server_time_msc = coerce_int(self.cache.get("server_time_msc"))
            server_time_checked_at = self.cache.get("server_time_checked_at")
        now = quote_reference_time(server_time_msc, server_time_checked_at)
        live = 0
        newest: datetime | None = None
        for tick in ticks:
            timestamp = to_datetime(tick.get("tick_time"))
            if timestamp is None:
                continue
            if newest is None or timestamp > newest:
                newest = timestamp
            if tick.get("available") and 0 <= (now - timestamp).total_seconds() <= TICK_LIVE_MAX_AGE_SECONDS:
                live += 1
        return {
            "symbols": symbols,
            "cached_ticks": len(ticks),
            "live_ticks": live,
            "last_tick": newest.isoformat() if newest else None,
            "server_time_msc": server_time_msc or None,
            "server_time_checked_at": server_time_checked_at,
            "tick_worker_alive": bool(self.thread and self.thread.is_alive()) if self.native_mode else bool(self.tick_thread and self.tick_thread.is_alive()),
        }

    def dashboard_cache_view(self) -> Dict[str, Any]:
        with self._cache_generation() as cache:
            return {
                "users": [dict(row) for row in cache.get("users", [])],
                "positions_count": len(cache.get("positions", [])),
                "summary_rows": [dict(row) for row in cache.get("summary_rows", [])],
                "exposure_rows": [dict(row) for row in cache.get("exposure_rows", [])],
                "metrics": dict(cache.get("metrics", {})),
                "group_currencies": dict(cache.get("group_currencies", {})),
                "native_money_totals": cache.get("native_money_totals"),
                "last_update": cache.get("last_update", 0.0),
                "server_time_msc": cache.get("server_time_msc", 0),
                "server_time_checked_at": cache.get("server_time_checked_at"),
                "status": cache.get("status", "disconnected"),
                "errors": list(cache.get("errors", [])),
                "refresh_phase": cache.get("refresh_phase", "unknown"),
            }

    def live_risk_cache_view(self, groups: List[str] | None = None, *, active_only=False, excluded_logins=None, include_login=None) -> Dict[str, Any]:
        """Copy only the fields needed by the high-frequency live-risk route."""
        with self._cache_generation() as cache:
            source_users = cache.get("users", [])
            source_positions = cache.get("positions", [])
            portfolio = None
            indexed = cache.get("users_by_login", {})
            fast = (active_only and self.native_mode and "native_money_totals" in cache
                    and len(indexed) == len(source_users)
                    and sum(bucket["accounts"] for bucket in cache["native_money_totals"].values()) == len(source_users))
            if fast:
                from services.native_money import portfolio_summary
                selected = set(groups) if groups is not None else set(cache.get("groups", []))
                excluded = {int(login) for login in (excluded_logins or [])}
                source_positions = [row for row in source_positions if
                    (profile := indexed.get(coerce_int(row.get("Login")))) is not None
                    and str(profile.get("Group", "")) in selected and coerce_int(row.get("Login")) not in excluded]
                logins = {coerce_int(row.get("Login")) for row in source_positions}
                if include_login not in excluded and include_login in indexed and str(indexed[include_login].get("Group", "")) in selected:
                    logins.add(include_login)
                source_users = [indexed[login] for login in sorted(logins)]
                portfolio = portfolio_summary(cache["native_money_totals"], selected,
                    cache.get("group_currencies", {}), [indexed[login] for login in excluded if login in indexed])
            elif groups is not None:
                group_set = {str(group).strip() for group in groups if str(group).strip()}
                source_users = [
                    row for row in source_users
                    if str(row.get("Group", "")).strip() in group_set
                ] if group_set else []
                scoped_logins = {
                    coerce_int(row.get("Login")) for row in source_users
                    if coerce_int(row.get("Login")) > 0
                }
                source_positions = [
                    row for row in source_positions
                    if coerce_int(row.get("Login")) in scoped_logins
                ]
            symbols = cache.get("symbol_cache", {})
            positions = []
            for source in source_positions:
                row = dict(source)
                symbol = symbols.get(str(row.get("Symbol", "")), {})
                row["ContractSize"] = coerce_float(row.get("ContractSize")) or coerce_float(
                    symbol.get("Contract Size", symbol.get("contract_size"))
                )
                row["CurrencyProfit"] = str(symbol.get("Currency Profit", "") or "")
                row["CalcMode"] = symbol.get("CalcMode")
                positions.append(row)
            return {
                "users": [dict(row, Currency=str(cache.get("group_currencies", {}).get(str(row.get("Group", "")), "") or row.get("Currency") or "")) for row in source_users],
                "positions": positions,
                "groups": list(cache.get("groups", [])),
                "last_update": cache.get("last_update", 0.0),
                "server_time_msc": cache.get("server_time_msc", 0),
                "errors": list(cache.get("errors", [])),
                "refresh_phase": cache.get("refresh_phase", "unknown"),
                **({"portfolio_summary": portfolio} if portfolio is not None else {}),
            }

    # -- api call helper -----------------------------------------------------

    def call(self, method_name: str, *args, required: bool = True, default: Any = None) -> Any:
        with self.api_lock:
            if self.api is None:
                raise RuntimeError("Manager API is not connected.")
            method = getattr(self.api, method_name, None)
            if method is None:
                if required:
                    raise RuntimeError(f"MT5 API method not available: {method_name}")
                return default
            try:
                return method(*args)
            except Exception as exc:
                if required:
                    raise
                LOGGER.warning("Optional call %s failed: %s", method_name, exc)
                return default

    # -- capability probing --------------------------------------------------

    def _probe_capabilities(self) -> None:
        self.capabilities = {
            "summary_all": hasattr(self.api, "SummaryGetAll"),
            "exposure_all": hasattr(self.api, "ExposureGetAll"),
            "tick_last": hasattr(self.api, "TickLast"),
            "user_request_by_group": hasattr(self.api, "UserRequestByGroup"),
            "position_get_by_logins": hasattr(self.api, "PositionGetByLogins"),
        }
        LOGGER.info("MT5 capabilities: %s", self.capabilities)

    # -- data fetchers -------------------------------------------------------

    def fetch_all_users(self, *, strict: bool = False) -> List[Any]:
        raw = None
        errors: list[Exception] = []
        for method_name in ("UserRequestByGroup", "UserGetByGroup"):
            if hasattr(self.api, method_name):
                try:
                    raw = self.call(method_name, "*", required=strict, default=None)
                    if raw is not None:
                        break
                except Exception as exc:
                    errors.append(exc)
                    continue

        if raw is None and hasattr(MT5Manager, "UserArray"):
            try:
                container = MT5Manager.UserArray()
                for method_name in ("UserGetByGroup", "UserRequestByGroup"):
                    if hasattr(self.api, method_name):
                        ok = self.call(method_name, "*", container, required=strict, default=False)
                        if bool(ok):
                            raw = container
                            break
            except Exception as exc:
                errors.append(exc)

        if strict and raw is None and errors:
            raise RuntimeError(f"Unable to fetch the complete Manager client catalog: {errors[-1]}") from errors[-1]

        return _as_list(raw) if raw is not None else []

    def fetch_accounts_by_logins(self, logins: List[int], *, strict: bool = False) -> Dict[int, Any]:
        ordered = [coerce_int(login) for login in logins if coerce_int(login) > 0]
        if not ordered:
            return {}

        accounts: Dict[int, Any] = {}
        errors: list[Exception] = []
        for method_name in ("UserAccountGetByGroup", "UserAccountRequestByGroup"):
            if not hasattr(self.api, method_name):
                continue
            try:
                raw = _response_payload(
                    self.call(method_name, "*", required=strict, default=None)
                )
                for account in _as_list(raw):
                    login = coerce_int(_value(account, "Login", "login"))
                    if login > 0:
                        accounts[login] = account
            except Exception as exc:
                errors.append(exc)
                accounts.clear()
                continue
            if accounts:
                return accounts

        for method_name in ("UserAccountRequestByLogins", "UserAccountGetByLogins"):
            if not hasattr(self.api, method_name):
                continue
            try:
                for offset in range(0, len(ordered), 500):
                    chunk = ordered[offset : offset + 500]
                    raw = _response_payload(self.call(method_name, chunk, required=strict, default=None))
                    for account in _as_list(raw):
                        login = coerce_int(_value(account, "Login", "login"))
                        if login > 0:
                            accounts[login] = account
            except Exception as exc:
                errors.append(exc)
                accounts.clear()
                continue
            if accounts:
                return accounts

        for method_name in ("UserAccountGet", "UserAccountRequest"):
            if not hasattr(self.api, method_name):
                continue
            try:
                for login in ordered[:500]:
                    account = _response_payload(self.call(method_name, login, required=strict, default=None))
                    rows = _as_list(account) or ([account] if account and not isinstance(account, bool) else [])
                    for row in rows:
                        row_login = coerce_int(_value(row, "Login", "login"), login)
                        if row_login > 0:
                            accounts[row_login] = row
            except Exception as exc:
                errors.append(exc)
                accounts.clear()
                continue
            if accounts:
                break
        if strict and not accounts and errors:
            raise RuntimeError(f"Unable to fetch Manager client accounts: {errors[-1]}") from errors[-1]
        return accounts

    def fetch_positions(self, logins: List[int] | None = None, *, strict: bool = False) -> List[Any]:
        raw = None
        errors: list[Exception] = []
        requested_logins = self.all_logins if logins is None else logins
        if requested_logins:
            for method_name in ("PositionRequestByLogins", "PositionGetByLogins"):
                if hasattr(self.api, method_name):
                    try:
                        raw = self.call(method_name, requested_logins, required=strict, default=None)
                        if raw is not None:
                            break
                    except Exception as exc:
                        errors.append(exc)
                        continue
        if raw is None:
            for method_name in ("PositionRequestByGroup", "PositionGetByGroup"):
                if hasattr(self.api, method_name):
                    try:
                        raw = self.call(method_name, "*", required=strict, default=None)
                        if raw is not None:
                            break
                    except Exception as exc:
                        errors.append(exc)
                        continue
        if strict and raw is None and errors:
            raise RuntimeError(f"Unable to fetch the complete Manager position catalog: {errors[-1]}") from errors[-1]
        return _as_list(raw) if raw is not None else []

    def fetch_summary_rows(self, *, strict: bool = False) -> List[Any]:
        if not self.capabilities.get("summary_all", False):
            return []
        raw = self.call("SummaryGetAll", required=strict, default=[])
        return _as_list(raw) if raw else []

    def fetch_exposure_rows(self, *, strict: bool = False) -> List[Any]:
        if not self.capabilities.get("exposure_all", False):
            return []
        raw = self.call("ExposureGetAll", required=strict, default=[])
        return _as_list(raw) if raw else []

    # -- row normalizers -----------------------------------------------------

    def summary_to_row(self, row: Any) -> Dict[str, Any]:
        """Normalize one manager summary row from SummaryGetAll."""
        scale = 10000.0
        return {
            "Symbol": str(get_field(row, "Symbol", default="")),
            "Digits": coerce_int(get_field(row, "Digits")),
            "PositionClients": coerce_int(get_field(row, "PositionClients")),
            "PositionCoverage": coerce_int(get_field(row, "PositionCoverage")),
            "VolumeBuyClients": coerce_float(get_field(row, "VolumeBuyClients")) / scale,
            "VolumeBuyCoverage": coerce_float(get_field(row, "VolumeBuyCoverage")) / scale,
            "VolumeSellClients": coerce_float(get_field(row, "VolumeSellClients")) / scale,
            "VolumeSellCoverage": coerce_float(get_field(row, "VolumeSellCoverage")) / scale,
            "PriceBuyClients": coerce_float(get_field(row, "PriceBuyClients")),
            "PriceBuyCoverage": coerce_float(get_field(row, "PriceBuyCoverage")),
            "PriceSellClients": coerce_float(get_field(row, "PriceSellClients")),
            "PriceSellCoverage": coerce_float(get_field(row, "PriceSellCoverage")),
            "VolumeNet": coerce_float(get_field(row, "VolumeNet")),
            "ProfitClients": coerce_float(get_field(row, "ProfitClients")),
            "ProfitCoverage": coerce_float(get_field(row, "ProfitCoverage")),
            "ProfitFullClients": coerce_float(get_field(row, "ProfitFullClients")),
            "ProfitFullCoverage": coerce_float(get_field(row, "ProfitFullCoverage")),
            "ProfitUncovered": coerce_float(get_field(row, "ProfitUncovered")),
            "ProfitUncoveredFull": coerce_float(get_field(row, "ProfitUncoveredFull")),
        }

    def exposure_to_row(self, row: Any) -> Dict[str, Any]:
        """Normalize one manager exposure row from ExposureGetAll."""
        return {
            "Symbol": str(get_field(row, "Symbol", default="")),
            "Digits": coerce_int(get_field(row, "Digits")),
            "VolumeClients": coerce_float(get_field(row, "VolumeClients")),
            "VolumeCoverage": coerce_float(get_field(row, "VolumeCoverage")),
            "VolumeNet": coerce_float(get_field(row, "VolumeNet")),
            "PriceRate": coerce_float(get_field(row, "PriceRate")),
        }

    def _normalize_user(self, user_obj: Any) -> Dict[str, Any]:
        """Normalize a raw MT5 user object into a flat dict for caching."""
        login = coerce_int(_value(user_obj, "Login", "login"))
        balance = coerce_float(_value(user_obj, "Balance", "balance"))
        credit = coerce_float(_value(user_obj, "Credit", "credit"))
        equity = coerce_float(_value(user_obj, "Equity", "equity"))
        margin = coerce_float(_value(user_obj, "Margin", "margin"))
        free_margin = coerce_float(
            _value(user_obj, "MarginFree", "FreeMargin", "Free Margin", "free_margin")
        )
        floating = coerce_float(_value(user_obj, "Floating", "floating", "Profit", "profit"))
        swap = coerce_float(_value(user_obj, "Storage", "Swap", "swap"))

        margin_level = coerce_float(_value(user_obj, "MarginLevel", "Margin Level %", "margin_level"))

        leverage = coerce_int(_value(user_obj, "Leverage", "leverage"))
        enabled = bool(_value(user_obj, "Enable", "Enabled", "Rights", default=0))
        # If Rights is the field, check USER_RIGHT_ENABLED
        rights_val = _value(user_obj, "Rights", default=None)
        if rights_val is not None:
            try:
                enabled = (int(rights_val) & USER_RIGHT_ENABLED) != 0
            except (TypeError, ValueError):
                pass

        return {
            "Login": login,
            "Name": str(_value(user_obj, "Name", "name", default="")),
            "Group": str(_value(user_obj, "Group", "group", default="")),
            "Country": str(_value(user_obj, "Country", "country", default="")),
            "Email": str(_value(user_obj, "Email", "email", default="")),
            "LastIP": str(_value(user_obj, "LastIP", "last_ip", "IP", "ip", default="")),
            "Agent": coerce_int(_value(user_obj, "Agent", "AgentAccount", "agent", default=0)),
            "Enabled": enabled,
            "Leverage": leverage,
            "Balance": balance,
            "Credit": credit,
            "Equity": equity,
            "Margin": margin,
            "Free Margin": free_margin,
            "Margin Level %": margin_level,
            "Floating PnL": floating,
            "Swap": swap,
        }

    def _normalize_position(self, pos: Any) -> Dict[str, Any]:
        """Normalize a raw MT5 position into a flat dict."""
        return {
            "Login": coerce_int(_value(pos, "Login", "login")),
            "Symbol": str(_value(pos, "Symbol", "symbol", default="")),
            "Action": coerce_int(_value(pos, "Action", "action")),
            "Volume": (coerce_float(_value(pos, "VolumeExt", "volume_ext")) / 100000000.0
                       if coerce_float(_value(pos, "VolumeExt", "volume_ext")) > 0
                       else coerce_float(_value(pos, "Volume", "volume")) / 10000.0),
            "PriceOpen": coerce_float(_value(pos, "PriceOpen", "priceOpen", "price_open")),
            "PriceCurrent": coerce_float(_value(pos, "PriceCurrent", "priceCurrent", "price_current")),
            "ContractSize": coerce_float(_value(pos, "ContractSize", "contract_size")),
            "RateProfit": coerce_float(_value(pos, "RateProfit", "rate_profit")),
            "Profit": coerce_float(_value(pos, "Profit", "profit")),
            "Swap": coerce_float(_value(pos, "Storage", "Swap", "swap")),
            "Ticket": coerce_int(_value(pos, "Position", "Ticket", "ticket")),
            "TimeCreate": coerce_int(
                _value(pos, "TimeCreate", "Time", "time_create", "time")
            ),
        }

    # -- background refresh loop ---------------------------------------------

    def _refresh_loop(self) -> None:
        # Let the web server finish startup before native SDK polling begins.
        # Some MT5Manager builds hold the Python GIL during these calls.
        if self.stop_event.wait(2.0):
            return

        # Publish the small configured-group catalogue before starting the
        # account-wide refresh.  A large installation can take minutes to
        # stage millions of users, but its operators still need to select a
        # focused group immediately.
        self._refresh_group_cache()
        self.last_refresh["groups"] = time.time()
        self._refresh_symbol_cache()
        self.last_refresh["symbols"] = time.time()

        while not self.stop_event.is_set():
            started = time.perf_counter()
            try:
                now = time.time()
                refresh_users = now - self.last_refresh["users"] >= USER_REFRESH_INTERVAL_SECONDS
                refresh_positions = now - self.last_refresh["positions"] >= POSITION_REFRESH_INTERVAL_SECONDS
                refresh_groups = now - self.last_refresh["groups"] >= GROUP_REFRESH_INTERVAL_SECONDS
                # Group enumeration is independent of the account snapshot.
                # Keeping it outside the atomic account refresh means a slow
                # user/position request cannot make the group selector stale.
                if refresh_groups:
                    self._refresh_group_cache()
                    self.last_refresh["groups"] = now

                if FULL_SNAPSHOT_ENABLED and (refresh_users or refresh_positions):
                    self._mark_refresh_phase("staging")
                    self._refresh_core_cache_atomically(
                        refresh_users=refresh_users,
                        refresh_positions=refresh_positions,
                        refresh_groups=False,
                    )
                    if refresh_users:
                        self.last_refresh["users"] = now
                    if refresh_positions:
                        self.last_refresh["positions"] = now
                    self.last_refresh["metrics"] = now
                    with self.monitoring_lock:
                        self.monitoring["refresh_cycles"] += 1

                # Optional/slow refreshes are intentionally after the publish
                # point. If a Manager SDK build stalls on one of these calls,
                # the dashboard still has the latest core account/position data.
                if now - self.last_refresh["watchdog"] >= WATCHDOG_INTERVAL_SECONDS:
                    self.call("UserTotal", required=False, default=0)
                    self.last_refresh["watchdog"] = now

                if now - self.last_refresh["panels"] >= PANELS_REFRESH_INTERVAL_SECONDS:
                    self._refresh_manager_panels_cache()
                    self.last_refresh["panels"] = now

                if now - self.last_refresh["symbols"] >= SYMBOL_REFRESH_INTERVAL_SECONDS:
                    self._refresh_symbol_cache()
                    self.last_refresh["symbols"] = now

            except Exception as exc:
                LOGGER.error("Refresh loop error: %s", exc, exc_info=True)
                with self.cache_lock:
                    self.cache["status"] = "error"
                    self.cache["errors"] = [str(exc)]
                with self.monitoring_lock:
                    self.monitoring["error_count"] += 1
                self._attempt_reconnect(str(exc))

            elapsed = time.perf_counter() - started
            with self.monitoring_lock:
                self.monitoring["last_cycle_ms"] = round(elapsed * 1000, 2)
            time.sleep(max(0.0, ENGINE_LOOP_INTERVAL_SECONDS - elapsed))

    def _tick_loop(self) -> None:
        """Keep quote refresh independent of slow account and panel snapshots."""
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self._refresh_tick_cache()
            except Exception as exc:
                LOGGER.warning("Tick refresh failed: %s", exc)
            self.stop_event.wait(max(0.0, TICK_REFRESH_INTERVAL_SECONDS - (time.monotonic() - started)))

    # -- refresh sub-tasks ---------------------------------------------------

    def _refresh_symbol_cache(self) -> None:
        try:
            raw_symbols = _as_list(_response_payload(
                self.call("SymbolGetArray", required=False, default=None)
            ))
            if not raw_symbols:
                total = coerce_int(self.call("SymbolTotal", required=False, default=0))
                raw_symbols = [
                    self.call("SymbolNext", index, required=False, default=None)
                    for index in range(total)
                ]
            symbols = {}
            for sym in _as_list(raw_symbols):
                try:
                    if sym is None:
                        continue
                    name = str(_value(sym, "Symbol", "Name", "name", default="")).strip()
                    if name:
                        symbols[name] = {
                            "Symbol": name,
                            "Description": str(_value(sym, "Description", "description", default="")),
                            "Path": str(_value(sym, "Path", "path", default="")),
                            "Digits": coerce_int(_value(sym, "Digits", "digits")),
                            "Contract Size": coerce_float(_value(sym, "ContractSize", "Contract Size", "contract_size")),
                            "Currency Profit": str(_value(sym, "CurrencyProfit", "currency_profit", default="") or ""),
                            "Tick Value": coerce_float(_value(sym, "TickValue", "Tick Value", "tick_value")),
                            "CalcMode": _value(sym, "CalcMode", default=None),
                            "Margin Initial": coerce_float(_value(sym, "MarginInitial", "Margin Initial", "margin_initial")),
                            "Margin Long": coerce_float(_value(sym, "MarginLong", "Margin Long")),
                            "Margin Short": coerce_float(_value(sym, "MarginShort", "Margin Short")),
                        }
                except Exception:
                    continue
            if symbols:
                with self.cache_lock:
                    self.cache["symbol_cache"] = symbols
            else:
                LOGGER.warning("Symbol refresh returned no rows; retaining the last published catalog")
        except Exception as exc:
            LOGGER.warning("Symbol cache refresh failed: %s", exc)

    def _fetch_group_catalog(self, *, strict: bool = False) -> List[str]:
        """Build a complete configured-group catalog without publishing it."""
        groups: set[str] = set()
        group_currencies: dict[str, str] = {}
        errors: list[Exception] = []
        indexed_incomplete = False
        indexed_total = 0
        try:
            total = coerce_int(_response_payload(self.call("GroupTotal", required=strict, default=0)), 0)
            indexed_total = total
            for index in range(total):
                try:
                    group = _response_payload(self.call("GroupNext", index, required=strict, default=None))
                    name = str(_value(group, "Group", "Name", "group", "name", default="") or "").strip()
                    if name:
                        groups.add(name)
                        currency = str(_value(group, "Currency", "currency", default="") or "").strip().upper()
                        if currency:
                            group_currencies[name] = currency
                except Exception as exc:
                    errors.append(exc)
                    if strict:
                        indexed_incomplete = True
                        raise
        except Exception as exc:
            errors.append(exc)

        if strict and indexed_incomplete:
            raise RuntimeError(f"Manager group enumeration stopped before completion: {errors[-1]}") from errors[-1]
        if strict and indexed_total > 0 and len(groups) < indexed_total:
            raise RuntimeError(
                f"Manager group enumeration returned {len(groups)} of {indexed_total} configured groups"
            )

        if not groups:
            for args in (("*",), ("",), tuple()):
                try:
                    raw = _response_payload(self.call("GroupRequestArray", *args, required=strict, default=[]))
                except Exception as exc:
                    errors.append(exc)
                    continue
                for group in _as_list(raw):
                    name = str(_value(group, "Group", "Name", "group", "name", default="") or "").strip()
                    if name:
                        groups.add(name)
                        currency = str(_value(group, "Currency", "currency", default="") or "").strip().upper()
                        if currency:
                            group_currencies[name] = currency
                if groups:
                    break

        if strict and not groups and errors:
            raise RuntimeError(f"Unable to fetch the complete Manager group catalog: {errors[-1]}") from errors[-1]
        if groups:
            with self.cache_lock:
                self.cache["group_currencies"] = {**self.cache.get("group_currencies", {}), **group_currencies}
        return sorted(groups, key=lambda value: (value.lower(), value))

    def _refresh_group_cache(self) -> None:
        """Enumerate configured groups and retain the last catalog on failure."""
        try:
            ordered = self._fetch_group_catalog(strict=True)
            if ordered:
                with self.cache_lock:
                    # Group pumps can lag behind the user request catalog.
                    # Do not temporarily drop valid memberships every time
                    # the smaller configured-group pump refreshes.
                    memberships = {str(row.get("Group", "")).strip()
                                   for row in self.cache.get("users", [])
                                   if str(row.get("Group", "")).strip()}
                    self.cache["groups"] = sorted(set(ordered) | memberships,
                                                  key=lambda value: (value.lower(), value))
        except Exception as exc:
            LOGGER.warning("Group catalog refresh failed: %s", exc)

    def _build_users_cache(self, *, strict: bool = False) -> tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]]]:
        raw_users = self.fetch_all_users(strict=strict)
        normalized = [self._normalize_user(user) for user in raw_users]
        users_by_login = {user["Login"]: user for user in normalized if user["Login"] > 0}
        account_rows = self.fetch_accounts_by_logins(list(users_by_login), strict=strict)
        if strict:
            missing_accounts = set(users_by_login).difference(account_rows)
            if missing_accounts:
                raise RuntimeError(
                    f"Manager account snapshot is incomplete: {len(missing_accounts)} of "
                    f"{len(users_by_login)} accounts have no authoritative metrics"
                )
            required_metrics = {
                "Balance": ("Balance", "balance"),
                "Equity": ("Equity", "equity"),
                "Margin": ("Margin", "margin"),
                "Free Margin": ("MarginFree", "FreeMargin", "free_margin"),
                "Floating PnL": ("Profit", "Floating", "profit", "floating"),
            }
            incomplete = 0
            for account in account_rows.values():
                if any(_value(account, *aliases, default=None) is None for aliases in required_metrics.values()):
                    incomplete += 1
            if incomplete:
                raise RuntimeError(
                    f"Manager account snapshot is incomplete: {incomplete} of {len(account_rows)} "
                    "accounts are missing authoritative financial fields"
                )
        for login, account in account_rows.items():
            if login not in users_by_login:
                continue
            metrics = self._normalize_user(account)
            for field in ("Balance", "Credit", "Equity", "Margin", "Free Margin", "Margin Level %", "Floating PnL", "Swap"):
                users_by_login[login][field] = metrics[field]
        return list(users_by_login.values()), users_by_login

    def _build_positions_cache(self, logins: List[int], *, strict: bool = False) -> tuple[List[Dict[str, Any]], Dict[int, List[Dict[str, Any]]]]:
        normalized = [self._normalize_position(position) for position in self.fetch_positions(logins, strict=strict)]
        positions_by_login: Dict[int, List[Dict[str, Any]]] = {}
        for position in normalized:
            positions_by_login.setdefault(position["Login"], []).append(position)
        return normalized, positions_by_login

    @staticmethod
    def _build_metrics(users: List[Dict[str, Any]], positions: List[Dict[str, Any]]) -> Dict[str, Any]:
        top_margin_accounts = [
            {
                "login": coerce_int(row.get("Login")),
                "name": str(row.get("Name", "")),
                "group": str(row.get("Group", "")),
                "margin": round(coerce_float(row.get("Margin")), 2),
                "margin_level": round(coerce_float(row.get("Margin Level %")), 2),
                "floating_pnl": round(coerce_float(row.get("Floating PnL")), 2),
            }
            for row in heapq.nlargest(5, users, key=lambda row: coerce_float(row.get("Margin")))
        ]
        return {
            "total_users": len(users),
            "enabled_users": sum(1 for user in users if user.get("Enabled")),
            "total_positions": len(positions),
            "total_balance": round(sum(coerce_float(user.get("Balance")) for user in users), 2),
            "total_equity": round(sum(coerce_float(user.get("Equity")) for user in users), 2),
            "total_margin": round(sum(coerce_float(user.get("Margin")) for user in users), 2),
            "total_free_margin": round(sum(coerce_float(user.get("Free Margin")) for user in users), 2),
            "total_floating": round(sum(
                coerce_float(user.get("Live Floating PnL", user.get("Floating PnL")))
                for user in users
            ), 2),
            "top_margin_accounts": top_margin_accounts,
        }

    @staticmethod
    def _build_native_metrics(users, positions):
        """Financial rows were validated once when read from the native stream."""
        top = heapq.nlargest(5, users, key=lambda row: row["Margin"])
        return {
            "total_users": len(users),
            "enabled_users": sum(1 for row in users if row.get("Enabled")),
            "total_positions": len(positions),
            "total_balance": round(sum(float(row["Balance"]) for row in users), 2),
            "total_equity": round(sum(float(row["Equity"]) for row in users), 2),
            "total_margin": round(sum(float(row["Margin"]) for row in users), 2),
            "total_free_margin": round(sum(float(row["Free Margin"]) for row in users), 2),
            "total_floating": round(sum(float(row["Live Floating PnL"]) for row in users), 2),
            "top_margin_accounts": [{
                "login": int(row["Login"]), "name": str(row.get("Name", "")), "group": str(row.get("Group", "")),
                "margin": round(float(row["Margin"]), 2), "margin_level": round(float(row["Margin Level %"]), 2),
                "floating_pnl": round(float(row["Floating PnL"]), 2),
            } for row in top],
        }

    @staticmethod
    def _merge_live_position_pnl(
        users: List[Dict[str, Any]],
        users_by_login: Dict[int, Dict[str, Any]],
        positions: List[Dict[str, Any]],
    ) -> None:
        """Overlay fast position P&L without replacing account-authoritative fields."""
        live_by_login: Dict[int, float] = {}
        for position in positions:
            login = coerce_int(position.get("Login"))
            live_by_login[login] = live_by_login.get(login, 0.0) + coerce_float(
                position.get("Profit")
            ) + coerce_float(position.get("Swap"))
        for user in users:
            login = coerce_int(user.get("Login"))
            live_value = round(live_by_login.get(login, 0.0), 2)
            user["Live Floating PnL"] = live_value
            if login in users_by_login:
                users_by_login[login]["Live Floating PnL"] = live_value

    def _refresh_core_cache_atomically(
        self,
        *,
        refresh_users: bool,
        refresh_positions: bool,
        refresh_groups: bool,
    ) -> None:
        """Stage one Manager generation and publish it only after every fetch succeeds."""
        with self.cache_lock:
            # Published generations are immutable except for the live-P&L overlay
            # below. Keep references to fields that this refresh will replace or
            # only read instead of cloning a potentially 190k-account snapshot.
            # A positions-only refresh gets one shallow user copy so the previous
            # generation remains stable while its P&L values are updated.
            if not refresh_users and refresh_positions:
                staged_users = [dict(user) for user in self.cache["users"]]
                staged_users_by_login = {
                    coerce_int(user.get("Login")): user
                    for user in staged_users
                    if coerce_int(user.get("Login")) > 0
                }
            else:
                staged_users = self.cache["users"]
                staged_users_by_login = self.cache["users_by_login"]
            staged = {
                "users": staged_users,
                "users_by_login": staged_users_by_login,
                "positions": self.cache["positions"],
                "positions_by_login": self.cache["positions_by_login"],
                "summary_rows": self.cache["summary_rows"],
                "exposure_rows": self.cache["exposure_rows"],
                "groups": self.cache["groups"],
            }

        if refresh_users:
            staged["users"], staged["users_by_login"] = self._build_users_cache(strict=True)
        staged_logins = list(staged["users_by_login"])
        if refresh_positions:
            staged["positions"], staged["positions_by_login"] = self._build_positions_cache(
                staged_logins, strict=True
            )
            if not refresh_users:
                self._merge_live_position_pnl(
                    staged["users"], staged["users_by_login"], staged["positions"]
                )
        if refresh_groups:
            staged["groups"] = self._fetch_group_catalog(strict=True)

        # A user's group is valid membership evidence even while the configured
        # group pump is propagating. Merge it into this same generation.
        staged["groups"] = sorted(
            set(staged["groups"]) | {
                str(user.get("Group", "")).strip()
                for user in staged["users"]
                if str(user.get("Group", "")).strip()
            },
            key=lambda value: (value.lower(), value),
        )
        staged["metrics"] = self._build_metrics(staged["users"], staged["positions"])
        # TimeServer is supplied by the Manager time pump.  It is the same
        # authoritative clock displayed to Manager administrators; do not
        # substitute the web-server clock when it is unavailable.
        try:
            raw_server_time = self.call("TimeServer", required=False, default=0)
        except Exception as exc:
            # A time-pump issue must not invalidate an otherwise complete
            # account/position generation. Keep the last verified MT5 time.
            LOGGER.debug("Manager TimeServer is unavailable: %s", exc)
            raw_server_time = 0
        server_time_msc = coerce_int(raw_server_time)
        if 0 < server_time_msc < 100_000_000_000:
            server_time_msc *= 1000
        published_at = time.time()
        with self.cache_lock:
            self.cache.update(staged)
            self.cache["metrics"] = staged["metrics"]
            self.cache["last_update"] = published_at
            if server_time_msc > 0:
                self.cache["server_time_msc"] = server_time_msc
                self.cache["server_time_checked_at"] = datetime.now(timezone.utc).isoformat()
            self.cache["status"] = "connected"
            self.cache["errors"] = []
            self.cache["refresh_phase"] = "published"
            self.all_logins = staged_logins

    def _refresh_users_cache(self) -> None:
        normalized, users_by_login = self._build_users_cache()
        with self.cache_lock:
            self.cache["users"] = normalized
            self.cache["users_by_login"] = users_by_login
            self.all_logins = list(users_by_login)

    def _refresh_positions_cache(self) -> None:
        normalized, positions_by_login = self._build_positions_cache(self.all_logins)
        with self.cache_lock:
            self.cache["positions"] = normalized
            self.cache["positions_by_login"] = positions_by_login

    def _refresh_manager_panels_cache(self) -> None:
        summary_rows = [self.summary_to_row(row) for row in self.fetch_summary_rows()]
        exposure_rows = [self.exposure_to_row(row) for row in self.fetch_exposure_rows()]
        with self.cache_lock:
            self.cache["summary_rows"] = summary_rows
            self.cache["exposure_rows"] = exposure_rows

    def _refresh_tick_cache(self) -> None:
        if not self.capabilities.get("tick_last", False):
            return
        with self.cache_lock:
            symbol_names = sorted(self.cache.get("symbol_cache", {}).keys())
            tick_cache = dict(self.cache.get("tick_cache", {}))
        if not symbol_names:
            return

        total = len(symbol_names)
        batch_size = min(TICK_REFRESH_BATCH_SIZE, total)
        start = self.tick_cursor % total
        with self.cache_lock:
            priority_names = [name for name in symbol_names if name in self.priority_ticks][:batch_size]
            self.priority_ticks.difference_update(priority_names)
        selected_names = list(priority_names)
        rotation_count = 0
        while len(selected_names) < batch_size and rotation_count < total:
            candidate = symbol_names[(start + rotation_count) % total]
            rotation_count += 1
            if candidate not in selected_names:
                selected_names.append(candidate)
        checked_at = datetime.now(timezone.utc).isoformat()

        for symbol_name in selected_names:
            try:
                tick = self.call("TickLast", symbol_name, required=False, default=None)
            except Exception:
                continue
            if not tick:
                previous = tick_cache.get(symbol_name, {})
                tick_cache[symbol_name] = {
                    **previous,
                    "available": False,
                    "checked_at": checked_at,
                }
                continue

            bid = coerce_float(get_field(tick, "Bid", "bid"))
            ask = coerce_float(get_field(tick, "Ask", "ask"))
            raw_time = _value(tick, "TimeMsc", "time_msc") or _value(
                tick, "Time", "time", "DateTime"
            )
            tick_datetime = to_datetime(raw_time) if raw_time else None
            tick_cache[symbol_name] = {
                "available": bid > 0.0 and ask > 0.0,
                "bid": bid,
                "ask": ask,
                "tick_time": tick_datetime.isoformat() if tick_datetime else None,
                "checked_at": checked_at,
            }

        self.tick_cursor = (start + rotation_count) % total
        with self.cache_lock:
            self.cache["tick_cache"] = tick_cache

    def prioritize_ticks(self, symbols: list[str]) -> None:
        """Move currently visible Market Watch symbols to the next refresh batch."""
        cleaned = {str(symbol).strip() for symbol in symbols if str(symbol).strip()}
        if not cleaned:
            return
        with self.cache_lock:
            self.priority_ticks.update(cleaned)

    def _refresh_metrics_cache(self) -> None:
        with self.cache_lock:
            users = self.cache.get("users", [])
            positions = self.cache.get("positions", [])

        with self.cache_lock:
            self.cache["metrics"] = self._build_metrics(users, positions)

    def _attempt_reconnect(self, reason: str) -> None:
        LOGGER.warning("Attempting reconnect due to: %s", reason)
        with self.monitoring_lock:
            self.monitoring["reconnects"] += 1
        if self.stop_event.wait(RECONNECT_BACKOFF_SECONDS):
            return

        # Publish the disconnected state before attempting a replacement. A
        # failed SDK Connect must never leave a stale handle looking healthy.
        with self.api_lock:
            previous_api = self.api
            self.api = None
        if previous_api is not None:
            try:
                previous_api.Disconnect()
            except Exception:
                pass

        try:
            api_handle = self._create_connected_api()
        except Exception as exc:
            LOGGER.error("Reconnect exception: %s", exc)
            with self.cache_lock:
                self.cache["status"] = "disconnected"
                self.cache["errors"] = [str(exc)]
                self.cache["refresh_phase"] = "reconnect_failed"
            return

        if self.stop_event.is_set():
            try:
                api_handle.Disconnect()
            except Exception:
                pass
            return

        with self.api_lock:
            self.api = api_handle
        self._probe_capabilities()
        self.last_refresh["users"] = 0.0
        self.last_refresh["positions"] = 0.0
        self.last_refresh["groups"] = 0.0
        with self.cache_lock:
            self.cache["status"] = "reconnecting"
            self.cache["errors"] = []
            self.cache["refresh_phase"] = "reconnected"
        LOGGER.info("Reconnected successfully.")


# ---------------------------------------------------------------------------
# MarketStructureEngine - live tick data
# ---------------------------------------------------------------------------
class MarketStructureEngine:
    """Provides live bid/ask quotes and spread calculations from MT5."""

    def __init__(self, connection: MT5ConnectionManager) -> None:
        self.connection = connection
        self._watch_cache: Dict[tuple, tuple[float, Dict[str, Any]]] = {}
        self._watch_cache_lock = threading.Lock()

    def clear_transient_cache(self) -> int:
        """Drop derived view results without touching the live Manager snapshot."""
        with self._watch_cache_lock:
            cleared = len(self._watch_cache)
            self._watch_cache.clear()
        return cleared

    def live_watch(self, search: str = "", limit: int = 250) -> Dict[str, Any]:
        """Return live bid/ask quotes and spread calculations."""
        max_rows = int(limit)
        if max_rows <= 0:
            raise ValueError("Limit must be greater than zero.")
        cache_key = (str(search or "").strip().lower(), max_rows)
        now = time.monotonic()
        with self._watch_cache_lock:
            cached = self._watch_cache.get(cache_key)
            if cached and now - cached[0] < 0.12:
                return copy.deepcopy(cached[1])

        with self.connection.cache_lock:
            # Cache generations are replaced atomically. Reading their immutable
            # references avoids copying the whole account catalogue each second.
            all_symbols_dict = self.connection.cache.get("symbol_cache", {})
            all_ticks_dict = self.connection.cache.get("tick_cache", {})
            top_margin_accounts = self.connection.cache.get("metrics", {}).get("top_margin_accounts", [])
            server_time_msc = coerce_int(self.connection.cache.get("server_time_msc"))
            server_time_checked_at = self.connection.cache.get("server_time_checked_at")

        all_symbols = list(all_symbols_dict.values())
        token = str(search or "").strip().lower()

        def matches(sym: dict) -> bool:
            if not token:
                return True
            haystack = f"{sym.get('Symbol', '')} {sym.get('Description', '')} {sym.get('Path', '')}".lower()
            return token in haystack

        filtered_symbols = [s for s in all_symbols if matches(s)]
        self.connection.prioritize_ticks([
            str(symbol.get("Symbol", "")) for symbol in filtered_symbols[:max_rows]
        ])

        rows: List[Dict[str, Any]] = []
        current_time = quote_reference_time(server_time_msc, server_time_checked_at)
        for symbol in filtered_symbols[:max_rows]:
            symbol_name = str(symbol.get("Symbol", "")).strip()
            if not symbol_name:
                continue
            tick = all_ticks_dict.get(symbol_name, {})
            bid = coerce_float(tick.get("bid"))
            ask = coerce_float(tick.get("ask"))
            tick_datetime = to_datetime(tick.get("tick_time"))
            tick_age_seconds = (
                max(0.0, (current_time - tick_datetime).total_seconds())
                if tick_datetime else None
            )
            quote_available = bool(tick.get("available")) and bid > 0 and ask > 0
            quote_live = (
                quote_available and tick_age_seconds is not None
                and tick_age_seconds <= TICK_LIVE_MAX_AGE_SECONDS
            )
            digits = coerce_int(symbol.get("Digits"), 5)
            spread_price = max(0.0, ask - bid) if bid > 0.0 and ask > 0.0 else 0.0
            spread_points = int(round(spread_price * (10 ** max(digits, 0)))) if spread_price > 0.0 else 0
            margin_initial = self._resolve_margin_initial(symbol, bid=bid, ask=ask)

            rows.append({
                "symbol": symbol_name,
                "description": str(symbol.get("Description", "")),
                "path": str(symbol.get("Path", "")),
                "digits": digits,
                "contract_size": coerce_float(symbol.get("Contract Size"), 1.0),
                "tick_value": coerce_float(symbol.get("Tick Value")),
                "margin_initial": margin_initial,
                "bid": round(bid, digits) if bid > 0.0 else 0.0,
                "ask": round(ask, digits) if ask > 0.0 else 0.0,
                "spread_price": round(spread_price, digits) if spread_price > 0.0 else 0.0,
                "spread_points": spread_points,
                "tick_time": tick.get("tick_time"),
                "tick_age_seconds": round(tick_age_seconds, 1) if tick_age_seconds is not None else None,
                "checked_at": tick.get("checked_at"),
                "available": quote_live,
                "quote_status": "live" if quote_live else "stale" if quote_available else "unavailable",
            })

        spread_points_list = [int(r["spread_points"]) for r in rows if r["available"]]
        last_tick = max((r["tick_time"] for r in rows if r.get("tick_time")), default=None)
        max_spread_pts = max(spread_points_list) if spread_points_list else 0
        max_spread_sym = next(
            (r["symbol"] for r in rows if r["available"] and int(r["spread_points"]) == max_spread_pts), ""
        )
        avg_spread_pts = (
            round(sum(spread_points_list) / len(spread_points_list), 2)
            if spread_points_list else 0.0
        )
        result = {
            "summary": {
                "total_symbols": len(all_symbols),
                "filtered_symbols": len(filtered_symbols),
                "live_quotes": sum(1 for row in rows if row.get("available")),
                "stale_quotes": sum(1 for row in rows if row.get("quote_status") == "stale"),
                "average_spread_points": avg_spread_pts,
                "max_spread_points": int(max_spread_pts),
                "max_spread_symbol": max_spread_sym,
                "last_tick": last_tick,
                "tick_source": "cached_ticklast" if self.connection.capabilities.get("tick_last", False) else "unavailable",
                "live_max_age_seconds": TICK_LIVE_MAX_AGE_SECONDS,
            },
            "rows": rows,
            "top_margin_accounts": top_margin_accounts,
            "server_time_msc": server_time_msc or None,
            "server_time_source": "manager_time_pump" if server_time_msc else "unavailable",
        }
        with self._watch_cache_lock:
            self._watch_cache[cache_key] = (time.monotonic(), result)
            if len(self._watch_cache) > 12:
                oldest = min(self._watch_cache, key=lambda key: self._watch_cache[key][0])
                self._watch_cache.pop(oldest, None)
        return copy.deepcopy(result)

    def _resolve_margin_initial(
        self, symbol: dict, bid: float = 0.0, ask: float = 0.0
    ) -> float:
        raw_margin_initial = coerce_float(symbol.get("Margin Initial"))
        if raw_margin_initial > 0.0:
            return raw_margin_initial

        margin_rate = max(
            coerce_float(symbol.get("Margin Long")),
            coerce_float(symbol.get("Margin Short")),
        )
        price = ask if ask > 0.0 else bid
        if margin_rate > 0.0 and price > 0.0:
            contract_size = max(1.0, coerce_float(symbol.get("Contract Size"), 1.0))
            if margin_rate <= 1.0:
                return round(contract_size * price * margin_rate, 2)
            return round((contract_size * price) / margin_rate, 2)

        return 0.0


# ---------------------------------------------------------------------------
# Global connection registry.  A ContextVar makes the selected manager safe for
# concurrent FastAPI requests while background workers can explicitly select a
# connection before doing manager-specific work.
# ---------------------------------------------------------------------------
_manager_instances: Dict[str, MT5ConnectionManager] = {}
_manager_lock = threading.Lock()
_market_engines: Dict[str, MarketStructureEngine] = {}
_default_manager_id: Optional[str] = None
_selected_manager_id: ContextVar[Optional[str]] = ContextVar("selected_mt5_manager_id", default=None)


def set_selected_manager(manager_id: Optional[str]):
    """Select a manager for the current request/thread and return its reset token."""
    return _selected_manager_id.set(manager_id)


def reset_selected_manager(token) -> None:
    _selected_manager_id.reset(token)


def get_manager(manager_id: Optional[str] = None) -> Optional[MT5ConnectionManager]:
    """Return the selected persistent manager, falling back to the default."""
    selected = manager_id if manager_id is not None else (_selected_manager_id.get() or _default_manager_id)
    return _manager_instances.get(selected) if selected else None


def get_managers() -> Dict[str, MT5ConnectionManager]:
    """Return a stable copy of every live configured manager."""
    with _manager_lock:
        return dict(_manager_instances)


def get_market_engine(manager_id: Optional[str] = None) -> Optional[MarketStructureEngine]:
    selected = manager_id if manager_id is not None else (_selected_manager_id.get() or _default_manager_id)
    return _market_engines.get(selected) if selected else None


def start_manager(server: str, login: int, password: str, manager_id: str = "default") -> MT5ConnectionManager:
    """Start or reuse the sole persistent Manager connection."""
    global _default_manager_id
    if manager_id != "default":
        raise ValueError("Only the .env system Manager connection is supported")
    with _manager_lock:
        for stale_id in list(_manager_instances):
            if stale_id != "default":
                stale = _manager_instances.pop(stale_id)
                _market_engines.pop(stale_id, None)
                stale.disconnect()
        existing = _manager_instances.get(manager_id)
        if (
            existing is not None
            and existing.is_connected
            and existing.server == server
            and existing.manager_login == login
            and existing.manager_password == password
        ):
            return existing
        if existing is not None:
            try:
                existing.disconnect()
            except Exception:
                pass
        mgr = MT5ConnectionManager(server, login, password)
        mgr.connect()
        _manager_instances[manager_id] = mgr
        _market_engines[manager_id] = MarketStructureEngine(mgr)
        _default_manager_id = "default"
        return mgr


def stop_manager(manager_id: Optional[str] = None) -> None:
    """Stop one named connection, or every connection when no id is supplied."""
    global _default_manager_id
    with _manager_lock:
        ids = [manager_id] if manager_id is not None else list(_manager_instances)
        for connection_id in ids:
            manager = _manager_instances.pop(connection_id, None)
            _market_engines.pop(connection_id, None)
            if manager is None:
                continue
            try:
                manager.disconnect()
            except Exception:
                pass
        if _default_manager_id not in _manager_instances:
            _default_manager_id = next(iter(_manager_instances), None)
