from __future__ import annotations

import base64
import asyncio
import copy
import fnmatch
import hashlib
import hmac
import ipaddress
import json
import math
import mimetypes
import os
import re
import secrets
import socket
import sqlite3
import tempfile
import logging
from logging.handlers import RotatingFileHandler
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor
import time as time_module
import urllib.request
from dataclasses import dataclass
from collections import Counter, defaultdict
from contextlib import asynccontextmanager, contextmanager
from functools import lru_cache
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from dotenv import dotenv_values, load_dotenv, set_key
import MT5Manager
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Response, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.utils import get_column_letter

from risk_automation import RiskAutomationService
from services import brokerage_pnl, markout
from services.markout_cache import MarkoutReportCache
from services.news_push import NewsPushService, SCHEMA as NEWS_PUSH_SCHEMA
from services.spread_alerts import SpreadAlertService, SpreadAlertSettings, fresh_spreads, SCHEMA as SPREAD_ALERT_SCHEMA
from services.live_scope import scope_live_payload
from services.live_money import currency_money_totals
from services.exposure_api import ExposureRequest, read_exposure
from services.native_observers import NativeMembershipCursor, online_account_identities
from services.risk_limits import limit_value as evaluate_limit_value
from calendar_feed import read_calendar_api

# This deployment is configured from the project-local .env file.  Explicitly
# override inherited Windows/service variables so a stale MT5 server, login, or
# password cannot silently connect the System Manager to another broker.
load_dotenv(override=True)

# Keep runtime output separate from application code and resolve it relative to
# this file so Windows services can start from any working directory.
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler(LOG_DIR / "api.log", maxBytes=5 * 1024 * 1024, backupCount=3),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("mt5_api")

_TELEGRAM_SENT: dict[str, float] = {}
_TELEGRAM_LOCK = threading.Lock()
_TELEGRAM_DELIVERY_LOCK = threading.Lock()
_TELEGRAM_LAST_DELIVERY = 0.0
_PERFORMANCE_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_PERFORMANCE_CACHE_LOCK = threading.RLock()
_STRESS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_STRESS_CACHE_LOCK = threading.RLock()
_LIVE_RISK_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_LIVE_RISK_CACHE_LOCK = threading.RLock()
_LIVE_RISK_KEY_LOCKS: dict[tuple[Any, ...], threading.Lock] = {}
_INTELLIGENCE_LOCK = threading.RLock()
_INTELLIGENCE_JOBS: dict[str, dict[str, Any]] = {}
_STRESS_REFRESHING: set[str] = set()
_PERFORMANCE_COMPUTE_LOCK = threading.Lock()
_PERFORMANCE_REFRESHING: set[tuple[Any, ...]] = set()
_BOOK_HISTORY_LOCK = threading.Lock()
_BOOK_HISTORY_REFRESHING: set[tuple[str, str, str]] = set()
PERFORMANCE_CACHE_SECONDS = max(float(os.getenv("PERFORMANCE_CACHE_SECONDS", "60")), 5.0)
_ANTIFRAUD_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_IN_APP_RISK_EVENT_VERSION = 0
_IN_APP_RISK_EVENT_LOCK = threading.Lock()
_ANTIFRAUD_CACHE_LOCK = threading.RLock()
_ANTIFRAUD_KEY_LOCKS: dict[str, threading.Lock] = {}
_ANTIFRAUD_REFRESHING: set[str] = set()
_ANTIFRAUD_REFRESH_CONTEXT = threading.local()
ANTIFRAUD_CACHE_SECONDS = max(float(os.getenv("ANTIFRAUD_CACHE_SECONDS", "5" if os.getenv("RMS_NATIVE_URL") else "60")), 2.0)
ANTIFRAUD_MAX_TICK_CHECKS = max(min(int(os.getenv("ANTIFRAUD_MAX_TICK_CHECKS", "3")), 40), 0)
RUNTIME_MAINTENANCE_INTERVAL_SECONDS = max(
    int(os.getenv("RUNTIME_MAINTENANCE_INTERVAL_SECONDS", "1800")), 60
)
APP_LOG_RETENTION_SECONDS = max(
    int(os.getenv("APP_LOG_RETENTION_HOURS", "24")) * 60 * 60, 60 * 60
)
MT5_JOURNAL_RETENTION_SECONDS = max(
    int(os.getenv("MT5_JOURNAL_RETENTION_DAYS", "7")) * 24 * 60 * 60,
    24 * 60 * 60,
)
AUDIT_RETENTION_DAYS = max(int(os.getenv("AUDIT_RETENTION_DAYS", "90")), 7)
BBOOK_SNAPSHOT_INTERVAL_SECONDS = max(
    int(os.getenv("BBOOK_SNAPSHOT_INTERVAL_SECONDS", "3600")), 60
)
CENT_ACCOUNT_DIVISOR = max(float(os.getenv("CENT_ACCOUNT_DIVISOR", "100")), 1.0)
_RUNTIME_MAINTENANCE_STOP = threading.Event()
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_LOGIN_ATTEMPTS_LOCK = threading.Lock()
_DATABASE_RESET_LOCK = threading.Lock()
_GOOGLE_SHEETS_SYNC_LOCK = threading.Lock()
_MANAGER_CONNECTION_ERROR = ""
_MANAGER_CONNECTION_ERRORS: dict[str, str] = {}
_MANAGER_CONFIG_PATH = Path(__file__).with_name(".env")
LOGIN_RATE_LIMIT_ATTEMPTS = max(int(os.getenv("LOGIN_RATE_LIMIT_ATTEMPTS", "10")), 3)
LOGIN_RATE_LIMIT_WINDOW_SECONDS = max(int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "900")), 60)
LOGIN_RATE_LIMIT_MAX_KEYS = max(int(os.getenv("LOGIN_RATE_LIMIT_MAX_KEYS", "10000")), 100)
MAX_REQUEST_BODY_BYTES = max(int(os.getenv("MAX_REQUEST_BODY_BYTES", str(25 * 1024 * 1024))), 1024 * 1024)
SMALL_BALANCE_ALERT_THRESHOLD = 500.0
OUTSIZED_PROFIT_MIN_USD = 200.0
OUTSIZED_PROFIT_MULTIPLE = 1.5
LIVE_RISK_CACHE_SECONDS = max(float(os.getenv("MT5_LIVE_RISK_CACHE_SECONDS", "20")), 1.0)
LIVE_RISK_IP_LOOKUP_LIMIT = max(int(os.getenv("MT5_LIVE_RISK_IP_LOOKUP_LIMIT", "100")), 0)
LOW_DEPOSIT_ALERT_THRESHOLD = max(float(os.getenv("LOW_DEPOSIT_ALERT_THRESHOLD", "500")), 0.0)
LOW_DEPOSIT_HIGH_PROFIT_MIN = max(float(os.getenv("LOW_DEPOSIT_HIGH_PROFIT_MIN", "200")), 0.0)
LOW_DEPOSIT_PROFIT_MULTIPLE = max(float(os.getenv("LOW_DEPOSIT_PROFIT_MULTIPLE", "1.5")), 0.0)
PROFIT_ALERT_PCT_THRESHOLD = float(os.getenv("PROFIT_ALERT_PCT_THRESHOLD", "30"))
DOUBLE_PROFIT_PCT_THRESHOLD = float(os.getenv("DOUBLE_PROFIT_PCT_THRESHOLD", "100"))
HIGH_LOT_ALERT_THRESHOLD = max(float(os.getenv("HIGH_LOT_ALERT_THRESHOLD", "1.0")), 0.01)
ACCOUNT_LOT_ALERT_THRESHOLD = max(float(os.getenv("ACCOUNT_LOT_ALERT_THRESHOLD", "5.0")), HIGH_LOT_ALERT_THRESHOLD)
ALERT_DEDUPE_RETENTION_SECONDS = max(int(os.getenv("ALERT_DEDUPE_RETENTION_SECONDS", "86400")), 900)
TELEGRAM_ALERTS_ENABLED = os.getenv("TELEGRAM_ALERTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
TELEGRAM_RISK_ALERTS_ENABLED = os.getenv("TELEGRAM_RISK_ALERTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
TELEGRAM_ANTIFRAUD_ALERTS_ENABLED = os.getenv("TELEGRAM_ANTIFRAUD_ALERTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
TELEGRAM_ROUTE_ENV = {
    "vps": "TELEGRAM_CHAT_ID_VPS",
    "scalpers": "TELEGRAM_CHAT_ID_SCALPERS",
    "win_rate": "TELEGRAM_CHAT_ID_WIN_RATE",
    "risk": "TELEGRAM_CHAT_ID_RISK",
    "antifraud": "TELEGRAM_CHAT_ID_ANTIFRAUD",
    "spread": "TELEGRAM_CHAT_ID_SPREAD",
}


def _live_risk_scope_cache_ttl(
    groups: list[str], payload: dict[str, Any] | None = None
) -> float:
    scoped_accounts = int((payload or {}).get("summary", {}).get("scoped_accounts", 0) or 0)
    if scoped_accounts >= 5_000 or len(groups) >= 200:
        target = 20.0
    elif scoped_accounts >= 1_000 or len(groups) >= 50:
        target = 10.0
    elif len(groups) >= 10:
        target = 5.0
    else:
        target = 2.0
    return min(LIVE_RISK_CACHE_SECONDS, target)


def _telegram_route_for_message(message: str) -> str:
    text = str(message).upper()
    if "VPS" in text:
        return "vps"
    if "SCALP" in text or "HFT" in text:
        return "scalpers"
    if "WIN RATE" in text or "WIN_RATE" in text:
        return "win_rate"
    if "ANTI-FRAUD" in text or "ANTIFRAUD" in text:
        return "antifraud"
    return "risk"


def send_telegram_notification(message: str, route: str | None = None) -> tuple[bool, str]:
    global _TELEGRAM_LAST_DELIVERY
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    selected_route = route or _telegram_route_for_message(message)
    route_env = TELEGRAM_ROUTE_ENV.get(selected_route, "")
    chat_id = os.getenv(route_env, "").strip() if route_env else ""
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False, "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env."
    payload = json.dumps({"chat_id": chat_id, "text": str(message)[:4000]}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        # Telegram rejects bursts. Serialize delivery across all report/risk
        # endpoints and leave a small gap without blocking request handlers.
        with _TELEGRAM_DELIVERY_LOCK:
            wait_seconds = max(0.0, 1.1 - (time_module.monotonic() - _TELEGRAM_LAST_DELIVERY))
            if wait_seconds:
                time_module.sleep(wait_seconds)
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8"))
            _TELEGRAM_LAST_DELIVERY = time_module.monotonic()
        return bool(body.get("ok")), "sent" if body.get("ok") else str(body)
    except Exception as exc:
        logger.warning("Telegram notification failed: %s", exc)
        return False, str(exc)


def _send_telegram_messages(messages: list[str]) -> None:
    # One entry is one account or position alert. Do not combine entries:
    # dealers need an independently actionable Telegram message per account.
    individual_messages = [
        str(message)[:3900]
        for message in messages[:20]
        if str(message).strip()
    ]

    def deliver() -> None:
        for message in individual_messages:
            send_telegram_notification(message)

    if individual_messages:
        threading.Thread(target=deliver, daemon=True).start()


def _telegram_digest_key(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_line_message(title: str, lines: list[str]) -> str:
    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    return "\n".join([title, *cleaned])[:4000]


def _profit_pct(balance: Any, profit: Any) -> float:
    base = _to_float(balance, 0.0)
    pnl = _to_float(profit, 0.0)
    if base <= 0:
        return 0.0
    return round((pnl / base) * 100.0, 2)


def _profit_alert_style(balance: Any, profit: Any) -> tuple[str, str]:
    pct = _profit_pct(balance, profit)
    if pct >= DOUBLE_PROFIT_PCT_THRESHOLD:
        return "🚀 DOUBLE+ PROFIT ALERT", f"Profit vs Balance: {pct}%"
    if pct >= PROFIT_ALERT_PCT_THRESHOLD:
        return "💸 HIGH PROFIT ALERT", f"Profit vs Balance: {pct}%"
    return "MT5 Live RMS Alert", f"Profit vs Balance: {pct}%"


def notify_new_antifraud_events(events: list[dict[str, Any]], client_summaries: list[dict[str, Any]] | None = None) -> None:
    if not TELEGRAM_ALERTS_ENABLED or not TELEGRAM_ANTIFRAUD_ALERTS_ENABLED:
        return
    now = datetime.now(timezone.utc).timestamp()
    messages: list[str] = []
    client_summaries = client_summaries or []
    with _TELEGRAM_LOCK:
        expired = [key for key, sent_at in _TELEGRAM_SENT.items() if now - sent_at > 86400]
        for key in expired:
            _TELEGRAM_SENT.pop(key, None)

        for summary in client_summaries:
            severity = str(summary.get("severity") or "")
            if severity not in {"critical", "high"}:
                continue
            digest = _telegram_digest_key(
                "antifraud-client",
                summary.get("login"),
                summary.get("severity"),
                summary.get("total_deals"),
                summary.get("total_profit"),
                summary.get("total_events"),
                summary.get("gross_open_lots"),
            )
            if digest in _TELEGRAM_SENT:
                continue
            _TELEGRAM_SENT[digest] = now
            recent_evidence = "\n".join(f"- {item}" for item in (summary.get("recent_evidence") or [])[:3]) or "- No recent evidence text"
            title, pct_line = _profit_alert_style(summary.get('balance', 0.0), summary.get('total_profit', 0.0))
            if any(signal in (summary.get("signals") or []) for signal in (
                "low_deposit_high_profit", "lifetime_deposit_high_profit", "starting_capital_high_profit"
            )):
                title = "LOW DEPOSIT / HIGH PROFIT ALERT"
            elif summary.get("vps_status") == "detected":
                title = "🛰️ VPS ANTI-FRAUD ALERT"
            messages.append(_build_line_message(title, [
                f"Account: {summary.get('name') or 'Unknown'}",
                f"Login: #{summary.get('login')}",
                f"Group: {summary.get('group') or '-'}",
                f"Severity: {severity.upper()}",
                f"Toxicity: {summary.get('toxicity_score', 0)}%",
                f"IP: {summary.get('last_ip') or 'Unavailable'}",
                f"Location: {summary.get('location') or 'Unavailable'}",
                f"Provider: {(str(summary.get('provider') or 'Unavailable') + (' ' + str(summary.get('asn') or '') if summary.get('asn') else '')).strip()}",
                f"VPS Status: {summary.get('vps_status') or 'unknown'}",
                f"VPS Confidence: {summary.get('vps_confidence') or 0}%",
                f"Balance: {_format_money_text(summary.get('balance', 0.0))}",
                f"Lookback Deposit: {_format_money_text(summary.get('lookback_deposit', summary.get('total_deposit', 0.0)))}",
                f"Lifetime Deposits: {_format_money_text(summary.get('lifetime_deposits', 0.0))}",
                f"Starting Capital: {_format_money_text(summary.get('starting_capital', 0.0))}",
                f"Total Deals: {summary.get('total_deals', 0)}",
                f"Total Profit: {_format_money_text(summary.get('total_profit', 0.0))}",
                pct_line,
                f"Scalp Deals: {summary.get('scalp_deals', 0)}",
                f"Scalp Profit: {_format_money_text(summary.get('scalp_profit', 0.0))}",
                f"Latency Deals: {summary.get('latency_deals', 0)}",
                f"Latency Profit: {_format_money_text(summary.get('latency_profit', 0.0))}",
                f"Sharp Deals: {summary.get('sharp_deals', 0)}",
                f"Sharp Profit: {_format_money_text(summary.get('sharp_profit', 0.0))}",
                f"Large Orders: {summary.get('large_order_count', 0)}",
                f"Large Order Lots: {round(float(summary.get('large_order_lots', 0.0) or 0.0), 2)}",
                f"Open Positions: {summary.get('open_positions', 0)}",
                f"Gross Open Lots: {round(float(summary.get('gross_open_lots', 0.0) or 0.0), 2)}",
                f"Floating P&L: {_format_money_text(summary.get('floating_pnl', 0.0))}",
                f"Equity: {_format_money_text(summary.get('equity', 0.0))}",
                f"Signals: {', '.join(summary.get('signals') or []) or 'None'}",
                "Recent Evidence:",
                recent_evidence,
            ]))

        for event in events:
            if event.get("detector") != "deposit_alert":
                continue
            digest = _telegram_digest_key("antifraud-deposit", event.get("login"), event.get("deal_id"))
            if digest in _TELEGRAM_SENT:
                continue
            _TELEGRAM_SENT[digest] = now
            messages.append("\n".join([
                "MT5 Deposit Alert",
                f"Account: {event.get('name') or 'Unknown'} (#{event.get('login')})",
                f"Group: {event.get('group') or '-'}",
                f"Deal: #{event.get('deal_id')}",
                f"Evidence: {event.get('evidence')}",
                f"Data quality: {event.get('data_quality') or 'PARTIAL'}",
            ]))

        if not messages:
            important = [event for event in events if event.get("severity") in {"critical", "high"}]
            for event in important[:20]:
                digest = _telegram_digest_key("antifraud-event", event.get("detector"), event.get("login"), event.get("position_id"), event.get("evidence"))
                if digest in _TELEGRAM_SENT:
                    continue
                _TELEGRAM_SENT[digest] = now
                messages.append(
                    "\n".join([
                        "MT5 Anti-Fraud Alert",
                        f"Account: {event.get('name') or 'Unknown'} (#{event.get('login')})",
                        f"Group: {event.get('group') or '-'}",
                        f"Severity: {str(event.get('severity', '')).upper()}",
                        f"Detector: {event.get('detector')}",
                        f"Symbol: {event.get('symbol') or '-'}",
                        f"Evidence: {event.get('evidence')}",
                    ])
                )
    if messages:
        _send_telegram_messages(messages)


def notify_live_risk_events(accounts: list[dict[str, Any]], positions: list[dict[str, Any]]) -> None:
    if not TELEGRAM_ALERTS_ENABLED or not TELEGRAM_RISK_ALERTS_ENABLED:
        return
    now = datetime.now(timezone.utc).timestamp()
    messages: list[str] = []
    large_positions = [
        row for row in positions
        if float(row.get("volume") or 0) >= HIGH_LOT_ALERT_THRESHOLD
    ]
    risky_accounts = [
        row for row in accounts
        if row.get("severity") in {"critical", "high"}
        or row.get("vps_status") == "detected"
        or float(row.get("gross_lots") or 0) >= ACCOUNT_LOT_ALERT_THRESHOLD
    ]

    with _TELEGRAM_LOCK:
        expired = [
            key for key, sent_at in _TELEGRAM_SENT.items()
            if now - sent_at > ALERT_DEDUPE_RETENTION_SECONDS
        ]
        for key in expired:
            _TELEGRAM_SENT.pop(key, None)

        for row in risky_accounts[:15]:
            signal_text = ", ".join((row.get("signals") or [])[:4]) or "Live risk threshold breached"
            # Account alerts describe state transitions. P&L is deliberately not
            # part of the key because every tick would otherwise create a message.
            digest = _telegram_digest_key(
                "live-account", row.get("login"), row.get("severity"),
                row.get("vps_status"), signal_text,
            )
            if digest in _TELEGRAM_SENT:
                continue
            _TELEGRAM_SENT[digest] = now
            title, pct_line = _profit_alert_style(row.get('balance', 0.0), row.get('floating_pnl', 0.0))
            if row.get("vps_status") == "detected":
                title = "🛰️ VPS TRADE ALERT"
            messages.append(_build_line_message(title, [
                f"Account: {row.get('name') or 'Unknown'}",
                f"Login: #{row.get('login')}",
                f"Group: {row.get('group') or '-'}",
                f"Severity: {str(row.get('severity', 'watch')).upper()}",
                f"Balance: {_format_money_text(row.get('balance', 0.0))}",
                f"Floating P&L: {_format_money_text(row.get('floating_pnl', 0.0))}",
                f"Equity: {_format_money_text(row.get('equity', 0.0))}",
                f"Margin Level: {round(float(row.get('margin_level') or 0.0), 2)}%",
                f"Open Positions: {row.get('positions', 0)}",
                f"Gross Lots: {round(float(row.get('gross_lots') or 0.0), 2)}",
                f"Profit Multiple vs Balance: {round(float(row.get('profit_multiple') or 0.0), 2)}x",
                pct_line,
                f"IP: {row.get('last_ip') or 'Unavailable'}",
                f"Location: {row.get('location') or 'Unavailable'}",
                f"Provider: {(str(row.get('provider') or 'Unavailable') + (' ' + str(row.get('asn') or '') if row.get('asn') else '')).strip()}",
                f"VPS Status: {row.get('vps_status') or 'unknown'}",
                f"VPS Confidence: {row.get('vps_confidence') or 0}%",
                f"Signals: {signal_text}",
            ]))

        for row in large_positions[:15]:
            volume = float(row.get("volume") or 0.0)
            # Notify immediately for a new ticket, then only when its size crosses
            # another configured threshold step. Floating P&L never retriggers it.
            volume_step = int(volume / HIGH_LOT_ALERT_THRESHOLD)
            digest = _telegram_digest_key(
                "large-position", row.get("ticket"), row.get("login"), volume_step,
            )
            if digest in _TELEGRAM_SENT:
                continue
            _TELEGRAM_SENT[digest] = now
            messages.append(_build_line_message("🐋 HIGH LOT ALERT", [
                f"Login: #{row.get('login')}",
                f"Ticket: {row.get('ticket') or '-'}",
                f"Symbol: {row.get('symbol') or '-'}",
                f"Side: {row.get('action') or '-'}",
                f"Lots: {round(volume, 2)}",
                f"Floating P&L: {_format_money_text(row.get('profit', 0.0))}",
            ]))

    if messages:
        _send_telegram_messages(messages)

DB_PATH = os.getenv("RMS_TEST_DB_PATH") or os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db"))
FRONTEND_DIST = Path(__file__).with_name("frontend") / "dist"
_FRONTEND_ASSET_CACHE: dict[str, tuple[bytes, str]] = {}


def _warm_frontend_asset_cache() -> None:
    """Keep hashed production assets in RAM to avoid synced-folder disk latency."""
    _FRONTEND_ASSET_CACHE.clear()
    assets_dir = FRONTEND_DIST / "assets"
    if not assets_dir.is_dir():
        return
    for asset in assets_dir.rglob("*"):
        if not asset.is_file() or asset.stat().st_size > 5 * 1024 * 1024:
            continue
        key = asset.relative_to(FRONTEND_DIST).as_posix()
        media_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
        _FRONTEND_ASSET_CACHE[key] = (asset.read_bytes(), media_type)


_warm_frontend_asset_cache()


def configured_manager_configs() -> dict[str, dict[str, Any]]:
    """Read the one system Manager from the project .env file."""
    values = dotenv_values(_MANAGER_CONFIG_PATH)
    server = str(values.get("MT5_MANAGER_SERVER") or "").strip()
    login_text = str(values.get("MT5_MANAGER_LOGIN") or "").strip()
    password = str(values.get("MT5_MANAGER_PASSWORD") or "")
    if not server or not login_text or not password:
        raise RuntimeError("Set MT5_MANAGER_SERVER, MT5_MANAGER_LOGIN, and MT5_MANAGER_PASSWORD in .env.")
    try:
        login = int(login_text)
    except ValueError as exc:
        raise RuntimeError("MT5_MANAGER_LOGIN must be an integer.") from exc
    return {"default": {"id": "default", "name": "Manager", "server": server, "login": login, "password": password}}


def configured_manager_credentials(manager_id: str | None = None) -> tuple[str, int, str]:
    """Return the sole configured Manager credentials."""
    configs = configured_manager_configs()
    if manager_id not in (None, "default"):
        raise RuntimeError(f"Unknown MT5 manager: {manager_id}")
    config = configs["default"]
    return config["server"], config["login"], config["password"]


def get_configured_manager(manager_id: str | None = None):
    """Return the sole persistent Manager, starting it if necessary."""
    import mt5_connection

    configs = configured_manager_configs()
    selected = manager_id or "default"
    if selected != "default":
        raise RuntimeError(f"Unknown MT5 manager: {selected}")
    server, login, password = configured_manager_credentials(selected)
    manager = mt5_connection.get_manager(selected)
    if manager and manager.is_connected:
        if manager.server != server or manager.manager_login != login or manager.manager_password != password:
            return mt5_connection.start_manager(server, login, password, selected)
        return manager
    if os.getenv("MT5_MANAGER_AUTOCONNECT", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("MT5 manager is offline because auto-connect is disabled for this process.")
    if not _manager_server_reachable(server, timeout=2.0):
        raise RuntimeError(f"MT5 manager server {server} is unreachable.")
    return mt5_connection.start_manager(server, login, password, selected)


def _manager_server_reachable(server: str, timeout: float = 3.0) -> bool:
    host, separator, port_text = server.rpartition(":")
    if not separator or not host:
        host, port_text = server, "443"
    try:
        with socket.create_connection((host.strip("[]"), int(port_text)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _manager_connection_error_message(exc: Exception | str) -> str:
    """Return a safe, actionable Manager connection error for the dashboard."""
    detail = str(exc)
    if "MT_RET_AUTH_ACCOUNT_INVALID" in detail:
        return (
            "MT5 rejected the configured Manager credentials. Verify the server, "
            "Manager login, password, and that the account is enabled for Manager API access."
        )
    if "MT_RET_ERR_NETWORK" in detail:
        return "MT5 Manager network connection failed. The server will retry automatically."
    return detail


def _manager_connection_supervisor() -> None:
    """Keep the one .env Manager connected and reconcile edits within five seconds."""
    global _MANAGER_CONNECTION_ERROR
    while not _RUNTIME_MAINTENANCE_STOP.is_set():
        try:
            configs = configured_manager_configs()
        except Exception as exc:
            logger.error("MT5 manager configuration is invalid; retrying in 5s: %s", exc)
            if _RUNTIME_MAINTENANCE_STOP.wait(5):
                return
            continue
        import mt5_connection
        for manager_id in set(mt5_connection.get_managers()) - set(configs):
            mt5_connection.stop_manager(manager_id)
            _MANAGER_CONNECTION_ERRORS.pop(manager_id, None)
        for manager_id, config in configs.items():
            try:
                existing_manager = mt5_connection.get_manager(manager_id)
                was_connected = bool(existing_manager and existing_manager.is_connected)
                if not was_connected:
                    if not _manager_server_reachable(config["server"]):
                        raise RuntimeError(f"MT5 manager server {config['server']} is unreachable.")
                manager = get_configured_manager(manager_id)
                _MANAGER_CONNECTION_ERRORS.pop(manager_id, None)
                if not was_connected:
                    logger.info("Persistent MT5 manager connected: id=%s server=%s login=%s", manager_id, manager.server, manager.manager_login)
            except Exception as exc:
                _MANAGER_CONNECTION_ERRORS[manager_id] = _manager_connection_error_message(exc)
                logger.warning("MT5 manager %s connection failed; will retry: %s", manager_id, exc)
        _MANAGER_CONNECTION_ERROR = next(iter(_MANAGER_CONNECTION_ERRORS.values()), "")
        if _RUNTIME_MAINTENANCE_STOP.wait(5):
            return


REPORT_COLUMNS = [
    "Login",
    "Name",
    "Prev Balance",
    "Deposit",
    "Closed P/L",
    "Balance",
    "Credit",
    "Floating",
    "Equity",
    "Margin",
    "Free Margin",
]

SUMMARY_COLUMNS = [
    "Login",
    "Name",
    "Deposit",
    "Withdraw",
    "In/Out",
    "Credit",
    "Additional",
    "Volume",
    "Commission",
    "Fee",
    "Swap",
    "Profit",
]

DEAL_TABLE_COLUMNS = [
    "Login",
    "Name",
    "Group",
    "Symbol",
    "Volume",
    "Time",
    "Profit",
    "Commission",
    "Fee",
    "Swap",
    "PnL",
]

ANALYTICS_PERIOD_OPTIONS = [
    "Today",
    "Yesterday",
    "This Week",
    "Last Week",
    "This Month",
    "Last Month",
    "This Quarter",
    "Last Quarter",
]


PASSWORD_HASH_ITERATIONS = max(int(os.getenv("PASSWORD_HASH_ITERATIONS", "600000")), 100_000)
SESSION_TTL_SECONDS = max(int(os.getenv("SESSION_TTL_SECONDS", "2592000")), 300)
SESSION_COOKIE_NAME = "mt5_session"
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
_session_secret_text = os.getenv("APP_SESSION_SECRET", "").strip()
_session_salt_text = os.getenv("APP_SECRET_SALT", "").strip()
_SESSION_SECRET = (
    hashlib.pbkdf2_hmac(
        "sha256",
        _session_secret_text.encode(),
        _session_salt_text.encode() if _session_salt_text else b"mt5-session-signing-v1",
        100_000,
    )
    if _session_secret_text
    else secrets.token_bytes(32)
)
if not _session_secret_text:
    logger.warning("APP_SESSION_SECRET is unset; sessions will be invalidated when this process restarts.")
elif not _session_salt_text:
    logger.warning("APP_SECRET_SALT is unset; configure a separate stable session-signing salt.")


def _hash_pw(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_HASH_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_pw(password: str, stored: str) -> bool:
    try:
        if stored.startswith("pbkdf2_sha256$"):
            _, iterations, salt_hex, expected_hex = stored.split("$", 3)
            actual = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
            )
            return hmac.compare_digest(actual, bytes.fromhex(expected_hex))
        legacy = hashlib.sha256(password.encode()).hexdigest()
        return hmac.compare_digest(legacy, stored)
    except (TypeError, ValueError):
        return False


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _create_session_token(user: dict[str, Any]) -> str:
    now = int(time_module.time())
    payload = {
        "sub": int(user["id"]),
        "username": str(user["username"]),
        "is_admin": bool(user.get("is_admin")),
        "iat": now,
        "exp": now + SESSION_TTL_SECONDS,
        "nonce": secrets.token_hex(8),
    }
    encoded = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64url_encode(hmac.new(_SESSION_SECRET, encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="strict",
        path="/",
    )


def _login_rate_limit_key(request: Request, username: str) -> str:
    address = request.client.host if request.client else "unknown"
    return f"{address}:{username.strip().lower()}"


def _enforce_login_rate_limit(request: Request, username: str) -> str:
    key = _login_rate_limit_key(request, username)
    cutoff = time_module.time() - LOGIN_RATE_LIMIT_WINDOW_SECONDS
    with _LOGIN_ATTEMPTS_LOCK:
        stale_keys = [
            candidate for candidate, values in _LOGIN_ATTEMPTS.items()
            if not values or max(values) < cutoff
        ]
        for candidate in stale_keys:
            _LOGIN_ATTEMPTS.pop(candidate, None)
        if key not in _LOGIN_ATTEMPTS and len(_LOGIN_ATTEMPTS) >= LOGIN_RATE_LIMIT_MAX_KEYS:
            oldest_key = min(
                _LOGIN_ATTEMPTS,
                key=lambda candidate: max(_LOGIN_ATTEMPTS[candidate], default=0.0),
            )
            _LOGIN_ATTEMPTS.pop(oldest_key, None)
        attempts = [timestamp for timestamp in _LOGIN_ATTEMPTS.get(key, []) if timestamp >= cutoff]
        _LOGIN_ATTEMPTS[key] = attempts
        if len(attempts) >= LOGIN_RATE_LIMIT_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts. Try again later.",
                headers={"Retry-After": str(LOGIN_RATE_LIMIT_WINDOW_SECONDS)},
            )
    return key


def _record_login_result(key: str, succeeded: bool) -> None:
    with _LOGIN_ATTEMPTS_LOCK:
        if succeeded:
            _LOGIN_ATTEMPTS.pop(key, None)
        else:
            _LOGIN_ATTEMPTS.setdefault(key, []).append(time_module.time())


def _verify_session_token(token: str) -> dict[str, Any] | None:
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected_signature = _b64url_encode(
            hmac.new(_SESSION_SECRET, encoded.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return None
        payload = json.loads(_b64url_decode(encoded))
        if int(payload.get("exp", 0)) <= int(time_module.time()):
            return None
        user_id = int(payload["sub"])
        with _conn() as con:
            row = con.execute(
                "SELECT id, username, is_admin, must_change_pw FROM app_users WHERE id=?",
                (user_id,),
            ).fetchone()
        if not row or str(row["username"]) != str(payload.get("username", "")):
            return None
        return dict(row)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


@contextmanager
def _conn():
    # Every worker/thread owns its connection. A real busy timeout plus WAL
    # prevents transient "database is locked" failures during alert bursts.
    con = sqlite3.connect(DB_PATH, timeout=15.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 15000")
    con.execute("PRAGMA synchronous = NORMAL")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT    NOT NULL UNIQUE,
                password_hash   TEXT    NOT NULL,
                is_admin        INTEGER NOT NULL DEFAULT 0,
                must_change_pw  INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL DEFAULT 'system',
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT '',
                entity_id TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS investigation_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_login INTEGER NOT NULL,
                title TEXT NOT NULL,
                assigned_to TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT NOT NULL DEFAULT 'medium',
                summary TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                created_by TEXT NOT NULL DEFAULT 'system',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS case_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL REFERENCES investigation_cases(id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'system',
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS exposure_limits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type TEXT NOT NULL,
                scope_value TEXT NOT NULL DEFAULT '*',
                metric TEXT NOT NULL,
                threshold REAL NOT NULL,
                warning_pct REAL NOT NULL DEFAULT 80,
                escalation_level TEXT NOT NULL DEFAULT 'warning',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_by TEXT NOT NULL DEFAULT 'system',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS alert_acknowledgements (
                alert_key TEXT PRIMARY KEY,
                actor TEXT NOT NULL,
                acknowledged_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS performance_snapshots (
                cache_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                generated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS antifraud_snapshots (
                cache_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                generated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS antifraud_monitor_scopes (
                cache_key TEXT PRIMARY KEY,
                request_json TEXT NOT NULL,
                last_scan_at TEXT,
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS news_trader_incidents (
                login INTEGER NOT NULL, position_id TEXT NOT NULL,
                calendar_event_ts INTEGER NOT NULL, symbol TEXT NOT NULL,
                first_seen_at TEXT NOT NULL, evidence_json TEXT NOT NULL,
                PRIMARY KEY(login,position_id,calendar_event_ts)
            );
            CREATE TABLE IF NOT EXISTS risk_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                detector TEXT NOT NULL,
                login INTEGER,
                position_id INTEGER,
                symbol TEXT,
                account_group TEXT,
                severity TEXT NOT NULL,
                confidence TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'NEW',
                source TEXT NOT NULL,
                source_generation TEXT,
                detected_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                thresholds_json TEXT NOT NULL,
                actual_values_json TEXT NOT NULL,
                data_quality TEXT NOT NULL,
                acknowledged_by TEXT,
                acknowledged_at TEXT,
                resolved_by TEXT,
                resolved_at TEXT,
                assigned_to TEXT,
                case_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_risk_events_seen ON risk_events(last_seen_at DESC);
            CREATE INDEX IF NOT EXISTS idx_risk_events_first ON risk_events(first_seen_at DESC);
            CREATE INDEX IF NOT EXISTS idx_risk_events_login_seen ON risk_events(login,last_seen_at DESC);
            CREATE INDEX IF NOT EXISTS idx_risk_events_status_seen ON risk_events(status,last_seen_at DESC);
            CREATE INDEX IF NOT EXISTS idx_risk_events_type_first_seen ON risk_events(event_type,first_seen_at DESC);
            CREATE TABLE IF NOT EXISTS risk_event_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS google_sheets_toxic_history (
                login INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL DEFAULT '',
                account_group TEXT NOT NULL DEFAULT '',
                last_risk_score REAL NOT NULL DEFAULT 0,
                last_severity TEXT NOT NULL DEFAULT 'normal',
                toxic_reason TEXT NOT NULL DEFAULT '',
                observations INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL DEFAULT (datetime('now')),
                last_seen TEXT NOT NULL DEFAULT (datetime('now')),
                last_snapshot_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS notification_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                destination TEXT NOT NULL DEFAULT '',
                min_severity TEXT NOT NULL DEFAULT 'warning',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS daily_reconciliations (
                report_date TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                warnings_json TEXT NOT NULL DEFAULT '[]',
                source_updated_at REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS book_group_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                a_book_json TEXT NOT NULL DEFAULT '[]',
                b_book_json TEXT NOT NULL DEFAULT '[]',
                cent_json TEXT NOT NULL DEFAULT '[]',
                hybrid_json TEXT NOT NULL DEFAULT '[]',
                updated_by TEXT NOT NULL DEFAULT 'system',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS book_membership_state (
                login INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL DEFAULT '',
                account_group TEXT NOT NULL,
                book TEXT NOT NULL DEFAULT 'unassigned',
                equity REAL NOT NULL,
                floating_pnl REAL NOT NULL DEFAULT 0,
                source_updated_at REAL NOT NULL,
                observed_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS book_shift_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manager_login INTEGER NOT NULL,
                login INTEGER NOT NULL,
                client_name TEXT NOT NULL DEFAULT '',
                from_group TEXT NOT NULL,
                to_group TEXT NOT NULL,
                from_book TEXT NOT NULL,
                to_book TEXT NOT NULL,
                equity_at_shift REAL NOT NULL,
                floating_pnl_at_shift REAL NOT NULL DEFAULT 0,
                source_updated_at REAL NOT NULL,
                detected_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(login, from_group, to_group, source_updated_at)
            );
            CREATE INDEX IF NOT EXISTS idx_book_shift_events_detected
                ON book_shift_events(detected_at DESC);
            CREATE TABLE IF NOT EXISTS bbook_pnl_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                source_updated_at REAL NOT NULL,
                floating REAL NOT NULL DEFAULT 0,
                closed_swaps_comm REAL NOT NULL DEFAULT 0,
                ib_payout REAL NOT NULL DEFAULT 0,
                account_count INTEGER NOT NULL DEFAULT 0,
                groups_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(source_updated_at)
            );
            CREATE INDEX IF NOT EXISTS idx_bbook_pnl_snapshots_time
                ON bbook_pnl_snapshots(captured_at DESC);
            CREATE TABLE IF NOT EXISTS book_pnl_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book TEXT NOT NULL CHECK (book IN ('a_book','b_book','cent','hybrid')),
                captured_at TEXT NOT NULL,
                source_updated_at REAL NOT NULL,
                floating REAL NOT NULL DEFAULT 0,
                balance REAL NOT NULL DEFAULT 0,
                equity REAL NOT NULL DEFAULT 0,
                deposits REAL NOT NULL DEFAULT 0,
                withdrawals REAL NOT NULL DEFAULT 0,
                closed_profit REAL NOT NULL DEFAULT 0,
                swaps REAL NOT NULL DEFAULT 0,
                commissions REAL NOT NULL DEFAULT 0,
                closed_swaps_comm REAL NOT NULL DEFAULT 0,
                ib_payout REAL NOT NULL DEFAULT 0,
                account_count INTEGER NOT NULL DEFAULT 0,
                groups_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(book, source_updated_at)
            );
            CREATE INDEX IF NOT EXISTS idx_book_pnl_snapshots_book_time
                ON book_pnl_snapshots(book, captured_at DESC);
            CREATE TABLE IF NOT EXISTS book_pnl_daily (
                book TEXT NOT NULL CHECK (book IN ('a_book','b_book','cent','hybrid')),
                report_day TEXT NOT NULL,
                deposits REAL NOT NULL DEFAULT 0,
                withdrawals REAL NOT NULL DEFAULT 0,
                net_in_out REAL NOT NULL DEFAULT 0,
                balance REAL NOT NULL DEFAULT 0,
                equity REAL NOT NULL DEFAULT 0,
                closed_profit REAL NOT NULL DEFAULT 0,
                swaps REAL NOT NULL DEFAULT 0,
                commissions REAL NOT NULL DEFAULT 0,
                closed_swaps_comm REAL NOT NULL DEFAULT 0,
                opening_floating REAL NOT NULL DEFAULT 0,
                closing_floating REAL NOT NULL DEFAULT 0,
                floating_delta REAL NOT NULL DEFAULT 0,
                shift_corrections REAL NOT NULL DEFAULT 0,
                ib_payout REAL NOT NULL DEFAULT 0,
                total_book_pnl REAL NOT NULL DEFAULT 0,
                account_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY(book, report_day)
            );
            CREATE TABLE IF NOT EXISTS cid_observations (
                login INTEGER NOT NULL,
                computer_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                account_group TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                first_seen TEXT NOT NULL DEFAULT (datetime('now')),
                last_seen TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (login, computer_id)
            );
            CREATE TABLE IF NOT EXISTS cid_login_events (
                event_key TEXT PRIMARY KEY,
                occurred_at TEXT NOT NULL,
                login INTEGER NOT NULL,
                computer_id TEXT NOT NULL,
                ip_address TEXT NOT NULL DEFAULT '',
                device_type TEXT NOT NULL DEFAULT '',
                build INTEGER NOT NULL DEFAULT 0,
                access_server TEXT NOT NULL DEFAULT '',
                ping_ms REAL,
                country TEXT NOT NULL DEFAULT '',
                city TEXT NOT NULL DEFAULT '',
                isp TEXT NOT NULL DEFAULT '',
                raw_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        snapshot_sql_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='book_pnl_snapshots'"
        ).fetchone()
        snapshot_sql = str(snapshot_sql_row[0] or "") if snapshot_sql_row else ""
        if "'cent'" not in snapshot_sql:
            con.executescript(
                """
                ALTER TABLE book_pnl_snapshots RENAME TO book_pnl_snapshots_legacy;
                CREATE TABLE book_pnl_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book TEXT NOT NULL CHECK (book IN ('a_book','b_book','cent','hybrid')),
                    captured_at TEXT NOT NULL,
                    source_updated_at REAL NOT NULL,
                    floating REAL NOT NULL DEFAULT 0,
                    balance REAL NOT NULL DEFAULT 0,
                    equity REAL NOT NULL DEFAULT 0,
                    deposits REAL NOT NULL DEFAULT 0,
                    withdrawals REAL NOT NULL DEFAULT 0,
                    closed_profit REAL NOT NULL DEFAULT 0,
                    swaps REAL NOT NULL DEFAULT 0,
                    commissions REAL NOT NULL DEFAULT 0,
                    closed_swaps_comm REAL NOT NULL DEFAULT 0,
                    ib_payout REAL NOT NULL DEFAULT 0,
                    account_count INTEGER NOT NULL DEFAULT 0,
                    groups_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(book, source_updated_at)
                );
                INSERT INTO book_pnl_snapshots
                    (id,book,captured_at,source_updated_at,floating,closed_swaps_comm,
                     ib_payout,account_count,groups_json)
                SELECT id,book,captured_at,source_updated_at,floating,closed_swaps_comm,
                       ib_payout,account_count,groups_json
                FROM book_pnl_snapshots_legacy;
                DROP TABLE book_pnl_snapshots_legacy;
                """
            )
        daily_sql_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='book_pnl_daily'"
        ).fetchone()
        daily_sql = str(daily_sql_row[0] or "") if daily_sql_row else ""
        if "'cent'" not in daily_sql:
            con.executescript(
                """
                ALTER TABLE book_pnl_daily RENAME TO book_pnl_daily_legacy;
                CREATE TABLE book_pnl_daily (
                    book TEXT NOT NULL CHECK (book IN ('a_book','b_book','cent','hybrid')),
                    report_day TEXT NOT NULL,
                    deposits REAL NOT NULL DEFAULT 0,
                    withdrawals REAL NOT NULL DEFAULT 0,
                    net_in_out REAL NOT NULL DEFAULT 0,
                    balance REAL NOT NULL DEFAULT 0,
                    equity REAL NOT NULL DEFAULT 0,
                    closed_profit REAL NOT NULL DEFAULT 0,
                    swaps REAL NOT NULL DEFAULT 0,
                    commissions REAL NOT NULL DEFAULT 0,
                    closed_swaps_comm REAL NOT NULL DEFAULT 0,
                    opening_floating REAL NOT NULL DEFAULT 0,
                    closing_floating REAL NOT NULL DEFAULT 0,
                    floating_delta REAL NOT NULL DEFAULT 0,
                    shift_corrections REAL NOT NULL DEFAULT 0,
                    ib_payout REAL NOT NULL DEFAULT 0,
                    total_book_pnl REAL NOT NULL DEFAULT 0,
                    account_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY(book, report_day)
                );
                INSERT INTO book_pnl_daily
                    (book,report_day,closed_swaps_comm,opening_floating,closing_floating,
                     floating_delta,shift_corrections,ib_payout,total_book_pnl,
                     account_count,updated_at)
                SELECT book,report_day,closed_swaps_comm,opening_floating,closing_floating,
                       floating_delta,shift_corrections,ib_payout,total_book_pnl,
                       account_count,updated_at
                FROM book_pnl_daily_legacy;
                DROP TABLE book_pnl_daily_legacy;
                """
            )
        for table, column, definition in (
            ("book_membership_state", "floating_pnl", "REAL NOT NULL DEFAULT 0"),
            ("book_shift_events", "floating_pnl_at_shift", "REAL NOT NULL DEFAULT 0"),
            ("google_sheets_toxic_history", "toxic_reason", "TEXT NOT NULL DEFAULT ''"),
            ("book_pnl_daily", "source_status", "TEXT NOT NULL DEFAULT 'full'"),
        ):
            columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        con.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_cases_status_priority ON investigation_cases(status, priority, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_case_events_case_id ON case_events(case_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_limits_enabled ON exposure_limits(enabled, scope_type, scope_value);
            CREATE INDEX IF NOT EXISTS idx_cid_observations_cid ON cid_observations(computer_id, last_seen DESC);
            CREATE INDEX IF NOT EXISTS idx_cid_events_login_time ON cid_login_events(login, occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_cid_events_cid_time ON cid_login_events(computer_id, occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_cid_events_ip_time ON cid_login_events(ip_address, occurred_at DESC);
            """
        )
        con.executescript(NEWS_PUSH_SCHEMA)
        con.executescript(SPREAD_ALERT_SCHEMA)
        channel_count = con.execute("SELECT COUNT(*) FROM notification_channels").fetchone()[0]
        if channel_count == 0:
            con.execute(
                "INSERT INTO notification_channels (name, channel_type, destination, min_severity, enabled) VALUES (?,?,?,?,?)",
                ("Operations Telegram", "telegram", "configured-chat", "warning", 1),
            )
        con.execute("""INSERT OR IGNORE INTO news_trader_incidents
            (login,position_id,calendar_event_ts,symbol,first_seen_at,evidence_json)
            SELECT login,CAST(position_id AS TEXT),CAST(json_extract(actual_values_json,'$.calendar_event_ts') AS INTEGER),
                   symbol,first_seen_at,evidence_json
            FROM risk_events WHERE detector='news_trading' AND data_quality='VERIFIED'
              AND login>0 AND position_id>0 AND json_valid(actual_values_json)
              AND CAST(json_extract(actual_values_json,'$.calendar_event_ts') AS INTEGER)>0""")
        con.execute("INSERT OR IGNORE INTO book_group_settings (id) VALUES (1)")
        row = con.execute("SELECT COUNT(*) FROM app_users").fetchone()[0]
        if row == 0:
            con.execute(
                "INSERT INTO app_users (username, password_hash, is_admin, must_change_pw) VALUES (?,?,1,1)",
                ("admin", _hash_pw("admin")),
            )


def verify_user(username: str, password: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute(
            "SELECT id, username, password_hash, is_admin, must_change_pw FROM app_users WHERE LOWER(username)=LOWER(?)",
            (username.strip(),),
        ).fetchone()
        if not row or not _verify_pw(password, str(row["password_hash"])):
            return None
        if not str(row["password_hash"]).startswith("pbkdf2_sha256$"):
            con.execute("UPDATE app_users SET password_hash=? WHERE id=?", (_hash_pw(password), row["id"]))
        user = dict(row)
    user.pop("password_hash", None)
    return user


def write_audit(actor: str, action: str, entity_type: str = "", entity_id: Any = "", details: Any = None) -> None:
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO audit_log (actor, action, entity_type, entity_id, details_json) VALUES (?,?,?,?,?)",
                (str(actor or "system")[:120], str(action)[:160], str(entity_type)[:80], str(entity_id or "")[:120], json.dumps(details or {}, default=str)[:20000]),
            )
    except Exception:
        logger.exception("Unable to write audit event")


def _record_news_incidents(events: list[dict[str, Any]], clients=None) -> dict[int, int]:
    """Count verified trade/release matches once across scopes, settings and restarts."""
    news = [event for event in events if (event.get("rule") or event.get("detector")) == "news_trading"]
    logins = sorted({_to_int(event.get("login"), 0) for event in [*news, *(clients or [])]} - {0})
    if not logins:
        return {}
    with _conn() as con:
        for event in news:
            login = _to_int(event.get("login"), 0)
            position = _to_int(event.get("position_id"), 0)
            release = _to_int((event.get("triggering_values") or {}).get("calendar_event_ts"), 0)
            if event.get("data_quality") == "VERIFIED" and login > 0 and position > 0 and release > 0:
                con.execute("""INSERT OR IGNORE INTO news_trader_incidents
                    (login,position_id,calendar_event_ts,symbol,first_seen_at,evidence_json)
                    VALUES (?,?,?,?,?,?)""", (login, str(position), release, str(event.get("symbol") or ""),
                    datetime.now(timezone.utc).isoformat(), json.dumps(event, default=str)))
        marks = ",".join("?" for _ in logins)
        counts = {int(row["login"]): int(row["n"]) for row in con.execute(
            f"SELECT login,COUNT(*) AS n FROM news_trader_incidents WHERE login IN ({marks}) GROUP BY login", logins)}
    for row in [*news, *(clients or [])]:
        count = counts.get(_to_int(row.get("login"), 0), 0)
        if count:
            row["news_incident_count"] = count
            row["news_flag"] = f"News trader {count}x"
    return counts


def persist_antifraud_events(events: list[dict[str, Any]], source_generation: Any = None) -> list[dict[str, Any]]:
    """Keep first/last seen and dealer status across scans and process restarts."""
    _record_news_incidents(events)
    now = datetime.now(timezone.utc).isoformat()
    created: list[dict[str, Any]] = []
    with _conn() as con:
        pending_writes = 0
        for event in events:
            if event.get("data_quality") == "UNCHECKED":
                continue
            identity = (["news_trading", event.get("login"), event.get("position_id"), (event.get("triggering_values") or {}).get("calendar_event_ts")]
                        if (event.get("rule") or event.get("detector")) == "news_trading" and event.get("position_id") and (event.get("triggering_values") or {}).get("calendar_event_ts")
                        else ["deposit_alert", event.get("login"), event.get("deal_id")]
                        if event.get("rule") == "deposit_alert" and event.get("deal_id") is not None
                        else [event.get("rule") or event.get("detector"), event.get("login"), event.get("position_id"), event.get("symbol"), event.get("thresholds") or {}])
            event_id = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:24]
            row = (event_id, "antifraud", str(event.get("rule") or event.get("detector") or ""), _to_int(event.get("login"), None), _to_int(event.get("position_id"), None), str(event.get("symbol") or ""), str(event.get("group") or ""), str(event.get("severity") or "warning"), str(event.get("data_quality") or "PARTIAL"), "mt5_manager", str(source_generation or ""), str(event.get("timestamp") or now), now, now, json.dumps(event, default=str), json.dumps(event.get("thresholds") or {}, default=str), json.dumps(event.get("triggering_values") or {}, default=str), str(event.get("data_quality") or "PARTIAL"))
            inserted = con.execute("""INSERT OR IGNORE INTO risk_events(event_id,event_type,detector,login,position_id,symbol,account_group,severity,confidence,source,source_generation,detected_at,first_seen_at,last_seen_at,evidence_json,thresholds_json,actual_values_json,data_quality) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row).rowcount
            if inserted:
                created.append(event)
            else:
                con.execute("""UPDATE risk_events SET last_seen_at=?,source_generation=?,detected_at=?,severity=?,confidence=?,evidence_json=?,actual_values_json=?,data_quality=? WHERE event_id=?""", (now, str(source_generation or ""), str(event.get("timestamp") or now), str(event.get("severity") or "warning"), str(event.get("data_quality") or "PARTIAL"), json.dumps(event, default=str), json.dumps(event.get("triggering_values") or {}, default=str), str(event.get("data_quality") or "PARTIAL"), event_id))
            pending_writes += 1
            if pending_writes >= 100:
                con.commit()
                pending_writes = 0
    if created:
        global _IN_APP_RISK_EVENT_VERSION
        with _IN_APP_RISK_EVENT_LOCK:
            _IN_APP_RISK_EVENT_VERSION += 1
    return created


def _json_column(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return default


def _case_record(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["evidence"] = _json_column(data.pop("evidence_json", "[]"), [])
    return data


def _severity_rank(value: str) -> int:
    return {"info": 0, "warning": 1, "high": 2, "critical": 3}.get(str(value).lower(), 1)


def dispatch_operations_alert(alert: dict[str, Any]) -> None:
    """Deliver a real alert through every enabled channel meeting its severity."""
    with _conn() as con:
        channels = [dict(row) for row in con.execute("SELECT * FROM notification_channels WHERE enabled=1").fetchall()]
    severity = str(alert.get("severity", "warning"))
    message = f"MT5 {severity.upper()} ALERT\n{alert.get('title', 'Operations alert')}\n{alert.get('detail', '')}"
    for channel in channels:
        if _severity_rank(severity) < _severity_rank(channel.get("min_severity", "warning")):
            continue
        kind = channel.get("channel_type")
        try:
            if kind == "telegram":
                send_telegram_notification(message)
            elif kind == "webhook" and str(channel.get("destination", "")).startswith(("https://", "http://")):
                payload = json.dumps({"alert": alert}).encode("utf-8")
                request = urllib.request.Request(channel["destination"], data=payload, headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(request, timeout=8):
                    pass
            elif kind == "email" and channel.get("destination"):
                import smtplib
                from email.message import EmailMessage
                host = os.getenv("SMTP_HOST", "").strip()
                if not host:
                    continue
                email = EmailMessage()
                email["Subject"] = f"MT5 {severity.upper()}: {alert.get('title', 'Operations alert')}"
                email["From"] = os.getenv("SMTP_FROM", "mt5-alerts@localhost")
                email["To"] = channel["destination"]
                email.set_content(message)
                with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as client:
                    if os.getenv("SMTP_TLS", "true").lower() in {"1", "true", "yes"}:
                        client.starttls()
                    username = os.getenv("SMTP_USERNAME", "")
                    if username:
                        client.login(username, os.getenv("SMTP_PASSWORD", ""))
                    client.send_message(email)
        except Exception:
            logger.exception("Operations notification channel failed: %s", channel.get("name"))


def _last_error_text() -> str:
    try:
        err = MT5Manager.LastError()
    except Exception as exc:
        return str(exc)

    if isinstance(err, tuple):
        native_code = err[0] if len(err) > 0 else ""
        retcode = err[1] if len(err) > 1 else ""
        message = err[2] if len(err) > 2 else ""
        if message:
            return f"{message} (native={native_code}, retcode={retcode})"
        return f"native={native_code}, retcode={retcode}"
    return str(err)


def _retcode_to_int(code) -> int | None:
    try:
        return int(code)
    except Exception:
        try:
            return int(getattr(code, "value"))
        except Exception:
            return None


def _is_retcode_like(value) -> bool:
    if isinstance(value, int):
        return True
    if isinstance(value, MT5Manager.EnMTAPIRetcode):
        return True
    return "EnMTAPIRetcode" in value.__class__.__name__


def _unwrap_response(resp):
    retcode = None
    data = resp
    message = ""

    if isinstance(resp, tuple):
        if len(resp) >= 2 and _is_retcode_like(resp[0]):
            retcode = _retcode_to_int(resp[0])
            data = resp[1]
            if len(resp) >= 3 and resp[2] is not None:
                message = str(resp[2])
        elif len(resp) == 1:
            data = resp[0]

    return retcode, data, message


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (str, bytes, dict)):
        return []
    try:
        return list(value)
    except Exception:
        return [value]


def _response_to_data(resp, ctx):
    retcode, data, message = _unwrap_response(resp)
    if retcode is not None and retcode != 0:
        err = message or _last_error_text()
        return None, f"{ctx} failed ({retcode}): {err}"
    return data, None


def _value(obj, *names, default=None):
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
        for candidate in (
            name,
            str(name).lower(),
            str(name).upper(),
            str(name)[:1].lower() + str(name)[1:],
        ):
            try:
                val = obj[candidate]
            except Exception:
                continue
            if val is not None:
                return val

    checked = set()
    for name in names:
        for candidate in (
            name,
            str(name).lower(),
            str(name).upper(),
            str(name)[:1].lower() + str(name)[1:],
        ):
            if candidate in checked:
                continue
            checked.add(candidate)
            try:
                attr = getattr(obj, candidate)
            except Exception:
                continue
            try:
                return attr() if callable(attr) else attr
            except Exception:
                continue

    return default


def _to_int(value, default=None):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _to_float(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _pick_float(*values, default=0.0) -> float:
    for val in values:
        if val is None:
            continue
        try:
            num = float(val)
            if not math.isfinite(num):
                continue
            return num
        except Exception:
            continue
    return default


def _enum_int(default, *path) -> int:
    obj = MT5Manager
    try:
        for part in path:
            obj = getattr(obj, part)
        return int(obj)
    except Exception:
        return int(default)


DEAL_BALANCE = _enum_int(2, "MTDeal", "EnDealAction", "DEAL_BALANCE")
DEAL_CREDIT = _enum_int(3, "MTDeal", "EnDealAction", "DEAL_CREDIT")
DEAL_CHARGE = _enum_int(4, "MTDeal", "EnDealAction", "DEAL_CHARGE")
DEAL_CORRECTION = _enum_int(5, "MTDeal", "EnDealAction", "DEAL_CORRECTION")
DEAL_BONUS = _enum_int(6, "MTDeal", "EnDealAction", "DEAL_BONUS")
DEAL_COMMISSION = _enum_int(7, "MTDeal", "EnDealAction", "DEAL_COMMISSION")
DEAL_COMMISSION_DAILY = _enum_int(8, "MTDeal", "EnDealAction", "DEAL_COMMISSION_DAILY")
DEAL_COMMISSION_MONTHLY = _enum_int(9, "MTDeal", "EnDealAction", "DEAL_COMMISSION_MONTHLY")
DEAL_COMMISSION_AGENT_DAILY = _enum_int(10, "MTDeal", "EnDealAction", "DEAL_AGENT_DAILY")
DEAL_COMMISSION_AGENT_MONTHLY = _enum_int(11, "MTDeal", "EnDealAction", "DEAL_AGENT_MONTHLY")
DEAL_COMMISSION_AGENT_INSTANT = _enum_int(18, "MTDeal", "EnDealAction", "DEAL_AGENT")
DEAL_INTEREST = _enum_int(12, "MTDeal", "EnDealAction", "DEAL_INTERESTRATE")
DEAL_BUY = _enum_int(0, "MTDeal", "EnDealAction", "DEAL_BUY")
DEAL_SELL = _enum_int(1, "MTDeal", "EnDealAction", "DEAL_SELL")
DEAL_BUY_CANCELED = _enum_int(13, "MTDeal", "EnDealAction", "DEAL_BUY_CANCELED")
DEAL_SELL_CANCELED = _enum_int(14, "MTDeal", "EnDealAction", "DEAL_SELL_CANCELED")
DEAL_DIVIDEND = _enum_int(15, "MTDeal", "EnDealAction", "DEAL_DIVIDEND")
DEAL_DIVIDEND_FRANKED = _enum_int(16, "MTDeal", "EnDealAction", "DEAL_DIVIDEND_FRANKED")
DEAL_TAX = _enum_int(17, "MTDeal", "EnDealAction", "DEAL_TAX")
ENTRY_OUT = _enum_int(1, "MTDeal", "EnDealEntry", "ENTRY_OUT")
ENTRY_OUT_BY = _enum_int(3, "MTDeal", "EnDealEntry", "ENTRY_OUT_BY")
ENTRY_INOUT = _enum_int(2, "MTDeal", "EnDealEntry", "ENTRY_INOUT")

TRADING_ACTIONS = {DEAL_BUY, DEAL_SELL, DEAL_BUY_CANCELED, DEAL_SELL_CANCELED}
CLOSED_ENTRIES = {ENTRY_OUT, ENTRY_OUT_BY, ENTRY_INOUT}
ADDITIONAL_ACTIONS = {
    DEAL_CHARGE,
    DEAL_CORRECTION,
    DEAL_BONUS,
    DEAL_COMMISSION,
    DEAL_COMMISSION_DAILY,
    DEAL_COMMISSION_MONTHLY,
    DEAL_COMMISSION_AGENT_DAILY,
    DEAL_COMMISSION_AGENT_MONTHLY,
    DEAL_COMMISSION_AGENT_INSTANT,
    DEAL_INTEREST,
    DEAL_DIVIDEND,
    DEAL_DIVIDEND_FRANKED,
    DEAL_TAX,
}


def _deal_side_text(deal: Any) -> str:
    return str(
        _value(deal, "Type", "type", "DealType", "dealtype", "TradeType", "tradetype", default="") or ""
    ).strip().lower()


def _is_trade_deal(deal: Any, action: int | None = None, volume_units: float | None = None) -> bool:
    if action in {DEAL_BUY, DEAL_SELL}:
        return True

    if _deal_side_text(deal) in {"buy", "sell"}:
        return True

    if volume_units is not None and volume_units > 0.0:
        return True

    return False


def connect_manager(server: str, login: int, password: str, timeout_ms: int = 30000):
    api = MT5Manager.ManagerAPI()
    pump_mode = _enum_int(1, "ManagerAPI", "EnPumpModes", "PUMP_MODE_USERS")
    attempts = [
        (server, login, password, pump_mode, timeout_ms),
        (server, login, password, timeout_ms),
        (server, login, password),
    ]

    for args in attempts:
        try:
            result = api.Connect(*args)
        except TypeError:
            continue
        except Exception:
            return None, _last_error_text() or "Unable to connect."

        if isinstance(result, bool):
            ok = result
        else:
            retcode, _, _ = _unwrap_response(result)
            ok = (retcode is None) or (retcode == 0) or bool(result)

        if ok:
            return api, None

        # The signature was accepted and the SDK completed the attempt. Do not
        # retry with legacy signatures: on current SDKs those overloads use
        # their own default timeout and can turn one failure into a minute wait.
        return None, _last_error_text() or "Unable to connect."

    return None, _last_error_text() or "Unable to connect."


def connect_market_manager(server: str, login: int, password: str, timeout_ms: int = 30000):
    """Connect with the real market-data pumps required by tick anti-fraud rules."""
    api_handle = MT5Manager.ManagerAPI()
    modes = getattr(MT5Manager.ManagerAPI, "EnPumpModes", None)
    pump_names = (
        "PUMP_MODE_USERS", "PUMP_MODE_GROUPS", "PUMP_MODE_SYMBOLS",
        "PUMP_MODE_TIME", "PUMP_MODE_POSITIONS", "PUMP_MODE_ORDERS",
    )
    pump_mask = 0
    for name in pump_names:
        pump_mask |= int(getattr(modes, name, 0)) if modes is not None else 0
    try:
        result = api_handle.Connect(server, login, password, pump_mask or 1, timeout_ms)
    except Exception as exc:
        return None, str(exc)
    if bool(result):
        return api_handle, None
    return None, _last_error_text() or "Unable to connect with market-data pumps."


def _collect_group_names(payload, collected_groups: set[str]) -> None:
    if payload is None:
        return

    if isinstance(payload, str):
        name = payload.strip()
        if name:
            collected_groups.add(name)
        return

    if isinstance(payload, dict) or hasattr(payload, "_asdict"):
        name = _value(payload, "Group", "group", "Name", "name", "Path", "path")
        if name:
            text = str(name).strip()
            if text:
                collected_groups.add(text)
        return

    if not isinstance(payload, (list, tuple, set)):
        name = _value(payload, "Group", "group", "Name", "name", "Path", "path")
        if name:
            text = str(name).strip()
            if text:
                collected_groups.add(text)
        return

    for item in _as_list(payload):
        if isinstance(item, str):
            text = item.strip()
            if text:
                collected_groups.add(text)
            continue

        name = _value(item, "Group", "group", "Name", "name", "Path", "path")
        if name:
            text = str(name).strip()
            if text:
                collected_groups.add(text)


def _collect_manager_access_groups(api, collected_groups: set[str], errors: list[str]) -> None:
    if api is None:
        return

    current_method = getattr(api, "ManagerCurrent", None)
    if current_method is None:
        return

    try:
        resp = current_method()
    except Exception as exc:
        errors.append(str(exc))
        return

    data, err = _response_to_data(resp, "ManagerCurrent")
    if err:
        errors.append(err)
        return

    if data is None:
        return

    if hasattr(data, "GroupGet"):
        try:
            group_resp = data.GroupGet()
        except Exception as exc:
            errors.append(str(exc))
        else:
            group_data, err = _response_to_data(group_resp, "ManagerCurrent.GroupGet")
            if err:
                errors.append(err)
            else:
                _collect_group_names(group_data, collected_groups)

    if hasattr(data, "GroupTotal") and hasattr(data, "GroupNext"):
        try:
            total_resp = data.GroupTotal()
            total_data, err = _response_to_data(total_resp, "ManagerCurrent.GroupTotal")
            if err:
                errors.append(err)
                return

            total = _to_int(total_data, 0) or 0
            for pos in range(total):
                try:
                    resp = data.GroupNext(pos)
                except Exception as exc:
                    errors.append(str(exc))
                    continue

                group_data, err = _response_to_data(resp, f"ManagerCurrent.GroupNext({pos})")
                if err:
                    errors.append(err)
                    continue

                _collect_group_names(group_data, collected_groups)
        except Exception as exc:
            errors.append(str(exc))


def _collect_groups_by_mask(api, mask: str, collected_groups: set[str], errors: list[str]) -> None:
    if api is None:
        return

    mask = str(mask or "").strip()
    if not mask:
        return

    method = getattr(api, "GroupRequestArray", None)
    if method is None:
        return

    try:
        resp = method(mask)
    except Exception as exc:
        errors.append(str(exc))
        return

    group_data, err = _response_to_data(resp, f"GroupRequestArray({mask})")
    if err:
        errors.append(err)
        return

    _collect_group_names(group_data, collected_groups)


def _collect_groups_by_index(api, collected_groups: set[str], errors: list[str]) -> None:
    """Enumerate all accessible groups without GroupRequestArray page limits."""
    total_method = getattr(api, "GroupTotal", None)
    next_method = getattr(api, "GroupNext", None)
    if total_method is None or next_method is None:
        return
    try:
        total_resp = total_method()
        total_data, err = _response_to_data(total_resp, "GroupTotal")
        if err:
            errors.append(err)
            return
        total = _to_int(total_data, 0) or 0
    except Exception as exc:
        errors.append(str(exc))
        return

    for index in range(total):
        try:
            response = next_method(index)
        except Exception as exc:
            errors.append(str(exc))
            continue
        group_data, err = _response_to_data(response, f"GroupNext({index})")
        if err:
            errors.append(err)
            continue
        _collect_group_names(group_data, collected_groups)


def _manager_group_display_name(pattern: str) -> str:
    text = str(pattern or "").strip()
    if not text:
        return ""
    if text == "*":
        return text
    if text.endswith("*"):
        text = text[:-1]
    return text.rstrip("\\/").strip()


def request_groups(api):
    if api is None:
        return [], "Not connected."

    errors = []
    manager_groups: set[str] = set()
    collected_groups: set[str] = set()
    indexed_groups: set[str] = set()

    _collect_manager_access_groups(api, manager_groups, errors)
    _collect_groups_by_index(api, indexed_groups, errors)

    # GroupTotal/GroupNext is the authoritative catalog already scoped by the
    # connected manager's permissions. ManagerCurrent.GroupGet may itself be a
    # capped list, so intersecting indexed results with it can truncate 158 to 99.
    if indexed_groups:
        return sorted(indexed_groups, key=lambda value: (value.lower(), value)), None

    if manager_groups:
        for mask in sorted(manager_groups, key=lambda value: (value.lower(), value)):
            _collect_groups_by_mask(api, mask, collected_groups, errors)

        if collected_groups:
            filtered_groups = apply_group_filters(sorted(collected_groups), sorted(manager_groups), "")
            if filtered_groups:
                return filtered_groups, None

            fallback_groups = [
                normalized
                for normalized in (
                    _manager_group_display_name(mask) for mask in sorted(manager_groups, key=lambda value: (value.lower(), value))
                )
                if normalized
            ]
            if fallback_groups:
                return sorted(set(fallback_groups), key=lambda value: (value.lower(), value)), None

    call_variants = [
        ("GroupRequestArray", ("*",)),
        ("GroupRequestArray", ("",)),
        ("GroupRequestArray", tuple()),
    ]

    for method_name, args in call_variants:
        method = getattr(api, method_name, None)
        if method is None:
            continue
        try:
            resp = method(*args)
        except TypeError as exc:
            errors.append(str(exc))
            continue
        except Exception as exc:
            errors.append(str(exc))
            continue

        data, err = _response_to_data(resp, method_name)
        if err:
            errors.append(err)
            continue

        _collect_group_names(data, collected_groups)

    if manager_groups:
        filtered_groups = apply_group_filters(sorted(collected_groups), sorted(manager_groups), "")
        if filtered_groups:
            return filtered_groups, None

        fallback_groups = [
            normalized
            for normalized in (
                _manager_group_display_name(mask) for mask in sorted(manager_groups, key=lambda value: (value.lower(), value))
            )
            if normalized
        ]
        if fallback_groups:
            return sorted(set(fallback_groups), key=lambda value: (value.lower(), value)), None

    if collected_groups:
        return sorted(collected_groups, key=lambda value: (value.lower(), value)), None

    return [], "; ".join(errors[-3:]) if errors else "Failed to load groups."


def request_users_by_group(api, group):
    if api is None:
        return [], "Not connected."
    errors = []
    found_method = False
    # RequestByGroup asks the server for an authoritative snapshot. GetByGroup
    # reads the local pump and can legitimately be empty just after startup.
    for method_name in ("UserRequestByGroup", "UserGetByGroup"):
        method = getattr(api, method_name, None)
        if method is None:
            continue
        found_method = True
        try:
            resp = method(group)
        except Exception as exc:
            errors.append(str(exc))
        else:
            data, err = _response_to_data(resp, f"{method_name}({group})")
            if not err:
                rows = _as_list(data)
                if rows or method_name == "UserGetByGroup":
                    return rows, None
            else:
                errors.append(err)

        if hasattr(MT5Manager, "UserArray"):
            try:
                container = MT5Manager.UserArray()
                ok = method(group, container)
                if bool(ok):
                    rows = _as_list(container)
                    if rows or method_name == "UserGetByGroup":
                        return rows, None
                errors.append(_last_error_text())
            except Exception as exc:
                errors.append(str(exc))

    if not found_method:
        return [], "No Manager user-by-group method is available in this wrapper."
    return [], "; ".join(errors[-3:]) if errors else "User request failed."


def request_deals_by_group(api, group, from_ts: int, to_ts: int):
    if api is None:
        return [], "Not connected."
    method = getattr(api, "DealRequestByGroup", None)
    if method is None:
        return [], "DealRequestByGroup is not available in this wrapper."

    errors = []
    try:
        resp = method(group, from_ts, to_ts)
    except Exception as exc:
        errors.append(str(exc))
    else:
        code, _, _ = _unwrap_response(resp)
        if code in {1, 13} or (resp is False and brokerage_pnl._last_error_code() in {1, 13}):
            return [], None  # The installed wrapper reports empty history as Not Found.
        if resp is False:
            errors.append(f"DealRequestByGroup({group}): {_last_error_text()}")
        else:
            data, err = _response_to_data(resp, f"DealRequestByGroup({group})")
            if not err:
                return _as_list(data), None
            errors.append(err)

    if hasattr(MT5Manager, "DealArray"):
        try:
            container = MT5Manager.DealArray()
            ok = method(group, from_ts, to_ts, container)
            if bool(ok):
                return _as_list(container), None
            errors.append(_last_error_text())
        except Exception as exc:
            errors.append(str(exc))

    return [], "; ".join(errors[-3:]) if errors else "Deal request failed."


def request_deals_by_login(api, login: int, from_ts: int, to_ts: int):
    """Request one login directly when the installed Manager wrapper supports it."""
    if api is None:
        return [], "Not connected."
    method = getattr(api, "DealRequestByLogins", None)
    if method is None:
        return [], "DealRequestByLogins is not available in this wrapper."
    errors = []
    for logins in ([int(login)], (int(login),)):
        try:
            resp = method(logins, from_ts, to_ts)
        except Exception as exc:
            errors.append(str(exc))
            continue
        code, _, _ = _unwrap_response(resp)
        if code in {1, 13} or (resp is False and brokerage_pnl._last_error_code() in {1, 13}):
            return [], None
        if resp is False or resp is None or resp is True:
            errors.append("DealRequestByLogins returned no deal array: " + _last_error_text())
            continue
        data, err = _response_to_data(resp, "DealRequestByLogins")
        if not err:
            return _as_list(data), None
        errors.append(err)
    return [], "; ".join(errors[-3:]) if errors else "Login deal request failed."


def request_deals_by_logins(api_handle, logins: Any, from_ts: int, to_ts: int) -> tuple[list[Any], str | None]:
    method = getattr(api_handle, "DealRequestByLogins", None)
    numpy_method = getattr(api_handle, "DealRequestByLoginsNumPy", None)
    normalized = sorted(_normalize_login_set(logins))
    if (method is None and numpy_method is None) or not normalized:
        return [], "DealRequestByLogins is unavailable or no logins were supplied."
    rows: list[Any] = []
    errors: list[str] = []
    fields = "Deal,Login,Action,Entry,Time,TimeMsc,Volume,VolumeExt,Profit,Commission,Fee,Storage,Swap"
    for offset in range(0, len(normalized), 500):
        chunk = normalized[offset : offset + 500]
        try:
            if numpy_method is not None:
                response = numpy_method(chunk, from_ts, to_ts, fields)
                data, err = _response_to_data(response, "DealRequestByLoginsNumPy")
            else:
                response = method(chunk, from_ts, to_ts)
                data, err = _response_to_data(response, "DealRequestByLogins")
        except Exception as exc:
            if numpy_method is not None and method is not None:
                try:
                    response = method(chunk, from_ts, to_ts)
                    data, err = _response_to_data(response, "DealRequestByLogins")
                except Exception as fallback_exc:
                    data, err = [], str(fallback_exc)
            else:
                data, err = [], str(exc)
        if err:
            errors.append(err)
        else:
            rows.extend(_as_list(data))
    if rows or not errors:
        return rows, None
    return [], "; ".join(errors[-3:])


def request_positions_by_logins(api_handle, logins: Any) -> tuple[list[Any], str | None]:
    method = getattr(api_handle, "PositionRequestByLogins", None)
    normalized = sorted(_normalize_login_set(logins))
    if method is None or not normalized:
        return [], "PositionRequestByLogins is unavailable or no logins were supplied."
    rows: list[Any] = []
    errors: list[str] = []
    for offset in range(0, len(normalized), 2000):
        try:
            response = method(normalized[offset : offset + 2000])
            data, err = _response_to_data(response, "PositionRequestByLogins")
        except Exception as exc:
            data, err = [], str(exc)
        if err:
            errors.append(err)
        else:
            rows.extend(_as_list(data))
    if rows or not errors:
        return rows, None
    return [], "; ".join(errors[-3:])


_IPV4_PATTERN = re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")


def _journal_records(response: Any) -> list[Any]:
    if response is None or isinstance(response, (bool, int, float, str, bytes)):
        return []
    data, err = _response_to_data(response, "LoggerServerRequest")
    if not err:
        return [row for row in _as_list(data) if not isinstance(row, (bool, int, float, str, bytes))]
    return []


def request_trade_journal(
    api_handle, login: int, from_ts: int, to_ts: int,
    server: str = "", manager_login: int = 0, password: str = "",
    relevant_ids: set[int] | None = None,
) -> tuple[list[Any], str | None]:
    """Request filtered server journal records used to prove per-trade execution IPs."""
    method = getattr(api_handle, "LoggerServerRequest", None)
    if method is None:
        return [], "LoggerServerRequest is unavailable in this Manager API wrapper."
    modes = getattr(MT5Manager, "EnMTLogRequestMode", object())
    types = getattr(MT5Manager, "EnMTLogType", object())
    mode_values = list(dict.fromkeys(
        getattr(modes, name) for name in ("MTLogModeFull", "MTLogModeStd", "MTLogModeFirst")
        if hasattr(modes, name)
    )) or [0]
    type_values = list(dict.fromkeys(
        getattr(types, name) for name in
        ("MTLogTypeLiveUpdate", "MTLogTypeUser", "MTLogTypeNet", "MTLogTypeSys", "MTLogTypeHst", "MTLogTypeAll")
        if hasattr(types, name)
    )) or [0]
    filters = [str(login), f"'{login}'", f"*{login}*"]
    time_ranges = [(from_ts, to_ts), (from_ts * 1000, to_ts * 1000)]
    errors: list[str] = []
    collected: list[Any] = []
    seen: set[tuple[str, str]] = set()

    def meaningful(rows: list[Any]) -> list[Any]:
        matched: list[Any] = []
        needles = {str(login), *(str(value) for value in (relevant_ids or set()) if value)}
        for row in rows:
            message = str(_value(row, "Message", "message", "Text", "text", default="") or "").strip()
            if not message or not message.strip("-=_ \t\r\n"):
                continue
            if any(needle in message for needle in needles):
                matched.append(row)
        return matched
    for mode in mode_values:
        for log_type in type_values:
            for start, end in time_ranges:
                for filter_text in filters:
                    try:
                        rows = _journal_records(method(mode, log_type, start, end, filter_text))
                    except Exception as exc:
                        errors.append(str(exc))
                        continue
                    for row in meaningful(rows):
                        key = (str(_value(row, "Time", "Datetime", default="")),
                               str(_value(row, "Message", "Text", default="")))
                        if key not in seen:
                            seen.add(key)
                            collected.append(row)
                    # A login line alone cannot prove a trade IP. Keep searching
                    # journal types until a public IP is linked to a trade ID.
                    if relevant_ids and any(
                        _public_ip(ip)
                        and any(re.search(rf"(?<!\d){identifier}(?!\d)",
                                          str(_value(row, "Message", "Text", default="")))
                                for identifier in relevant_ids)
                        for row in collected
                        for ip in _IPV4_PATTERN.findall(str(_value(row, "Message", "Text", default="")))
                    ):
                        return collected, None

    if collected:
        return collected, None
    return [], "; ".join(errors[-2:]) if errors else "No matching trade journal records were returned, or this manager lacks server-log permission."


_CID_LOGIN_MESSAGE_PATTERN = re.compile(
    r"'?(?P<login>\d+)'?\s*:\s*login(?:\s+as\s+\S+)?\s*\("
    r"Client\s+(?P<device>\S+)\s+build\s+(?P<build>\d+)\s*,\s*"
    r"cid\s*:\s*(?P<cid>[0-9a-fA-F]+)\s*,\s*"
    r"point\s*:\s*(?P<point>[^,]+)\s*,\s*"
    r"ping\s*:\s*(?P<ping>[\d.]+)\s*ms\s*,\s*"
    r"geo\s*:\s*(?P<geo>[^)]*)\)?",
    re.IGNORECASE,
)


def parse_cid_login_message(message: str) -> dict[str, Any] | None:
    """Parse a verified MT5 client-login journal message into CID evidence."""
    match = _CID_LOGIN_MESSAGE_PATTERN.search(str(message or ""))
    if not match:
        return None
    geo = [part.strip() for part in match.group("geo").split(",") if part.strip()]
    return {
        "login": int(match.group("login")),
        "computer_id": match.group("cid").lower(),
        "device_type": match.group("device"),
        "build": int(match.group("build")),
        "access_server": match.group("point").strip(),
        "ping_ms": float(match.group("ping")),
        "country": geo[0] if geo else "",
        "city": geo[1] if len(geo) > 1 else "",
        "isp": ", ".join(geo[2:]) if len(geo) > 2 else "",
    }


def _journal_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and value > 0:
        seconds = float(value) / 1000 if value > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    return None


def build_cid_login_events(records: list[Any]) -> list[dict[str, Any]]:
    """Normalize and deduplicate CID login evidence returned by the Manager journal."""
    events: dict[str, dict[str, Any]] = {}
    for record in records:
        message = str(_value(record, "Message", "message", "Text", "text", default="") or "")
        parsed = parse_cid_login_message(message)
        if not parsed:
            continue
        occurred = _journal_timestamp(_value(record, "Time", "time", "Datetime", "datetime"))
        ip_address = _ip_text(
            _value(record, "IP", "Ip", "ip", "Address", "address", "Source", default="")
        )
        occurred_text = occurred.isoformat() if occurred else ""
        event_key = hashlib.sha256(
            f"{occurred_text}|{parsed['login']}|{parsed['computer_id']}|{ip_address}|{message}".encode()
        ).hexdigest()
        events[event_key] = {
            "event_key": event_key,
            "occurred_at": occurred_text,
            "ip_address": ip_address,
            "raw_message": message,
            **parsed,
        }
    return sorted(events.values(), key=lambda row: row["occurred_at"], reverse=True)


def analyze_cid_login_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply z6 CID/IP multi-account risk rules without inventing missing evidence."""
    cid_logins: dict[str, set[int]] = defaultdict(set)
    ip_cids: dict[str, set[str]] = defaultdict(set)
    ip_logins: dict[str, set[int]] = defaultdict(set)
    login_cids: dict[int, set[str]] = defaultdict(set)
    cid_countries: dict[str, set[str]] = defaultdict(set)
    for event in events:
        login = int(event["login"])
        cid = str(event["computer_id"])
        ip_address = str(event.get("ip_address", ""))
        country = str(event.get("country", ""))
        cid_logins[cid].add(login)
        login_cids[login].add(cid)
        if ip_address:
            ip_cids[ip_address].add(cid)
            ip_logins[ip_address].add(login)
        if country:
            cid_countries[cid].add(country)

    flagged_events = []
    for event in events:
        cid = str(event["computer_id"])
        login = int(event["login"])
        ip_address = str(event.get("ip_address", ""))
        flags = {
            "shared_cid": len(cid_logins[cid]) >= 2,
            "shared_ip": bool(ip_address and len(ip_cids[ip_address]) >= 3),
            "multiple_cids": len(login_cids[login]) >= 2,
            "multiple_countries": len(cid_countries[cid]) >= 2,
        }
        flagged_events.append({
            **event,
            "cid_login_count": len(cid_logins[cid]),
            "ip_cid_count": len(ip_cids[ip_address]) if ip_address else 0,
            "flags": flags,
            "flagged": any(flags.values()),
        })
    cid_groups = [
        {
            "computer_id": cid,
            "logins": sorted(logins),
            "login_count": len(logins),
            "countries": sorted(cid_countries[cid]),
            "shared": len(logins) >= 2,
        }
        for cid, logins in cid_logins.items()
    ]
    ip_groups = [
        {
            "ip_address": address,
            "computer_ids": sorted(ip_cids[address]),
            "logins": sorted(logins),
            "cid_count": len(ip_cids[address]),
            "login_count": len(logins),
            "shared": len(ip_cids[address]) >= 3,
        }
        for address, logins in ip_logins.items()
    ]
    cid_groups.sort(key=lambda row: (-row["login_count"], row["computer_id"]))
    ip_groups.sort(key=lambda row: (-row["cid_count"], row["ip_address"]))
    return {
        "events": flagged_events,
        "cid_groups": cid_groups,
        "ip_groups": ip_groups,
        "summary": {
            "events": len(flagged_events),
            "logins": len(login_cids),
            "computer_ids": len(cid_logins),
            "ip_addresses": len(ip_cids),
            "flagged_events": sum(bool(row["flagged"]) for row in flagged_events),
            "shared_computer_ids": sum(len(logins) >= 2 for logins in cid_logins.values()),
            "shared_ip_addresses": sum(len(cids) >= 3 for cids in ip_cids.values()),
        },
        "risk_summary": {
            "shared_cid": sum(row["flags"]["shared_cid"] for row in flagged_events),
            "shared_ip": sum(row["flags"]["shared_ip"] for row in flagged_events),
            "multiple_cids": sum(row["flags"]["multiple_cids"] for row in flagged_events),
            "multiple_countries": sum(
                row["flags"]["multiple_countries"] for row in flagged_events
            ),
        },
    }


def request_cid_login_journal(
    api_handle: Any, from_ts: int, to_ts: int
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch MT5 client-login journal records using supported SDK enum variants."""
    method = getattr(api_handle, "LoggerServerRequest", None)
    if method is None:
        return [], "LoggerServerRequest is unavailable in this Manager API wrapper."
    modes = [
        value for value in (
            getattr(getattr(MT5Manager, "EnMTLogRequestMode", object()), name, None)
            for name in ("MTLogModeFull", "MTLogModeStd", "MTLogModeFirst")
        ) if value is not None
    ] or [0]
    log_types = [
        value for value in (
            getattr(getattr(MT5Manager, "EnMTLogType", object()), name, None)
            for name in ("MTLogTypeAll", "MTLogTypeUser", "MTLogTypeNet", "MTLogTypeSys", "MTLogTypeLiveUpdate")
        ) if value is not None
    ] or [0]
    errors: list[str] = []
    for mode in dict.fromkeys(modes):
        for log_type in dict.fromkeys(log_types):
            for start, end in ((from_ts, to_ts), (from_ts * 1000, to_ts * 1000)):
                try:
                    records = _journal_records(
                        method(mode, log_type, start, end, "login (Client")
                    )
                except Exception as exc:
                    errors.append(str(exc))
                    continue
                events = build_cid_login_events(records)
                if events:
                    return events, None
    return [], (
        "; ".join(errors[-2:])
        if errors
        else "No CID login journal entries were returned for this range."
    )


def request_cid_login_journal_isolated(
    server: str,
    manager_login: int,
    password: str,
    from_ts: int,
    to_ts: int,
    *,
    attempt_timeout_seconds: int = 3,
    max_attempts: int = 64,
    filter_text: str = "login (Client",
) -> tuple[list[dict[str, Any]], str | None]:
    """Read CID journals through the sole configured Manager connection."""
    del server, manager_login, password, attempt_timeout_seconds, max_attempts, filter_text
    try:
        with yielding_history_manager_session() as manager:
            return request_cid_login_journal(manager, from_ts, to_ts)
    except Exception as exc:
        return [], str(exc)



def build_ticket_position_map(deals: list[Any], login: int) -> tuple[dict[int, int], set[int]]:
    """Build the deal/order/ticket to PositionID map for one MT5 account."""
    ticket_to_position: dict[int, int] = {}
    known_positions: set[int] = set()
    for deal in deals:
        if _login_from_record(deal) != int(login):
            continue
        position_id = _to_int(_value(deal, "PositionID", "position_id", "Position", "position"), 0) or 0
        if not position_id:
            continue
        known_positions.add(position_id)
        for field in ("Deal", "deal", "Order", "order", "OrderID", "order_id", "Ticket", "ticket", "PositionID", "position_id"):
            ticket = _to_int(_value(deal, field), 0) or 0
            if ticket:
                ticket_to_position[ticket] = position_id
    return ticket_to_position, known_positions


def map_trade_execution_evidence(
    deals: list[Any], journal_rows: list[Any], login: int,
) -> dict[int, dict[str, list[str]]]:
    """Map proven journal IPs through ticket identifiers to MT5 positions."""
    ticket_to_position, known_positions = build_ticket_position_map(deals, login)

    by_position: dict[int, list[tuple[int, str, str]]] = defaultdict(list)
    for record in journal_rows:
        message = " ".join(str(_value(record, name, default="") or "") for name in ("Message", "message", "Text", "text", "Source", "source"))
        field_ip = _ip_text(_value(record, "IP", "ip", "Address", "address", "SourceIP", "source_ip"))
        candidates = ([field_ip] if field_ip else []) + _IPV4_PATTERN.findall(message)
        public_ips = [ip for ip in dict.fromkeys(candidates) if _public_ip(ip)]
        if not public_ips:
            continue
        identifiers: set[int] = set()
        for match in re.findall(r"(?<![\d.])\d{4,20}(?![\d.])", message):
            value = _to_int(match, 0) or 0
            if value in ticket_to_position or value in known_positions:
                identifiers.add(value)
        for field in ("Ticket", "ticket", "Deal", "deal", "Order", "order", "Position", "position", "PositionID", "position_id"):
            value = _to_int(_value(record, field), 0) or 0
            if value in ticket_to_position or value in known_positions:
                identifiers.add(value)
        timestamp = _to_int(_value(record, "Time", "time", "Datetime", "datetime"), 0) or 0
        source = str(
            _value(record, "Source", "source", "source_kind", default="remote_mt5_journal")
            or "remote_mt5_journal"
        )
        for identifier in identifiers:
            position_id = ticket_to_position.get(identifier, identifier if identifier in known_positions else 0)
            if not position_id:
                continue
            for ip in public_ips:
                by_position[position_id].append((timestamp, ip, source))
    return {
        position_id: {
            "ips": list(dict.fromkeys(ip for _, ip, _ in sorted(entries, key=lambda item: item[0]))),
            "sources": list(dict.fromkeys(source for _, _, source in sorted(entries, key=lambda item: item[0]))),
        }
        for position_id, entries in by_position.items()
    }


def map_trade_execution_ips(deals: list[Any], journal_rows: list[Any], login: int) -> dict[int, list[str]]:
    """Backward-compatible IP-only view of ticket-linked journal evidence."""
    return {
        position_id: evidence["ips"]
        for position_id, evidence in map_trade_execution_evidence(deals, journal_rows, login).items()
    }


def apply_trade_execution_evidence(
    trades: list[dict[str, Any]],
    execution_evidence: dict[int, dict[str, list[str]]],
    execution_network: dict[str, dict[str, Any]],
    fallback_ip: str = "",
) -> dict[str, int]:
    """Attach journal evidence, then a clearly-labelled last-login fallback."""
    previous_trade_ip = ""
    covered_trades = 0
    ip_change_count = 0
    multi_ip_trades = 0
    inferred_trades = 0
    for trade in sorted(trades, key=lambda row: row.get("open_ts", 0)):
        evidence = execution_evidence.get(trade["position_id"], {})
        trade_ips = evidence.get("ips", [])
        trade_sources = evidence.get("sources", [])
        proven = bool(trade_ips)
        primary_ip = trade_ips[0] if proven else fallback_ip
        if primary_ip and not proven:
            trade_ips = [primary_ip]
            trade_sources = ["account_last_login_inferred"]
            inferred_trades += 1
        intel = execution_network.get(primary_ip, {})
        if proven:
            covered_trades += 1
        ip_changed = bool(primary_ip and previous_trade_ip and primary_ip != previous_trade_ip)
        if ip_changed:
            ip_change_count += 1
        if len(trade_ips) > 1:
            multi_ip_trades += 1
        if primary_ip:
            previous_trade_ip = primary_ip
        trade.update({
            "trade_ip": primary_ip,
            "trade_ips": trade_ips,
            "trade_ip_sources": trade_sources,
            "trade_ip_evidence": "journal_proven" if proven else "login_ip_fallback" if primary_ip else "unavailable",
            "trade_ip_changed": ip_changed,
            "trade_location": intel.get("location", ""),
            "trade_provider": intel.get("provider", ""),
            "trade_asn": intel.get("asn", ""),
            "trade_vps_status": intel.get("vps_status", "unknown"),
            "trade_vps_confidence": intel.get("vps_confidence", 0),
            "trade_vps_evidence": intel.get("vps_evidence", ["Execution IP unavailable"]),
        })
    return {
        "covered_trades": covered_trades,
        "ip_change_count": ip_change_count,
        "multi_ip_trades": multi_ip_trades,
        "inferred_trades": inferred_trades,
        "attributed_trades": covered_trades + inferred_trades,
    }


def scan_local_trade_journals(
    login: int, from_ts: int, to_ts: int, relevant_ids: set[int] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Search configured MT5 log files for real ticket/IP evidence."""
    roots = [Path(__file__).with_name("logs")]
    configured = os.getenv("MT5_SERVER_LOG_DIRS", "")
    roots.extend(Path(value.strip()) for value in configured.split(os.pathsep) if value.strip())
    identifiers = {str(login), *(str(value) for value in (relevant_ids or set()) if value)}
    start_day = datetime.fromtimestamp(from_ts, tz=timezone.utc).date()
    end_day = datetime.fromtimestamp(to_ts, tz=timezone.utc).date()
    rows: list[dict[str, Any]] = []
    scanned_files: list[str] = []
    seen_paths: set[Path] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            continue
        if resolved in seen_paths or not resolved.is_dir():
            continue
        seen_paths.add(resolved)
        for path in sorted(resolved.rglob("*.log"))[-200:]:
            try:
                if path.stat().st_size > 100 * 1024 * 1024:
                    continue
            except OSError:
                continue
            date_match = re.search(r"(20\d{6})", path.stem)
            if date_match:
                try:
                    file_day = datetime.strptime(date_match.group(1), "%Y%m%d").date()
                    if file_day < start_day or file_day > end_day:
                        continue
                except ValueError:
                    pass
            try:
                raw = path.read_bytes()
                if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or raw[:200].count(b"\x00") > 20:
                    text_data = raw.decode("utf-16", errors="ignore")
                else:
                    text_data = raw.decode("utf-8", errors="ignore")
            except OSError:
                continue
            scanned_files.append(str(path))
            for line_number, line in enumerate(text_data.splitlines(), start=1):
                ips = [ip for ip in _IPV4_PATTERN.findall(line) if _public_ip(ip)]
                if not ips:
                    continue
                line_numbers = set(re.findall(r"(?<![\d.])\d{4,20}(?![\d.])", line))
                if not line_numbers.intersection(identifiers):
                    continue
                rows.append({
                    "Time": 0, "Message": line, "Source": f"{path.name}:{line_number}",
                    "IP": ips[0], "source_kind": "local_mt5_log",
                })
                if len(rows) >= 10000:
                    return rows, scanned_files
    return rows, scanned_files


def _request_daily_records(api, group: str, from_ts: int, to_ts: int):
    if api is None:
        return [], "Not connected."

    candidates = [
        "DailyRequestLightByGroup",
        "DailyRequestByGroup",
        "DailyRequestByGroupNumPy",
        "DailyRequestNumPy",
        "DailyGetByGroupNumPy",
        "DailyGetNumPy",
        "DailyRequest",
    ]
    errors = []
    saw_empty = False

    for name in candidates:
        method = getattr(api, name, None)
        if method is None:
            continue

        try:
            try:
                resp = method(group, from_ts, to_ts)
            except TypeError:
                resp = method(from_ts, to_ts)
        except Exception as exc:
            errors.append(str(exc))
            continue

        if isinstance(resp, (bool, int)):
            if resp not in (0, True):
                errors.append(f"{name} returned code: {resp}")
                continue

            for cls_name in ("DailyArray", "MTDailyArray"):
                if not hasattr(MT5Manager, cls_name):
                    continue
                try:
                    container = getattr(MT5Manager, cls_name)()
                    try:
                        ok = method(group, from_ts, to_ts, container)
                    except TypeError:
                        ok = method(from_ts, to_ts, container)
                    if bool(ok):
                        return _as_list(container), None
                    errors.append(_last_error_text())
                except Exception as exc:
                    errors.append(str(exc))

            saw_empty = True
            continue

        data, err = _response_to_data(resp, f"{name}({group})")
        if err:
            errors.append(err)
            continue

        rows = _as_list(data)
        if rows:
            return rows, None
        saw_empty = True

    if saw_empty and not errors:
        return [], None
    return [], "; ".join(errors[-3:]) if errors else "Daily request failed."


def request_daily_by_group(api, group, from_ts: int, to_ts: int):
    return _request_daily_records(api, group, from_ts, to_ts)


def request_daily_reports(api, from_ts: int, to_ts: int):
    return _request_daily_records(api, "*", from_ts, to_ts)


def _daily_equity_value(record: Any) -> float:
    """Return full EOD equity using the official MT5 Daily report mapping."""
    explicit_equity = _value(record, "Equity", "equity")
    if explicit_equity is not None:
        return _to_float(explicit_equity, 0.0)

    # MetaQuotes' ready-made Daily report maps its Equity column directly to
    # IMTDaily::ProfitEquity (Trades.Standard.Reports/Reports/Daily.cpp).
    return _to_float(_value(record, "ProfitEquity", "profitequity", "profit_equity"), 0.0)


def daily_to_dicts(records) -> list[dict[str, Any]]:
    if isinstance(records, pd.DataFrame):
        iterable = records.to_dict("records")
    elif records is None:
        iterable = []
    else:
        iterable = records

    rows: list[dict[str, Any]] = []
    for record in iterable:
        login = _to_int(_value(record, "Login", "login"), None)
        if login is None:
            continue

        rows.append(
            {
                "Login": login,
                "Datetime": _value(record, "Datetime", "datetime"),
                "Equity": _daily_equity_value(record),
                "ProfitEquity": _to_float(_value(record, "ProfitEquity", "profitequity", "Equity", "equity"), 0.0),
                "Balance": _to_float(_value(record, "Balance", "balance"), 0.0),
                "Credit": _to_float(_value(record, "Credit", "credit"), 0.0),
                "DailyBalance": _to_float(_value(record, "DailyBalance", "dailybalance"), 0.0),
                "DailyCredit": _to_float(_value(record, "DailyCredit", "dailycredit"), 0.0),
                "DailyBonus": _to_float(_value(record, "DailyBonus", "dailybonus"), 0.0),
            }
        )
    return rows


def _previous_trading_day(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day - timedelta(days=2)
    return day


def _resolve_analytics_period(period: str) -> tuple[date, date]:
    normalized = str(period or "").strip()
    if normalized not in ANALYTICS_PERIOD_OPTIONS:
        raise ValueError(
            f"Invalid period '{period}'. Supported values: {', '.join(ANALYTICS_PERIOD_OPTIONS)}"
        )

    today = datetime.now(timezone.utc).date()

    if normalized == "Today":
        return today, today
    if normalized == "Yesterday":
        yday = today - timedelta(days=1)
        return yday, yday

    if normalized == "This Week":
        start = today - timedelta(days=today.weekday())
        return start, today
    if normalized == "Last Week":
        start_this_week = today - timedelta(days=today.weekday())
        start = start_this_week - timedelta(days=7)
        end = start_this_week - timedelta(days=1)
        return start, end

    if normalized == "This Month":
        start = today.replace(day=1)
        return start, today
    if normalized == "Last Month":
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        start = last_month_end.replace(day=1)
        return start, last_month_end

    if normalized == "This Quarter":
        quarter = (today.month - 1) // 3 + 1
        start_month = 1 + (quarter - 1) * 3
        start = date(today.year, start_month, 1)
        return start, today
    if normalized == "Last Quarter":
        quarter = (today.month - 1) // 3 + 1
        start_month = 1 + (quarter - 1) * 3
        this_quarter_start = date(today.year, start_month, 1)
        last_quarter_end = this_quarter_start - timedelta(days=1)
        last_quarter_start_month = 1 + ((last_quarter_end.month - 1) // 3) * 3
        last_quarter_start = date(last_quarter_end.year, last_quarter_start_month, 1)
        return last_quarter_start, last_quarter_end

    return today, today


def _resolve_analytics_date_window(req: AnalyticsRequest) -> tuple[date, date, date, date]:
    if req.opening_equity_date is not None or req.closing_equity_date is not None:
        if req.opening_equity_date is None or req.closing_equity_date is None:
            raise ValueError("opening_equity_date and closing_equity_date must be provided together.")
        if req.opening_equity_date > req.closing_equity_date:
            raise ValueError("opening_equity_date must be on or before closing_equity_date.")

        summary_from = req.date_from or (req.opening_equity_date + timedelta(days=1))
        summary_to = req.date_to or req.closing_equity_date
        return req.opening_equity_date, req.closing_equity_date, summary_from, summary_to

    if req.period:
        start_date, end_date = _resolve_analytics_period(req.period)
        opening_equity_date = _previous_trading_day(start_date)
        closing_equity_date = _previous_trading_day(end_date)
        summary_from = _previous_trading_day(start_date) + timedelta(days=1)
        summary_to = _previous_trading_day(end_date)
        if summary_from > summary_to:
            summary_from = summary_to
        return opening_equity_date, closing_equity_date, summary_from, summary_to

    raise ValueError(
        "Provide opening_equity_date and closing_equity_date, or include period for legacy requests."
    )


def _row_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value

    ts = _to_int(value, None)
    if ts is not None:
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date()
        except Exception:
            pass

    try:
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.notna(parsed):
            return parsed.to_pydatetime().date()
    except Exception:
        pass

    return None


def _date_range_inclusive(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def build_daily_summary_display(
    summary_rows: list[dict[str, Any]],
    summary_start: date,
    summary_end: date,
) -> pd.DataFrame:
    columns = [
        "Date",
        "Opening Equity",
        "Closing Equity",
        "Deposit",
        "Withdraw",
        "Credit",
        "Additional",
        "Broker PnL",
        "Net PnL %",
    ]
    if not summary_rows:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(summary_rows).copy()
    if df.empty:
        return pd.DataFrame(columns=columns)

    if "Datetime" not in df.columns and "datetime" in df.columns:
        df["Datetime"] = df["datetime"]
    if "Datetime" not in df.columns and "Time" in df.columns:
        df["Datetime"] = df["Time"]
    if "Datetime" not in df.columns and "time" in df.columns:
        df["Datetime"] = df["time"]

    df["Date"] = df["Datetime"].apply(_row_date)
    if "Equity" not in df.columns:
        df["Equity"] = df.apply(_daily_equity_value, axis=1)

    for column in ("Equity", "DailyBalance", "DailyCredit", "DailyBonus"):
        if column not in df.columns:
            df[column] = 0.0
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)

    df = df.dropna(subset=["Date"]).copy()
    df = df[df["Date"] <= summary_end]
    if df.empty:
        return pd.DataFrame(columns=columns)

    equity_map = df.groupby("Date")["Equity"].sum().to_dict()
    summary_df = df[(df["Date"] >= summary_start) & (df["Date"] <= summary_end)].copy()
    rows: list[dict[str, Any]] = []

    for day in _date_range_inclusive(summary_start, summary_end):
        prev_day = _previous_trading_day(day - timedelta(days=1))
        day_rows = summary_df[summary_df["Date"] == day]

        opening_equity = float(equity_map.get(prev_day, 0.0))
        closing_equity = float(equity_map.get(day, 0.0))
        deposit = float(day_rows["DailyBalance"].clip(lower=0.0).sum()) if not day_rows.empty else 0.0
        withdraw = abs(float(day_rows["DailyBalance"].clip(upper=0.0).sum())) if not day_rows.empty else 0.0
        credit = float(day_rows["DailyCredit"].sum()) if not day_rows.empty else 0.0
        additional = float(day_rows["DailyBonus"].sum()) if not day_rows.empty else 0.0
        broker_pnl = closing_equity - opening_equity - deposit - credit - additional + withdraw
        pnl_pct = (broker_pnl / opening_equity * 100.0) if opening_equity > 0 else 0.0

        rows.append(
            {
                "Date": day.isoformat(),
                "Opening Equity": round(opening_equity, 2),
                "Closing Equity": round(closing_equity, 2),
                "Deposit": round(deposit, 2),
                "Withdraw": round(withdraw, 2),
                "Credit": round(credit, 2),
                "Additional": round(additional, 2),
                "Broker PnL": round(broker_pnl, 2),
                "Net PnL %": round(pnl_pct, 2),
            }
        )

    return pd.DataFrame(rows, columns=columns)


def request_account(api, login: int):
    if api is None:
        return None, "Not connected."
    method = getattr(api, "UserAccountGet", None)
    if method is None:
        return None, "UserAccountGet is not available in this wrapper."

    errors = []
    try:
        resp = method(login)
    except Exception as exc:
        errors.append(str(exc))
    else:
        data, err = _response_to_data(resp, f"UserAccountGet({login})")
        if not err:
            if isinstance(data, list):
                return (data[0] if data else None), None
            return data, None
        errors.append(err)

    for cls_name in ("UserAccount", "MTAccount"):
        if not hasattr(MT5Manager, cls_name):
            continue
        try:
            account_obj = getattr(MT5Manager, cls_name)()
            ok = method(login, account_obj)
            if bool(ok):
                return account_obj, None
            errors.append(_last_error_text())
        except Exception as exc:
            errors.append(str(exc))

    return None, "; ".join(errors[-3:]) if errors else "Account request failed."


def _date_bounds_utc(day: date) -> tuple[int, int]:
    start_dt = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(day, time.max, tzinfo=timezone.utc)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def _date_range_utc(date_from: date, date_to: date) -> tuple[date, date, int, int]:
    start_date = date_from
    end_date = date_to
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    from_ts = int(datetime.combine(start_date, time.min, tzinfo=timezone.utc).timestamp())
    to_ts = int(datetime.combine(end_date, time.max, tzinfo=timezone.utc).timestamp())
    return start_date, end_date, from_ts, to_ts


def _login_from_record(record: Any) -> int | None:
    return _to_int(_value(record, "Login", "login"), None)


def _ip_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            return ""
    text = str(value).strip()
    try:
        # The cache serializes SDK integer IPs to strings. Normalize that
        # representation as well as dotted/IPv6 addresses before enrichment.
        return str(ipaddress.ip_address(int(text) if text.isdecimal() else text))
    except ValueError:
        return ""


_IP_INTEL_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_IP_INTEL_LOCK = threading.Lock()
_IP_INTEL_NETWORK_LOCK = threading.Lock()
_IP_INTEL_REFRESHING: set[str] = set()
_IP_INTEL_TTL_SECONDS = 24 * 60 * 60
_IP_INTEL_FAILURE_TTL_SECONDS = 10 * 60
_IP_INTEL_BACKOFF_UNTIL = 0.0
_DATACENTER_ASNS = {
    "AS16509", "AS14618", "AS8075", "AS15169", "AS14061", "AS16276",
    "AS24940", "AS20473", "AS63949", "AS13335", "AS31898", "AS45102",
    "AS37963", "AS36351", "AS46606", "AS26496", "AS12876", "AS51167",
}
_HOSTING_TERMS = {
    "amazon", "aws", "azure", "google cloud", "digitalocean", "linode", "vultr",
    "ovh", "hetzner", "contabo", "scaleway", "alibaba cloud", "tencent cloud",
    "oracle cloud", "ibm cloud", "datacenter", "data center", "hosting", "vps",
    "server farm", "cloud services", "colocation",
}


def _public_ip(ip: str) -> bool:
    try:
        parsed = ipaddress.ip_address(str(ip).strip())
        return not (parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_multicast or parsed.is_reserved)
    except ValueError:
        return False


def _geo_coordinate(value: Any, limit: float) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    number = _to_float(value, None)
    return number if number is not None and math.isfinite(number) and abs(number) <= limit else None


def _normalize_ip_intel(ip: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw or raw.get("status") != "success":
        return {"ip": ip, "status": "unknown", "vps_status": "unknown", "vps_confidence": 0, "vps_evidence": ["Provider lookup unavailable"]}
    as_text = str(raw.get("as") or "").strip()
    asn = as_text.split(" ", 1)[0].upper() if as_text.upper().startswith("AS") else ""
    provider = str(raw.get("isp") or raw.get("org") or raw.get("asname") or "").strip()
    haystack = " ".join(str(raw.get(key) or "") for key in ("isp", "org", "asname", "as")).lower()
    evidence: list[str] = []
    confidence = 0
    if bool(raw.get("hosting")):
        evidence.append("Hosting/datacenter range")
        confidence = max(confidence, 95)
    if bool(raw.get("proxy")):
        evidence.append("Proxy or VPN range")
        confidence = max(confidence, 90)
    if asn in _DATACENTER_ASNS:
        evidence.append(f"Known datacenter ASN {asn}")
        confidence = max(confidence, 85)
    matched_term = next((term for term in _HOSTING_TERMS if term in haystack), "")
    if matched_term:
        evidence.append(f"Provider classified as {matched_term}")
        confidence = max(confidence, 75)
    vps_status = "detected" if evidence else "not_detected"
    if not evidence:
        evidence = ["No hosting, proxy or datacenter evidence"]
    location_parts = [str(raw.get(key) or "").strip() for key in ("city", "regionName", "country")]
    return {
        "ip": ip,
        "status": "available",
        "country": str(raw.get("country") or ""),
        "country_code": str(raw.get("countryCode") or ""),
        "region": str(raw.get("regionName") or ""),
        "city": str(raw.get("city") or ""),
        "location": ", ".join(part for part in location_parts if part),
        "latitude": _geo_coordinate(raw.get("lat"), 90),
        "longitude": _geo_coordinate(raw.get("lon"), 180),
        "timezone": str(raw.get("timezone") or ""),
        "provider": provider,
        "organization": str(raw.get("org") or ""),
        "asn": asn,
        "mobile": bool(raw.get("mobile")),
        "proxy": bool(raw.get("proxy")),
        "hosting": bool(raw.get("hosting")),
        "vps_status": vps_status,
        "vps_confidence": confidence,
        "vps_evidence": evidence,
    }


def enrich_ip_addresses(ips: Any, *, allow_network: bool = True) -> dict[str, dict[str, Any]]:
    """Resolve public IP intelligence in cached batches to protect API rate limits."""
    global _IP_INTEL_BACKOFF_UNTIL
    now = datetime.now(timezone.utc).timestamp()
    normalized = sorted({str(ip).strip() for ip in ips if _public_ip(str(ip).strip())})
    results: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    with _IP_INTEL_LOCK:
        for ip in normalized:
            cached = _IP_INTEL_CACHE.get(ip)
            if cached and cached[0] > now:
                results[ip] = dict(cached[1])
            else:
                missing.append(ip)

    if allow_network and missing and os.getenv("IP_GEOLOCATION_ENABLED", "true").lower() not in {"0", "false", "no"}:
        # Multiple dashboard routes can request the same uncached addresses at
        # once. Serialize provider access, then recheck the cache/backoff after
        # acquiring the lock so those requests cannot create a 429 storm.
        with _IP_INTEL_NETWORK_LOCK:
            now = datetime.now(timezone.utc).timestamp()
            with _IP_INTEL_LOCK:
                still_missing = []
                for ip in missing:
                    cached = _IP_INTEL_CACHE.get(ip)
                    if cached and cached[0] > now:
                        results[ip] = dict(cached[1])
                    else:
                        still_missing.append(ip)
            missing = still_missing
            fields = "status,message,query,country,countryCode,regionName,city,lat,lon,timezone,isp,org,as,asname,mobile,proxy,hosting"
            if now >= _IP_INTEL_BACKOFF_UNTIL:
                for offset in range(0, len(missing), 100):
                    chunk = missing[offset : offset + 100]
                    raw_by_ip: dict[str, dict[str, Any]] = {}
                    provider_exhausted = False
                    try:
                        body = json.dumps(chunk).encode("utf-8")
                        request = urllib.request.Request(
                            f"http://ip-api.com/batch?fields={fields}",
                            data=body,
                            headers={"Content-Type": "application/json", "User-Agent": "MT5-Risk-Monitor/1.0"},
                            method="POST",
                        )
                        with urllib.request.urlopen(request, timeout=6) as response:
                            payload = json.loads(response.read().decode("utf-8"))
                            remaining = _to_int(response.headers.get("X-Rl"), 1) or 0
                            if remaining <= 0:
                                _IP_INTEL_BACKOFF_UNTIL = now + max(_to_int(response.headers.get("X-Ttl"), 60) or 60, 60)
                                provider_exhausted = True
                        raw_by_ip = {str(item.get("query") or ""): item for item in payload if isinstance(item, dict)}
                    except Exception as exc:
                        logger.warning("IP intelligence batch failed: %s", exc)
                        _IP_INTEL_BACKOFF_UNTIL = now + 60
                        provider_exhausted = True
                    with _IP_INTEL_LOCK:
                        for ip in chunk:
                            intel = _normalize_ip_intel(ip, raw_by_ip.get(ip))
                            ttl = _IP_INTEL_TTL_SECONDS if intel["status"] == "available" else _IP_INTEL_FAILURE_TTL_SECONDS
                            _IP_INTEL_CACHE[ip] = (now + ttl, intel)
                            results[ip] = dict(intel)
                    if provider_exhausted:
                        break

    for ip in missing:
        results.setdefault(ip, _normalize_ip_intel(ip, None))
    return results


def enrich_ip_addresses_nonblocking(ips: Any) -> dict[str, dict[str, Any]]:
    """Return cached network data now and warm missing addresses in background."""
    normalized = sorted({str(ip).strip() for ip in ips if _public_ip(str(ip).strip())})
    results = enrich_ip_addresses(normalized, allow_network=False)
    with _IP_INTEL_LOCK:
        pending = [
            ip for ip in normalized
            if results.get(ip, {}).get("status") != "available"
            and ip not in _IP_INTEL_REFRESHING
        ]
        _IP_INTEL_REFRESHING.update(pending)
    if pending:
        def warm_network_cache() -> None:
            try:
                enrich_ip_addresses(pending, allow_network=True)
            finally:
                with _IP_INTEL_LOCK:
                    _IP_INTEL_REFRESHING.difference_update(pending)

        threading.Thread(
            target=warm_network_cache,
            name="ip-intelligence-warm",
            daemon=True,
        ).start()
    return results


def _normalize_login_set(values: Any) -> set[int]:
    normalized: set[int] = set()
    for value in _as_list(values):
        login = _to_int(value, None)
        if login is not None and login > 0:
            normalized.add(login)
    return normalized


def _collect_user_scope(
    api,
    groups: list[str],
    excluded_logins: set[int] | None = None,
    request_groups: list[str] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[int, str], list[str]]:
    users_by_login: dict[int, dict[str, Any]] = {}
    group_map: dict[int, str] = {}
    warnings: list[str] = []
    allowed_groups = {str(group).strip() for group in groups if str(group).strip()}
    excluded_set = _normalize_login_set(excluded_logins)

    def _load_from_wildcard() -> bool:
        nonlocal users_by_login, group_map
        users, err = request_users_by_group(api, "*")
        if err:
            logger.warning("Wildcard user request failed; retrying per group. Error: %s", err)
            return False

        for user in users:
            group_name = str(_value(user, "Group", "group", default="") or "").strip()
            if group_name not in allowed_groups:
                continue

            login = _login_from_record(user)
            if login is None or login <= 0:
                continue
            if login in excluded_set:
                continue

            if login not in users_by_login:
                users_by_login[login] = {
                    "name": str(_value(user, "Name", "name", default="") or ""),
                    "currency": str(_value(user, "Currency", "currency", default="USD") or "USD"),
                    "last_ip": _ip_text(_value(user, "LastIP", "last_ip", "IP", "ip")),
                    "agent_login": _to_int(_value(user, "Agent", "agent"), 0) or 0,
                    "balance": _to_float(_value(user, "Balance", "balance"), 0.0),
                    "credit": _to_float(_value(user, "Credit", "credit"), 0.0),
                }
            group_map[login] = group_name

        return bool(users_by_login)

    def _add_users(users: list[Any]) -> None:
        for user in users:
            group_name = str(_value(user, "Group", "group", default="") or "").strip()
            if group_name not in allowed_groups:
                continue
            login = _login_from_record(user)
            if login is None or login <= 0 or login in excluded_set:
                continue
            if login not in users_by_login:
                users_by_login[login] = {
                    "name": str(_value(user, "Name", "name", default="") or ""),
                    "currency": str(_value(user, "Currency", "currency", default="USD") or "USD"),
                    "last_ip": _ip_text(_value(user, "LastIP", "last_ip", "IP", "ip")),
                    "agent_login": _to_int(_value(user, "Agent", "agent"), 0) or 0,
                    "balance": _to_float(_value(user, "Balance", "balance"), 0.0),
                    "credit": _to_float(_value(user, "Credit", "credit"), 0.0),
                }
            group_map[login] = group_name

    requested_groups = request_groups or groups
    for group in requested_groups:
        users, err = request_users_by_group(api, group)
        if err:
            warnings.append(f"{group}: {err}")
            continue
        _add_users(users)

    # Some Manager installations accept a branch wildcard but return an empty
    # user array for it.  A compressed UI branch must never be interpreted as
    # an empty book in that case: retry the selected leaf groups exactly.
    if (
        not users_by_login
        and not warnings
        and request_groups
        and set(request_groups) != set(groups)
    ):
        logger.info(
            "Compressed user scope returned no rows; retrying %s exact groups.",
            len(groups),
        )
        for group in groups:
            users, err = request_users_by_group(api, group)
            if err:
                warnings.append(f"{group}: {err}")
                continue
            _add_users(users)

    # MT5 wildcard responses are not consistent across manager installations and
    # may be incomplete for larger selections.  Always build the authoritative
    # scope from the exact groups, then use the wildcard only as a last-resort
    # compatibility fallback when every exact request returned no users.
    if not users_by_login and not warnings and allowed_groups:
        _load_from_wildcard()

    return users_by_login, group_map, warnings


def _filter_records_by_logins(records: Any, allowed_logins: set[int] | None) -> list[Any]:
    rows = _as_list(records)
    if allowed_logins is None:
        return rows
    return [row for row in rows if _login_from_record(row) in allowed_logins]


def _load_wildcard_deals(api, from_ts: int, to_ts: int, scoped_logins: set[int]) -> tuple[list[Any], str | None]:
    deals_all, err = request_deals_by_group(api, "*", from_ts, to_ts)
    if err:
        return [], err
    return _filter_records_by_logins(deals_all, scoped_logins), None


def _load_wildcard_daily(api, from_ts: int, to_ts: int, scoped_logins: set[int]) -> tuple[list[Any], str | None]:
    daily_all, err = request_daily_reports(api, from_ts, to_ts)
    if err:
        return [], err
    return _filter_records_by_logins(daily_all, scoped_logins), None


def _fetch_scoped_deals(
    api,
    groups: list[str],
    from_ts: int,
    to_ts: int,
    allowed_logins: set[int] | None = None,
    request_groups: list[str] | None = None,
) -> tuple[list[Any], list[str]]:
    warnings: list[str] = []
    scoped_logins = None if allowed_logins is None else _normalize_login_set(allowed_logins)

    if scoped_logins is not None and not scoped_logins:
        return [], warnings

    # A complete branch can contain tens of thousands of logins. In that case,
    # one MT5 wildcard group request is dramatically faster than hundreds of
    # 500-login history calls. The rows are still filtered against the exact
    # selected-login set below, so a broad Manager response cannot leak data
    # from neighbouring groups.
    if request_groups:
        deal_rows: list[Any] = []
        successful_requests = 0
        for group in request_groups:
            deals, err = request_deals_by_group(api, group, from_ts, to_ts)
            if err:
                warnings.append(f"{group}: {err}")
                continue
            successful_requests += 1
            deal_rows.extend(_filter_records_by_logins(deals, scoped_logins))
        if successful_requests:
            return deal_rows, warnings

    # Bulk login history is substantially faster than scanning dozens of
    # groups one at a time and avoids native group-request page limits.
    if scoped_logins is not None and getattr(api, "DealRequestByLogins", None) is not None:
        bulk_rows: list[Any] = []
        bulk_errors: list[str] = []
        ordered_logins = sorted(scoped_logins)
        for offset in range(0, len(ordered_logins), 500):
            rows, err = request_deals_by_logins(api, ordered_logins[offset : offset + 500], from_ts, to_ts)
            if err:
                bulk_errors.append(err)
                break
            bulk_rows.extend(_filter_records_by_logins(rows, scoped_logins))
        if bulk_rows and not bulk_errors:
            return bulk_rows, warnings
        if bulk_errors:
            logger.warning("Bulk login deal request failed; retrying by group. Error: %s", bulk_errors[-1])

    deal_rows: list[Any] = []
    for group in groups:
        deals, err = request_deals_by_group(api, group, from_ts, to_ts)
        if err:
            warnings.append(f"{group}: {err}")
            continue
        deal_rows.extend(_filter_records_by_logins(deals, scoped_logins))

    if deal_rows or warnings or scoped_logins is None:
        return deal_rows, warnings

    filtered, err = _load_wildcard_deals(api, from_ts, to_ts, scoped_logins)
    if err:
        logger.warning("Wildcard deal fallback failed after empty per-group results. Error: %s", err)
        return deal_rows, warnings
    return filtered, warnings


def _fetch_scoped_daily_records(
    api,
    groups: list[str],
    from_ts: int,
    to_ts: int,
    allowed_logins: set[int] | None = None,
) -> tuple[list[Any], list[str]]:
    warnings: list[str] = []
    scoped_logins = None if allowed_logins is None else _normalize_login_set(allowed_logins)

    if scoped_logins is not None and not scoped_logins:
        return [], warnings

    # The SDK accepts several groups as one comma-separated expression and the
    # lightweight call contains every field used by this report. This avoids
    # repeated full daily requests and duplicate/mismatched result sets.
    group_expression = ",".join(dict.fromkeys(str(group).strip() for group in groups if str(group).strip()))
    if group_expression:
        rows, err = request_daily_by_group(api, group_expression, from_ts, to_ts)
        if not err:
            filtered_rows = _filter_records_by_logins(rows, scoped_logins)
            if filtered_rows:
                return filtered_rows, warnings
            logger.info("Combined daily request returned no scoped rows; retrying per group.")
        else:
            logger.warning("Combined daily request failed; retrying per group. Error: %s", err)

    daily_rows: list[Any] = []
    for group in groups:
        rows, err = request_daily_by_group(api, group, from_ts, to_ts)
        if err:
            warnings.append(f"{group}: {err}")
            continue
        daily_rows.extend(_filter_records_by_logins(rows, scoped_logins))

    if daily_rows or warnings or scoped_logins is None:
        return daily_rows, warnings

    filtered, err = _load_wildcard_daily(api, from_ts, to_ts, scoped_logins)
    if err:
        logger.warning("Wildcard daily fallback failed after empty per-group results. Error: %s", err)
        return daily_rows, warnings
    return filtered, warnings


def request_positions_by_group(api_handle, group: str) -> tuple[list[Any], str | None]:
    errors: list[str] = []
    for method_name in ("PositionRequestByGroup", "PositionGetByGroup"):
        method = getattr(api_handle, method_name, None)
        if method is None:
            continue
        try:
            response = method(group)
        except Exception as exc:
            errors.append(str(exc))
            continue
        data, err = _response_to_data(response, f"{method_name}({group})")
        if not err:
            return _as_list(data), None
        errors.append(err)
    return [], "; ".join(errors[-3:]) if errors else "Position request failed."


def request_accounts_by_logins(api_handle, logins: set[int]) -> tuple[dict[int, Any], list[str]]:
    accounts: dict[int, Any] = {}
    warnings: list[str] = []
    ordered_logins = sorted(_normalize_login_set(logins))
    for method_name in ("UserAccountRequestByLogins", "UserAccountGetByLogins"):
        method = getattr(api_handle, method_name, None)
        if method is None:
            continue
        method_accounts: dict[int, Any] = {}
        method_warnings: list[str] = []
        for offset in range(0, len(ordered_logins), 500):
            chunk = ordered_logins[offset : offset + 500]
            try:
                response = method(chunk)
                data, err = _response_to_data(response, method_name)
            except Exception as exc:
                data, err = [], str(exc)
            if err:
                method_warnings.append(err)
                break
            for account in _as_list(data):
                login = _login_from_record(account)
                if login is not None:
                    method_accounts[login] = account
        if method_accounts:
            accounts.update(method_accounts)
            break
        warnings.extend(method_warnings)

    # A few Manager builds do not expose the batch call. Limit the compatibility
    # fallback to active accounts so a live refresh cannot issue thousands of calls.
    if not accounts and ordered_logins:
        for login in ordered_logins[:250]:
            account, err = request_account(api_handle, login)
            if account is not None:
                accounts[login] = account
            elif err:
                warnings.append(f"Login {login}: {err}")
    return accounts, _dedupe_warnings(warnings)


def request_accounts_by_groups(
    api_handle,
    groups: list[str],
    allowed_logins: set[int],
) -> tuple[dict[int, Any], list[str]]:
    """Prefer one bulk account request per group for interactive live risk."""
    allowed = _normalize_login_set(allowed_logins)
    if not allowed:
        return {}, []

    accounts: dict[int, Any] = {}
    warnings: list[str] = []
    bulk_supported = False
    for group in dict.fromkeys(str(group).strip() for group in groups if str(group).strip()):
        group_succeeded = False
        for method_name in ("UserAccountGetByGroup", "UserAccountRequestByGroup"):
            method = getattr(api_handle, method_name, None)
            if method is None:
                continue
            bulk_supported = True
            try:
                response = method(group)
                data, err = _response_to_data(response, f"{method_name}({group})")
            except Exception as exc:
                data, err = [], str(exc)
            if err:
                warnings.append(f"{group}: {err}")
                continue
            for account in _as_list(data):
                login = _login_from_record(account)
                if login in allowed:
                    accounts[login] = account
            group_succeeded = True
            break
        if not group_succeeded and bulk_supported:
            logger.warning("Bulk account request failed for group %s", group)

    if accounts:
        return accounts, _dedupe_warnings(warnings)

    # Compatibility path for wrappers that do not offer group account arrays.
    fallback_accounts, fallback_warnings = request_accounts_by_logins(api_handle, allowed)
    return fallback_accounts, _dedupe_warnings(warnings, fallback_warnings)


def _position_action(position: Any) -> str:
    action = _to_int(_value(position, "Action", "action"), -1)
    if action == 0:
        return "BUY"
    if action == 1:
        return "SELL"
    return str(_value(position, "Type", "type", default="UNKNOWN") or "UNKNOWN").upper()


def _position_volume_lots(position: Any) -> float:
    extended = _value(position, "VolumeExt", "volume_ext", default=None)
    if extended is not None and _to_float(extended, 0.0) > 0:
        return _to_float(extended, 0.0) / 100000000.0
    raw = _to_float(_value(position, "Volume", "volume", "VolumeExt", "volume_ext"), 0.0)
    return raw / 10000.0 if abs(raw) >= 100.0 else raw


def build_live_risk_snapshot(
    api_handle,
    groups: list[str],
    excluded_logins: list[int] | None = None,
    request_groups: list[str] | None = None,
    row_limit: int | None = None,
    include_login: int | None = None,
    stable_order: bool = False,
) -> dict[str, Any]:
    excluded = _normalize_login_set(excluded_logins)
    users_by_login, group_map, warnings = _collect_user_scope(
        api_handle, groups, excluded, request_groups=request_groups
    )
    allowed_logins = set(users_by_login)

    positions: list[Any] = []
    seen_tickets: set[int] = set()
    for group in request_groups or groups:
        group_positions, err = request_positions_by_group(api_handle, group)
        if err:
            warnings.append(f"{group}: {err}")
            continue
        for position in group_positions:
            login = _login_from_record(position)
            if login is None or login not in allowed_logins:
                continue
            ticket = _to_int(_value(position, "Position", "position", "Ticket", "ticket"), 0) or 0
            if ticket and ticket in seen_tickets:
                continue
            if ticket:
                seen_tickets.add(ticket)
            positions.append(position)

    active_logins = {_login_from_record(position) for position in positions}
    active_logins = {login for login in active_logins if login is not None}
    # Equity is an account-scope measure, not a position-scope measure. Request
    # every scoped account so changing managers cannot silently drop clients
    # that currently have no open positions from total equity/free margin.
    bulk_account_supported = any(
        callable(getattr(api_handle, name, None))
        for name in ("UserAccountGetByGroup", "UserAccountRequestByGroup")
    )
    if bulk_account_supported:
        accounts, account_warnings = request_accounts_by_groups(
            api_handle, request_groups or groups, allowed_logins
        )
    else:
        accounts, account_warnings = request_accounts_by_logins(api_handle, active_logins | ({include_login} & allowed_logins if include_login else set()))
    warnings.extend(account_warnings)
    missing_accounts = active_logins.difference(accounts)
    if missing_accounts:
        warnings.append(
            f"Live account metrics unavailable for {len(missing_accounts)} of "
            f"{len(active_logins)} active accounts."
        )
    missing_scoped_accounts = allowed_logins.difference(accounts)
    if missing_scoped_accounts:
        warnings.append(
            f"Authoritative account metrics unavailable for {len(missing_scoped_accounts)} "
            f"of {len(allowed_logins)} scoped accounts; incomplete money totals "
            "are withheld until authoritative MT5 metrics arrive."
        )

    # Portfolio amounts require complete, finite SDK observations. An inactive
    # account without account metrics cannot be assigned balance-plus-credit
    # equity, zero floating P&L or zero margin.
    def observed_account_value(account, *fields):
        value = _value(account, *fields, default=None)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return value

    def observed_account_total(*fields):
        if missing_scoped_accounts:
            return None
        values = [observed_account_value(account, *fields) for account in accounts.values()]
        if any(value is None for value in values):
            return None
        try:
            return round(math.fsum(values), 2)
        except OverflowError:
            return None

    portfolio_totals = {
        "floating_pnl": observed_account_total("Profit", "Floating", "profit", "floating"),
        "equity": observed_account_total("Equity", "equity"),
        "margin": observed_account_total("Margin", "margin"),
        "margin_free": observed_account_total("MarginFree", "FreeMargin", "margin_free"),
    }
    if any(value is None for value in portfolio_totals.values()):
        warnings.append("Incomplete MT5 account metrics: unavailable money totals are withheld, never estimated.")

    normalized_positions: list[dict[str, Any]] = []
    by_login: dict[int, list[dict[str, Any]]] = defaultdict(list)
    symbol_totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"symbol": "", "buy_lots": 0.0, "sell_lots": 0.0, "gross_lots": 0.0, "floating_pnl": 0.0, "positions": 0}
    )
    now_ts = int(datetime.now(timezone.utc).timestamp())
    for position in positions:
        login = _login_from_record(position)
        if login is None:
            continue
        symbol = str(_value(position, "Symbol", "symbol", default="Unknown") or "Unknown")
        action = _position_action(position)
        lots = max(_position_volume_lots(position), 0.0)
        profit = _to_float(_value(position, "Profit", "profit"), 0.0)
        swap = _to_float(_value(position, "Storage", "storage", "Swap", "swap"), 0.0)
        opened_at = _to_int(_value(position, "TimeCreate", "time_create", "Time", "time"), 0) or 0
        row = {
            "ticket": str(_to_int(_value(position, "Position", "position", "Ticket", "ticket"), 0) or 0),
            "login": login,
            "name": users_by_login.get(login, {}).get("name", ""),
            "group": group_map.get(login, ""),
            "currency": users_by_login.get(login, {}).get("currency", ""),
            "symbol": symbol,
            "action": action,
            "volume": round(lots, 8),
            "price_open": round(_to_float(_value(position, "PriceOpen", "price_open"), 0.0), 8),
            "price_current": round(_to_float(_value(position, "PriceCurrent", "price_current"), 0.0), 8),
            "profit": round(profit + swap, 2),
            "opened_at": datetime.fromtimestamp(opened_at, tz=timezone.utc).isoformat() if opened_at else "",
            "age_seconds": max(now_ts - opened_at, 0) if opened_at else 0,
        }
        normalized_positions.append(row)
        by_login[login].append(row)
        aggregate = symbol_totals[symbol]
        aggregate["symbol"] = symbol
        aggregate["buy_lots" if action == "BUY" else "sell_lots"] += lots
        aggregate["gross_lots"] += lots
        aggregate["floating_pnl"] += profit + swap
        aggregate["positions"] += 1

    account_rows: list[dict[str, Any]] = []
    severity_order = {"critical": 0, "high": 1, "elevated": 2, "normal": 3}
    active_ip_counts = Counter(
        users_by_login.get(login, {}).get("last_ip", "")
        for login in by_login
        if users_by_login.get(login, {}).get("last_ip", "")
    )
    lookup_ips = [
        ip for ip, _ in active_ip_counts.most_common(LIVE_RISK_IP_LOOKUP_LIMIT)
    ]
    network_by_ip = enrich_ip_addresses_nonblocking(lookup_ips)
    for login in sorted((active_logins | ({include_login} if include_login in allowed_logins else set())).intersection(accounts)):
        login_positions = by_login.get(login, [])
        account = accounts.get(login)
        balance = _to_float(_value(account, "Balance", "balance"), 0.0)
        credit = _to_float(_value(account, "Credit", "credit"), 0.0)
        equity = _to_float(_value(account, "Equity", "equity"), 0.0)
        margin = _to_float(_value(account, "Margin", "margin"), 0.0)
        margin_free = _to_float(_value(account, "MarginFree", "FreeMargin", "margin_free"), 0.0)
        margin_level = _to_float(_value(account, "MarginLevel", "margin_level"), 0.0)
        floating = _to_float(_value(account, "Profit", "Floating", "profit", "floating"), 0.0)
        total_lots = math.fsum(row["volume"] for row in login_positions)
        symbol_lots: dict[str, float] = defaultdict(float)
        symbol_actions: dict[str, set[str]] = defaultdict(set)
        for row in login_positions:
            symbol_lots[row["symbol"]] += row["volume"]
            symbol_actions[row["symbol"]].add(row["action"])
        concentration = (max(symbol_lots.values()) / total_lots * 100.0) if total_lots > 0 else 0.0

        sorted_positions = sorted(login_positions, key=lambda row: row["opened_at"])
        burst = any(
            left["opened_at"] and right["opened_at"] and abs(right["age_seconds"] - left["age_seconds"]) <= 2
            for left, right in zip(sorted_positions, sorted_positions[1:])
        )
        hedge = any(len(actions) > 1 for actions in symbol_actions.values())
        grid = any(sum(1 for row in login_positions if row["symbol"] == symbol) >= 3 for symbol in symbol_lots)
        martingale = False
        for symbol in symbol_lots:
            symbol_rows = [row for row in sorted_positions if row["symbol"] == symbol]
            if any(current["volume"] > previous["volume"] * 1.5 for previous, current in zip(symbol_rows, symbol_rows[1:])):
                martingale = True
                break

        signals: list[str] = []
        score = 0
        last_ip = users_by_login.get(login, {}).get("last_ip", "")
        shared_ip_accounts = active_ip_counts.get(last_ip, 0) if last_ip else 0
        network = network_by_ip.get(last_ip, {})
        if login_positions and equity <= 0:
            signals.append("Non-positive equity")
            score += 35
        if login_positions and margin_level and margin_level < 100:
            signals.append("Stop-out proximity")
            score += 40
        elif login_positions and margin_level and margin_level < 150:
            signals.append("Low margin level")
            score += 25
        loss_ratio = abs(min(floating, 0.0)) / max(abs(balance), 1.0) * 100.0
        if loss_ratio >= 20:
            signals.append("Floating loss >20% balance")
            score += 30
        elif loss_ratio >= 10:
            signals.append("Floating loss >10% balance")
            score += 15
        profit_multiple = (floating / max(balance, 1.0)) if balance > 0 and floating > 0 else 0.0
        if balance > 0 and balance <= SMALL_BALANCE_ALERT_THRESHOLD and floating >= max(OUTSIZED_PROFIT_MIN_USD, balance * OUTSIZED_PROFIT_MULTIPLE):
            signals.append("Small balance, outsized floating profit")
            score += 25
        if concentration >= 70:
            signals.append("Symbol concentration")
            score += 15
        if len(login_positions) >= 20:
            signals.append("High open-position count")
            score += 15
        largest_position_lots = max((row["volume"] for row in login_positions), default=0.0)
        if largest_position_lots >= HIGH_LOT_ALERT_THRESHOLD:
            signals.append(f"Large position ({largest_position_lots:g} lots)")
            score += 20
        if grid:
            signals.append("Grid-like exposure")
            score += 15
        if martingale:
            signals.append("Volume escalation")
            score += 20
        if burst:
            signals.append("Burst execution")
            score += 15
        if hedge:
            signals.append("Opposing symbol exposure")
            score += 10
        if shared_ip_accounts > 1:
            signals.append(f"Shared login IP ({shared_ip_accounts} accounts)")
            score += 15
        if network.get("vps_status") == "detected":
            signals.append("VPS / hosting network detected")
            score += 15
        score = min(score, 100)
        severity = "critical" if score >= 70 else "high" if score >= 50 else "elevated" if score >= 25 else "normal"
        account_rows.append({
            "login": login,
            "name": users_by_login.get(login, {}).get("name", ""),
            "group": group_map.get(login, ""),
            "currency": users_by_login.get(login, {}).get("currency", ""),
            "balance": observed_account_value(account, "Balance", "balance"),
            "credit": observed_account_value(account, "Credit", "credit"),
            "equity": observed_account_value(account, "Equity", "equity"),
            "margin": observed_account_value(account, "Margin", "margin"),
            "margin_free": observed_account_value(account, "MarginFree", "FreeMargin", "margin_free"),
            "margin_level": observed_account_value(account, "MarginLevel", "margin_level"),
            "floating_pnl": observed_account_value(account, "Profit", "Floating", "profit", "floating"),
            "profit_multiple": round(profit_multiple, 2),
            "positions": len(login_positions),
            "gross_lots": round(total_lots, 8),
            "concentration_pct": round(concentration, 1),
            "risk_score": score,
            "severity": severity,
            "signals": signals,
            "last_ip": last_ip,
            "shared_ip_accounts": shared_ip_accounts,
            "vps_status": network.get("vps_status", "unknown"),
            "vps_confidence": network.get("vps_confidence", 0),
            "vps_evidence": network.get("vps_evidence", ["Provider lookup unavailable"]),
            "location": network.get("location", ""),
            "country": network.get("country", ""),
            "country_code": network.get("country_code", ""),
            "city": network.get("city", ""),
            "latitude": network.get("latitude"),
            "longitude": network.get("longitude"),
            "provider": network.get("provider", ""),
            "asn": network.get("asn", ""),
            "proxy": network.get("proxy", False),
            "hosting": network.get("hosting", False),
            "active": bool(login_positions),
        })

    account_rows.sort(key=(lambda row: row["login"]) if stable_order else (lambda row: (severity_order[row["severity"]], -row["risk_score"], _to_float(row["floating_pnl"], 0.0))))
    high_lot_positions = sorted(
        (row for row in normalized_positions if row["volume"] >= HIGH_LOT_ALERT_THRESHOLD),
        key=(lambda row: int(row["ticket"])) if stable_order else (lambda row: (-row["volume"], row["profit"])),
    )
    normalized_positions.sort(key=(lambda row: int(row["ticket"])) if stable_order else (lambda row: row["profit"]))
    symbols = []
    for aggregate in symbol_totals.values():
        symbols.append({
            **aggregate,
            "buy_lots": round(aggregate["buy_lots"], 8),
            "sell_lots": round(aggregate["sell_lots"], 8),
            "net_lots": round(aggregate["buy_lots"] - aggregate["sell_lots"], 8),
            "gross_lots": round(aggregate["gross_lots"], 8),
            "floating_pnl": round(aggregate["floating_pnl"], 2),
        })
    symbols.sort(key=lambda row: row["gross_lots"], reverse=True)

    map_points = []
    for ip, count in active_ip_counts.items():
        intel = network_by_ip.get(ip, {})
        latitude = intel.get("latitude")
        longitude = intel.get("longitude")
        if intel.get("status") != "available" or latitude is None or longitude is None:
            continue
        map_points.append({**intel, "accounts": count})
    map_points.sort(key=lambda row: (-row["accounts"], row.get("country", ""), row["ip"]))
    country_counts = Counter(row.get("country") or "Unknown" for row in map_points)
    provider_counts = Counter(row.get("provider") or "Unknown" for row in map_points)
    shared_clusters = [
        {"ip": ip, "accounts": count, **network_by_ip.get(ip, {})}
        for ip, count in active_ip_counts.items() if count > 1
    ]
    shared_clusters.sort(key=lambda row: (-row["accounts"], row["ip"]))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected_groups": groups,
        "summary": {
            "scoped_accounts": len(allowed_logins),
            "currencies": sorted({str(user.get("currency") or "UNKNOWN") for user in users_by_login.values()}),
            "balance": observed_account_total("Balance", "balance"),
            "buy_lots": round(math.fsum(row["volume"] for row in normalized_positions if row["action"] == "BUY"), 8),
            "sell_lots": round(math.fsum(row["volume"] for row in normalized_positions if row["action"] == "SELL"), 8),
            "authoritative_accounts": len(accounts),
            "estimated_accounts": len(missing_scoped_accounts),
            "active_accounts": len(active_logins),
            "open_positions": len(normalized_positions),
            "gross_lots": round(math.fsum(row["volume"] for row in normalized_positions), 8),
            "net_lots": round(math.fsum(row["volume"] * (1 if row["action"] == "BUY" else -1 if row["action"] == "SELL" else 0) for row in normalized_positions), 8),
            "high_lot_threshold": HIGH_LOT_ALERT_THRESHOLD,
            "large_positions": len(high_lot_positions),
            "floating_pnl": portfolio_totals["floating_pnl"],
            "equity": portfolio_totals["equity"],
            "margin": portfolio_totals["margin"],
            "margin_free": portfolio_totals["margin_free"],
            "critical_accounts": sum(row["severity"] == "critical" for row in account_rows),
            "flagged_accounts": sum(row["severity"] != "normal" for row in account_rows),
            "vps_accounts": sum(row["vps_status"] == "detected" for row in account_rows),
            "located_ips": len(map_points),
            "countries": len(country_counts),
        },
        "accounts": account_rows[:row_limit],
        "symbols": symbols[:100 if row_limit else None],
        "positions": normalized_positions[:row_limit],
        "large_positions": high_lot_positions[:100 if row_limit else None],
        "network": {
            "map_points": map_points,
            "countries": [{"country": name, "ips": count} for name, count in country_counts.most_common()],
            "providers": [{"provider": name, "ips": count} for name, count in provider_counts.most_common(20 if row_limit is not None else None)],
            "shared_clusters": shared_clusters[:100 if row_limit is not None else None],
            "lookup_provider": "ip-api.com",
        },
        "warnings": _dedupe_warnings(
            warnings,
            [f"Showing {row_limit} of {len(account_rows)} active accounts; totals include the full scope."] if row_limit is not None and len(account_rows) > row_limit else [],
            [f"Showing {row_limit} of {len(normalized_positions)} positions; totals include the full scope."] if row_limit is not None and len(normalized_positions) > row_limit else [],
        ),
    }


def build_live_risk_snapshot_from_cache(
    users: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    groups: list[str],
    excluded_logins: list[int] | None = None,
    row_limit: int | None = None,
    include_login: int | None = None,
    stable_order: bool = False,
) -> dict[str, Any]:
    excluded = _normalize_login_set(excluded_logins)
    group_set = {str(group).strip() for group in groups if str(group).strip()}
    scoped_users = [
        row for row in users
        if str(row.get("Group", "")).strip() in group_set
        and _to_int(row.get("Login"), 0) not in excluded
    ]
    allowed_logins = {_to_int(row.get("Login"), 0) for row in scoped_users}
    positions = [row for row in positions if _to_int(row.get("Login"), 0) in allowed_logins]
    detail_logins = {_to_int(row.get("Login"), 0) for row in positions}
    if include_login in allowed_logins:
        detail_logins.add(include_login)
    detail_users = [row for row in scoped_users if _to_int(row.get("Login"), 0) in detail_logins]
    users_by_login = {
        _to_int(row.get("Login"), 0): {
            "name": str(row.get("Name", "") or ""),
            "currency": str(row.get("Currency", "") or ""),
            "last_ip": _ip_text(row.get("LastIP", "")),
        }
        for row in detail_users
        if _to_int(row.get("Login"), 0) > 0
    }
    group_map = {
        _to_int(row.get("Login"), 0): str(row.get("Group", "") or "")
        for row in detail_users
        if _to_int(row.get("Login"), 0) > 0
    }

    normalized_positions: list[dict[str, Any]] = []
    by_login: dict[int, list[dict[str, Any]]] = defaultdict(list)
    symbol_totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"symbol": "", "buy_lots": 0.0, "sell_lots": 0.0, "gross_lots": 0.0, "floating_pnl": 0.0, "positions": 0}
    )
    now_ts = int(datetime.now(timezone.utc).timestamp())
    for position in positions:
        login = _to_int(position.get("Login"), 0)
        symbol = str(position.get("Symbol", "") or "Unknown")
        action = "BUY" if _to_int(position.get("Action"), -1) == 0 else "SELL" if _to_int(position.get("Action"), -1) == 1 else "UNKNOWN"
        lots = max(_to_float(position.get("Volume"), 0.0), 0.0)
        contract_size = _to_float(position.get("ContractSize"), 0.0)
        current_price = _to_float(position.get("PriceCurrent"), 0.0)
        rate_profit = _to_float(position.get("RateProfit"), 0.0)
        notional = abs(lots * contract_size * current_price * rate_profit) if position.get("CalcMode") in (0, 2, 4, 5, 32, 38) and contract_size > 0 and current_price > 0 and rate_profit > 0 else None
        profit = _to_float(position.get("Profit"), 0.0) + _to_float(position.get("Swap"), 0.0)
        opened_at = _to_int(position.get("TimeCreate") or position.get("Time"), 0) or 0
        row = {
            "ticket": str(_to_int(position.get("Ticket"), 0) or 0),
            "login": login,
            "name": users_by_login.get(login, {}).get("name", ""),
            "group": group_map.get(login, ""),
            "currency": users_by_login.get(login, {}).get("currency", ""),
            "symbol": symbol,
            "action": action,
            "volume": round(lots, 8),
            "contract_size": contract_size if contract_size > 0 else None,
            "notional": round(notional, 2) if notional is not None else None,
            "price_open": round(_to_float(position.get("PriceOpen"), 0.0), 8),
            "price_current": round(current_price, 8),
            "profit": round(profit, 2),
            "opened_at": datetime.fromtimestamp(opened_at, tz=timezone.utc).isoformat() if opened_at else "",
            "age_seconds": max(now_ts - opened_at, 0) if opened_at else 0,
        }
        normalized_positions.append(row)
        by_login[login].append(row)
        aggregate = symbol_totals[symbol]
        aggregate["symbol"] = symbol
        aggregate["buy_lots" if action == "BUY" else "sell_lots"] += lots
        aggregate["gross_lots"] += lots
        aggregate["floating_pnl"] += profit
        aggregate["positions"] += 1

    active_ip_counts = Counter(
        users_by_login.get(login, {}).get("last_ip", "")
        for login in by_login
        if users_by_login.get(login, {}).get("last_ip", "")
    )
    lookup_ips = [
        ip for ip, _ in active_ip_counts.most_common(LIVE_RISK_IP_LOOKUP_LIMIT)
    ]
    network_by_ip = enrich_ip_addresses_nonblocking(lookup_ips)
    account_source = {_to_int(row.get("Login"), 0): row for row in detail_users}
    account_rows: list[dict[str, Any]] = []
    severity_order = {"critical": 0, "high": 1, "elevated": 2, "normal": 3}

    # High-frequency risk scoring only needs accounts with open positions.
    # Portfolio totals below still cover every scoped account.
    for login in sorted(set(by_login) | ({include_login} if include_login in allowed_logins else set())):
        source = account_source.get(login, {})
        login_positions = by_login.get(login, [])
        balance = _to_float(source.get("Balance"), 0.0)
        credit = _to_float(source.get("Credit"), 0.0)
        equity = _to_float(source.get("Equity"), 0.0)
        margin = _to_float(source.get("Margin"), 0.0)
        margin_free = _to_float(source.get("Free Margin"), 0.0)
        margin_level = _to_float(source.get("Margin Level %"), 0.0)
        floating = _to_float(source.get("Live Floating PnL", source.get("Floating PnL")), 0.0)
        total_lots = math.fsum(row["volume"] for row in login_positions)
        symbol_lots: dict[str, float] = defaultdict(float)
        symbol_actions: dict[str, set[str]] = defaultdict(set)
        for row in login_positions:
            symbol_lots[row["symbol"]] += row["volume"]
            symbol_actions[row["symbol"]].add(row["action"])
        concentration = (max(symbol_lots.values()) / total_lots * 100.0) if total_lots > 0 else 0.0
        sorted_positions = sorted(login_positions, key=lambda row: row["opened_at"])
        burst = any(
            left["opened_at"] and right["opened_at"] and abs(right["age_seconds"] - left["age_seconds"]) <= 2
            for left, right in zip(sorted_positions, sorted_positions[1:])
        )
        hedge = any(len(actions) > 1 for actions in symbol_actions.values())
        grid = any(sum(1 for row in login_positions if row["symbol"] == symbol) >= 3 for symbol in symbol_lots)
        martingale = any(
            current["volume"] > previous["volume"] * 1.5
            for symbol in symbol_lots
            for previous, current in zip(
                [row for row in sorted_positions if row["symbol"] == symbol],
                [row for row in sorted_positions if row["symbol"] == symbol][1:],
            )
        )
        signals: list[str] = []
        score = 0
        last_ip = users_by_login.get(login, {}).get("last_ip", "")
        shared_ip_accounts = active_ip_counts.get(last_ip, 0) if last_ip else 0
        network = network_by_ip.get(last_ip, {})
        if login_positions and equity <= 0:
            signals.append("Non-positive equity")
            score += 35
        if login_positions and margin_level and margin_level < 100:
            signals.append("Stop-out proximity")
            score += 40
        elif login_positions and margin_level and margin_level < 150:
            signals.append("Low margin level")
            score += 25
        loss_ratio = abs(min(floating, 0.0)) / max(abs(balance), 1.0) * 100.0
        if loss_ratio >= 20:
            signals.append("Floating loss >20% balance")
            score += 30
        elif loss_ratio >= 10:
            signals.append("Floating loss >10% balance")
            score += 15
        profit_multiple = (floating / max(balance, 1.0)) if balance > 0 and floating > 0 else 0.0
        if balance > 0 and balance <= SMALL_BALANCE_ALERT_THRESHOLD and floating >= max(OUTSIZED_PROFIT_MIN_USD, balance * OUTSIZED_PROFIT_MULTIPLE):
            signals.append("Small balance, outsized floating profit")
            score += 25
        if concentration >= 70:
            signals.append("Symbol concentration")
            score += 15
        if len(login_positions) >= 20:
            signals.append("High open-position count")
            score += 15
        largest_position_lots = max((row["volume"] for row in login_positions), default=0.0)
        if largest_position_lots >= HIGH_LOT_ALERT_THRESHOLD:
            signals.append(f"Large position ({largest_position_lots:g} lots)")
            score += 20
        if grid:
            signals.append("Grid-like exposure")
            score += 15
        if martingale:
            signals.append("Volume escalation")
            score += 20
        if burst:
            signals.append("Burst execution")
            score += 15
        if hedge:
            signals.append("Opposing symbol exposure")
            score += 10
        if shared_ip_accounts > 1:
            signals.append(f"Shared login IP ({shared_ip_accounts} accounts)")
            score += 15
        if network.get("vps_status") == "detected":
            signals.append("VPS / hosting network detected")
            score += 15
        score = min(score, 100)
        severity = "critical" if score >= 70 else "high" if score >= 50 else "elevated" if score >= 25 else "normal"
        account_rows.append({
            "login": login,
            "name": users_by_login.get(login, {}).get("name", ""),
            "group": group_map.get(login, ""),
            "currency": users_by_login.get(login, {}).get("currency", ""),
            "balance": round(balance, 2),
            "credit": round(credit, 2),
            "equity": round(equity, 2),
            "margin": round(margin, 2),
            "margin_free": round(margin_free, 2),
            "margin_level": round(margin_level, 2),
            "floating_pnl": floating,
            "profit_multiple": round(profit_multiple, 2),
            "positions": len(login_positions),
            "gross_lots": round(total_lots, 8),
            "concentration_pct": round(concentration, 1),
            "risk_score": score,
            "severity": severity,
            "signals": signals,
            "last_ip": last_ip,
            "shared_ip_accounts": shared_ip_accounts,
            "vps_status": network.get("vps_status", "unknown"),
            "vps_confidence": network.get("vps_confidence", 0),
            "vps_evidence": network.get("vps_evidence", ["Provider lookup unavailable"]),
            "location": network.get("location", ""),
            "country": network.get("country", ""),
            "country_code": network.get("country_code", ""),
            "city": network.get("city", ""),
            "latitude": network.get("latitude"),
            "longitude": network.get("longitude"),
            "provider": network.get("provider", ""),
            "asn": network.get("asn", ""),
            "proxy": network.get("proxy", False),
            "hosting": network.get("hosting", False),
            "active": bool(login_positions),
        })

    account_rows.sort(key=(lambda row: row["login"]) if stable_order else (lambda row: (severity_order[row["severity"]], -row["risk_score"], row["floating_pnl"])))
    high_lot_positions = sorted(
        (row for row in normalized_positions if row["volume"] >= HIGH_LOT_ALERT_THRESHOLD),
        key=(lambda row: int(row["ticket"])) if stable_order else (lambda row: (-row["volume"], row["profit"])),
    )
    normalized_positions.sort(key=(lambda row: int(row["ticket"])) if stable_order else (lambda row: row["profit"]))
    symbols = []
    for aggregate in symbol_totals.values():
        symbols.append({
            **aggregate,
            "buy_lots": round(aggregate["buy_lots"], 8),
            "sell_lots": round(aggregate["sell_lots"], 8),
            "net_lots": round(aggregate["buy_lots"] - aggregate["sell_lots"], 8),
            "gross_lots": round(aggregate["gross_lots"], 8),
            "floating_pnl": round(aggregate["floating_pnl"], 2),
        })
    symbols.sort(key=lambda row: row["gross_lots"], reverse=True)

    map_points = []
    for ip, count in active_ip_counts.items():
        intel = network_by_ip.get(ip, {})
        if intel.get("status") == "available" and intel.get("latitude") is not None and intel.get("longitude") is not None:
            map_points.append({**intel, "accounts": count})
    map_points.sort(key=lambda row: (-row["accounts"], row.get("country", ""), row["ip"]))
    country_counts = Counter(row.get("country") or "Unknown" for row in map_points)
    provider_counts = Counter(row.get("provider") or "Unknown" for row in map_points)
    shared_clusters = [
        {"ip": ip, "accounts": count, **network_by_ip.get(ip, {})}
        for ip, count in active_ip_counts.items() if count > 1
    ]
    shared_clusters.sort(key=lambda row: (-row["accounts"], row["ip"]))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected_groups": groups,
        "summary": {
            "scoped_accounts": len(scoped_users),
            "currency_totals": currency_money_totals(scoped_users,native=True),
            "currencies": sorted({str(user.get("Currency") or "UNKNOWN") for user in scoped_users}),
            "balance": round(sum(_to_float(row.get("Balance"), 0.0) for row in scoped_users), 2),
            "buy_lots": round(math.fsum(row["volume"] for row in normalized_positions if row["action"] == "BUY"), 8),
            "sell_lots": round(math.fsum(row["volume"] for row in normalized_positions if row["action"] == "SELL"), 8),
            "active_accounts": len(by_login),
            "open_positions": len(normalized_positions),
            "gross_lots": round(math.fsum(row["volume"] for row in normalized_positions), 8),
            "net_lots": round(math.fsum(row["volume"] * (1 if row["action"] == "BUY" else -1 if row["action"] == "SELL" else 0) for row in normalized_positions), 8),
            "high_lot_threshold": HIGH_LOT_ALERT_THRESHOLD,
            "large_positions": len(high_lot_positions),
            "floating_pnl": round(sum(
                _to_float(row.get("Live Floating PnL", row.get("Floating PnL")), 0.0)
                for row in scoped_users
            ), 2),
            "equity": round(sum(_to_float(row.get("Equity"), 0.0) for row in scoped_users), 2),
            "margin": round(sum(_to_float(row.get("Margin"), 0.0) for row in scoped_users), 2),
            "margin_free": round(sum(_to_float(row.get("Free Margin"), 0.0) for row in scoped_users), 2),
            "critical_accounts": sum(row["severity"] == "critical" for row in account_rows),
            "flagged_accounts": sum(row["severity"] != "normal" for row in account_rows),
            "vps_accounts": sum(row["vps_status"] == "detected" for row in account_rows),
            "located_ips": len(map_points),
            "countries": len(country_counts),
        },
        "accounts": account_rows[:row_limit],
        "symbols": symbols[:100 if row_limit else None],
        "positions": normalized_positions[:row_limit],
        "large_positions": high_lot_positions[:100 if row_limit else None],
        "network": {
            "map_points": map_points,
            "countries": [{"country": name, "ips": count} for name, count in country_counts.most_common()],
            "providers": [{"provider": name, "ips": count} for name, count in provider_counts.most_common(20 if row_limit is not None else None)],
            "shared_clusters": shared_clusters[:100 if row_limit is not None else None],
            "lookup_provider": "ip-api.com",
        },
        "warnings": _dedupe_warnings(
            [f"Showing {row_limit} of {len(account_rows)} active accounts; totals include the full scope."] if row_limit is not None and len(account_rows) > row_limit else [],
            [f"Showing {row_limit} of {len(normalized_positions)} positions; totals include the full scope."] if row_limit is not None and len(normalized_positions) > row_limit else [],
        ),
    }


def _deal_timestamp(deal: Any) -> int:
    millis = _to_int(_value(deal, "TimeMsc", "time_msc", "TimeMSC"), 0) or 0
    if millis:
        return millis // 1000
    return _to_int(_value(deal, "Time", "time", "Datetime", "datetime"), 0) or 0


def _deal_lots(deal: Any) -> float:
    extended = _value(deal, "VolumeExt", "volume_ext", default=None)
    if extended is not None and _to_float(extended, 0.0) > 0:
        return _to_float(extended, 0.0) / 100000000.0
    raw = _to_float(_value(deal, "Volume", "volume", "VolumeExt", "volume_ext"), 0.0)
    return raw / 10000.0 if abs(raw) >= 100.0 else raw


def _deal_volume_units(deal: Any) -> float:
    """Return MT5's legacy 1/10,000-lot units, including VolumeExt-only rows."""
    volume = _value(deal, "Volume", "volume", default=None)
    if volume is not None and _to_float(volume, 0.0) > 0:
        return _to_float(volume, 0.0)
    extended = _value(deal, "VolumeExt", "volume_ext", default=None)
    return _to_float(extended, 0.0) / 10000.0 if extended is not None else _to_float(volume, 0.0)


def build_client_trade_lifecycles(deals: list[Any], login: int) -> list[dict[str, Any]]:
    """Collapse MT5 entry/exit deals into closed-position lifecycles."""
    grouped: dict[int, list[Any]] = defaultdict(list)
    for deal in deals:
        deal_login = _login_from_record(deal)
        action = _to_int(_value(deal, "Action", "action"), -1)
        position_id = _to_int(_value(deal, "PositionID", "position_id", "Position", "position"), 0) or 0
        if deal_login != int(login) or action not in {DEAL_BUY, DEAL_SELL} or not position_id:
            continue
        grouped[position_id].append(deal)

    lifecycles: list[dict[str, Any]] = []
    for position_id, position_deals in grouped.items():
        ordered = sorted(position_deals, key=_deal_timestamp)
        opens = [deal for deal in ordered if (_to_int(_value(deal, "Entry", "entry"), 0) or 0) == 0]
        closes = [
            deal for deal in ordered
            if (_to_int(_value(deal, "Entry", "entry"), -1) or -1) in {ENTRY_OUT, ENTRY_OUT_BY, ENTRY_INOUT}
        ]
        if not opens or not closes:
            continue
        if any(_value(deal, "Profit", "profit", default=None) is None for deal in closes):
            continue  # A closed trade without a reported profit cannot support a positive P&L detector.
        first_open, last_close = opens[0], closes[-1]
        opened_at, closed_at = _deal_timestamp(first_open), _deal_timestamp(last_close)
        if not opened_at or not closed_at or closed_at < opened_at:
            continue
        side_action = _to_int(_value(first_open, "Action", "action"), DEAL_BUY)
        pnl = sum(
            _to_float(_value(deal, "Profit", "profit"), 0.0)
            + _to_float(_value(deal, "Commission", "commission"), 0.0)
            + _to_float(_value(deal, "Fee", "fee"), 0.0)
            + _to_float(_value(deal, "Storage", "storage", "Swap", "swap"), 0.0)
            for deal in ordered
        )
        lifecycles.append({
            "position_id": position_id,
            "symbol": str(_value(first_open, "Symbol", "symbol", default="Unknown") or "Unknown"),
            "side": "BUY" if side_action == DEAL_BUY else "SELL",
            "open_ts": opened_at,
            "open_time_msc": _to_int(_value(first_open, "TimeMsc", "time_msc", "TimeMSC"), 0) or 0,
            "close_ts": closed_at,
            "open_time": datetime.fromtimestamp(opened_at, tz=timezone.utc).isoformat(),
            "close_time": datetime.fromtimestamp(closed_at, tz=timezone.utc).isoformat(),
            "open_price": round(_to_float(_value(first_open, "Price", "price", "PriceOpen", "price_open"), 0.0), 8),
            "close_price": round(_to_float(_value(last_close, "Price", "price", "PriceClose", "price_close"), 0.0), 8),
            "volume": round(sum(max(_deal_lots(deal), 0.0) for deal in opens), 4),
            "pnl": round(pnl, 2),
            "hold_seconds": max(closed_at - opened_at, 0),
        })
    return sorted(lifecycles, key=lambda row: (row["open_ts"], row["position_id"]))


def analyse_client_lifecycles(rows: list[dict[str, Any]], scalping_seconds: int = 180) -> dict[str, Any]:
    """Apply the Flow Analyzer pattern rules to completed MT5 positions."""
    ordered = sorted(rows, key=lambda row: (row["open_ts"], row["position_id"]))
    previous_by_symbol: dict[str, dict[str, Any]] = {}
    marked: list[dict[str, Any]] = []
    for index, source in enumerate(ordered):
        row = dict(source)
        previous = previous_by_symbol.get(row["symbol"])
        flags = []
        if row["hold_seconds"] <= scalping_seconds:
            flags.append("Scalp")
        if previous and row["side"] != previous["side"] and 0 <= row["open_ts"] - previous["close_ts"] <= 20:
            flags.append("Reversal")
        if index and 0 <= row["open_ts"] - ordered[index - 1]["open_ts"] <= 2:
            flags.append("Burst")
        if previous and 0 <= row["open_ts"] - previous["open_ts"] <= 300:
            price_base = max(abs(previous["open_price"]), 1e-12)
            if abs(row["open_price"] - previous["open_price"]) / price_base <= 0.002 and abs(row["volume"] - previous["volume"]) <= 0.01:
                flags.append("Grid")
        if previous and previous["pnl"] < 0 and row["volume"] > previous["volume"]:
            flags.append("Martingale")
        row["flags"] = flags
        marked.append(row)
        previous_by_symbol[row["symbol"]] = row

    total = len(marked)
    winning = [row for row in marked if row["pnl"] > 0]
    losing = [row for row in marked if row["pnl"] < 0]
    gross_profit = sum(row["pnl"] for row in winning)
    gross_loss = abs(sum(row["pnl"] for row in losing))
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    equity_curve = []
    loss_streak = worst_loss_streak = 0
    for row in sorted(marked, key=lambda item: item["close_ts"]):
        cumulative += row["pnl"]
        peak = max(peak, cumulative)
        drawdown = peak - cumulative
        max_drawdown = max(max_drawdown, drawdown)
        loss_streak = loss_streak + 1 if row["pnl"] < 0 else 0
        worst_loss_streak = max(worst_loss_streak, loss_streak)
        equity_curve.append({"time": row["close_time"], "pnl": round(cumulative, 2), "drawdown": round(-drawdown, 2)})

    flag_counts = Counter(flag for row in marked for flag in row["flags"])
    percentages = {key: round(flag_counts.get(key, 0) / total * 100.0, 1) if total else 0.0 for key in ("Scalp", "Reversal", "Burst", "Grid", "Martingale")}
    qualified_scalp = percentages["Scalp"] if percentages["Scalp"] >= 30.0 else 0.0
    toxicity = min(100.0, qualified_scalp + percentages["Reversal"] + percentages["Burst"])

    by_symbol: dict[str, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "volume": 0.0, "pnl": 0.0, "scalps": 0})
    by_hour: dict[int, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "pnl": 0.0})
    by_day: dict[str, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "pnl": 0.0})
    for row in marked:
        symbol = by_symbol[row["symbol"]]
        symbol["trades"] += 1
        symbol["volume"] += row["volume"]
        symbol["pnl"] += row["pnl"]
        symbol["scalps"] += "Scalp" in row["flags"]
        opened = datetime.fromtimestamp(row["open_ts"], tz=timezone.utc)
        by_hour[opened.hour]["trades"] += 1
        by_hour[opened.hour]["pnl"] += row["pnl"]
        day_key = opened.date().isoformat()
        by_day[day_key]["trades"] += 1
        by_day[day_key]["pnl"] += row["pnl"]

    symbol_rows = [{
        "symbol": symbol,
        "trades": values["trades"],
        "volume": round(values["volume"], 2),
        "pnl": round(values["pnl"], 2),
        "scalp_pct": round(values["scalps"] / values["trades"] * 100.0, 1),
    } for symbol, values in by_symbol.items()]
    symbol_rows.sort(key=lambda row: abs(row["pnl"]), reverse=True)
    total_volume = sum(row["volume"] for row in marked)
    concentration = max((row["volume"] for row in symbol_rows), default=0.0) / max(total_volume, 1e-12) * 100.0

    return {
        "metrics": {
            "total_trades": total,
            "total_pnl": round(sum(row["pnl"] for row in marked), 2),
            "total_volume": round(total_volume, 2),
            "win_rate": round(len(winning) / total * 100.0, 1) if total else 0.0,
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else (round(gross_profit, 2) if gross_profit else 0.0),
            "expectancy": round(sum(row["pnl"] for row in marked) / total, 2) if total else 0.0,
            "average_hold_seconds": round(sum(row["hold_seconds"] for row in marked) / total, 1) if total else 0.0,
            "max_drawdown": round(max_drawdown, 2),
            "worst_loss_streak": worst_loss_streak,
            "symbol_concentration_pct": round(concentration, 1),
            "toxicity_score": round(toxicity, 1),
        },
        "patterns": {key.lower(): {"count": flag_counts.get(key, 0), "pct": percentages[key]} for key in percentages},
        "equity_curve": equity_curve,
        "symbols": symbol_rows,
        "hourly": [{"hour": f"{hour:02d}:00", "trades": by_hour[hour]["trades"], "pnl": round(by_hour[hour]["pnl"], 2)} for hour in range(24)],
        "daily": [{"date": day, "trades": values["trades"], "pnl": round(values["pnl"], 2)} for day, values in sorted(by_day.items())],
        "trades": list(reversed(marked))[:5000],
    }


def request_tick_history(api_handle, symbol: str, from_ts: int, to_ts: int) -> tuple[list[Any], str | None]:
    method = getattr(api_handle, "TickHistoryRequest", None)
    if method is None:
        return [], "TickHistoryRequest is unavailable."
    try:
        response = method(symbol, int(from_ts), int(to_ts))
    except Exception as exc:
        return [], str(exc)
    if isinstance(response, bool) or response is None:
        return [], f"TickHistoryRequest({symbol}) returned no tick array."
    data, error = _response_to_data(response, f"TickHistoryRequest({symbol})")
    if error is None and isinstance(data, (bool, int, float)):
        return [], f"TickHistoryRequest({symbol}) returned status without tick data."
    return (_as_list(data), None) if error is None else ([], error)


def request_news(api_handle, from_ts: int, to_ts: int) -> tuple[list[dict[str, Any]], str | None]:
    method = getattr(api_handle, "NewsGet", None)
    if method is None:
        return [], "MT5 NewsGet is unavailable for this Manager API profile."
    try:
        response = method()
    except Exception as exc:
        return [], str(exc)
    if isinstance(response, tuple) and len(response) == 2:
        response = response[0]
    rows = []
    for item in _as_list(response):
        timestamp = _to_int(_value(item, "Time", "time", "Datetime", "datetime"), 0) or 0
        if not (from_ts <= timestamp <= to_ts):
            continue
        rows.append({
            "id": _to_int(_value(item, "ID", "id"), 0) or 0,
            "time": timestamp,
            "subject": str(_value(item, "Subject", "subject", default="") or "").strip(),
            "category": str(_value(item, "Category", "category", default="") or "").strip(),
            "flags": _to_int(_value(item, "Flags", "flags"), 0) or 0,
        })
    rows.sort(key=lambda row: row["time"])
    return rows, None


def normalize_calendar_events(events: list[dict[str, Any]], from_ts: int, to_ts: int) -> list[dict[str, Any]]:
    """Use explicit UTC economic-calendar releases, never bulletin publication times."""
    normalized = []
    for item in events:
        if not isinstance(item, dict):
            raise ValueError("Each economic calendar event must be an object.")
        if item.get("time_mode", "timed") != "timed":
            continue
        raw_time = item.get("time") or item.get("datetime")
        if not isinstance(raw_time, str):
            raise ValueError("Economic calendar event time must be an ISO 8601 UTC string.")
        try:
            parsed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid economic calendar time: {raw_time}") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError("Economic calendar times must include UTC (Z or +00:00).")
        timestamp = int(parsed.timestamp())
        if not from_ts <= timestamp <= to_ts:
            continue
        currency = str(item.get("currency") or "").strip().upper()
        title = str(item.get("title") or item.get("event") or "").strip()
        impact = str(item.get("impact") or "").strip().lower()
        if not re.fullmatch(r"[A-Z]{3}", currency) or not title or impact not in {"low", "medium", "high"}:
            raise ValueError("Calendar events require a 3-letter currency, title, and low/medium/high impact.")
        normalized.append({"time": timestamp, "currency": currency, "title": title[:256], "impact": impact})
    return sorted(normalized, key=lambda row: row["time"])


def _tick_time_msc(tick: Any) -> int:
    millis = _to_int(_value(tick, "datetime_msc", "DatetimeMsc", "TimeMsc", "time_msc"), 0) or 0
    if millis:
        return millis
    seconds = _to_int(_value(tick, "datetime", "Datetime", "Time", "time"), 0) or 0
    return seconds * 1000


def build_antifraud_snapshot(
    api_handle,
    groups: list[str],
    excluded_logins: list[int] | None = None,
    *,
    lookback_hours: int = 24,
    gap_seconds: float = 3.0,
    profit_threshold: float = 10.0,
    check_total_realized_pnl: bool = False,
    minimum_total_realized_pnl: float = 0.0,
    maximum_duration_minutes: float = 5.0,
    scalp_seconds: int = 180,
    scalp_sequence_min: int = 3,
    sharp_profit_threshold: float = 100.0,
    large_order_lots: float = 1.0,
    large_account_lots: float = 5.0,
    large_order_exposure_ratio: float = 1.0,
    large_account_exposure_ratio: float = 5.0,
    mini_equity_threshold: float = 100.0,
    stopout_margin_level: float = 100.0,
    news_window_minutes: float = 5.0,
    news_impacts: list[str] | None = None,
    calendar_events: list[dict[str, Any]] | None = None,
    excluded_symbols: list[str] | None = None,
    concentration_min_trades: int = 3,
    deposit_alert_threshold: float = 1000.0,
    cached_users: list[dict[str, Any]] | None = None,
    cached_positions: list[dict[str, Any]] | None = None,
    request_groups: list[str] | None = None,
) -> dict[str, Any]:
    scan_started = time_module.monotonic()
    now = datetime.now(timezone.utc)
    from_ts = int((now - timedelta(hours=lookback_hours)).timestamp())
    to_ts = int(now.timestamp())
    excluded = _normalize_login_set(excluded_logins)
    if cached_users is None:
        users_by_login, group_map, warnings = _collect_user_scope(
            api_handle, groups, excluded, request_groups=request_groups
        )
    else:
        selected_groups = {str(group).strip() for group in groups if str(group).strip()}
        scoped_users = [
            row for row in cached_users
            if str(row.get("Group", "")).strip() in selected_groups
            and _to_int(row.get("Login"), 0) not in excluded
        ]
        users_by_login = {
            _to_int(row.get("Login"), 0): {
                "name": str(row.get("Name", "") or ""),
                "currency": str(row.get("Currency", "USD") or "USD"),
                "last_ip": _ip_text(row.get("LastIP", "")),
                "agent_login": _to_int(row.get("Agent"), 0) or 0,
            }
            for row in scoped_users if _to_int(row.get("Login"), 0) > 0
        }
        group_map = {
            _to_int(row.get("Login"), 0): str(row.get("Group", "") or "")
            for row in scoped_users if _to_int(row.get("Login"), 0) > 0
        }
        warnings = []
    allowed_logins = set(users_by_login)
    excluded_patterns = [str(pattern).strip() for pattern in (excluded_symbols or []) if str(pattern).strip()]

    # Manager builds differ in how they marshal DealRequestByLogins; some only
    # return the first login without an error. Exact group requests are slower
    # but authoritative across manager profiles.
    deals, deal_warnings = _fetch_scoped_deals(
        api_handle, groups, from_ts, to_ts, allowed_logins,
        request_groups=request_groups,
    )
    warnings.extend(deal_warnings)
    seen_deals: set[int] = set()
    filtered_deals: list[Any] = []
    for deal in deals:
        login = _login_from_record(deal)
        if login not in allowed_logins:
            continue
        symbol = str(_value(deal, "Symbol", "symbol", default="") or "")
        if excluded_patterns and any(fnmatch.fnmatch(symbol, pattern) for pattern in excluded_patterns):
            continue
        ticket = _to_int(_value(deal, "Deal", "deal", "Ticket", "ticket"), 0) or 0
        if ticket and ticket in seen_deals:
            continue
        if ticket:
            seen_deals.add(ticket)
        filtered_deals.append(deal)
    deals = filtered_deals

    deals_by_login: dict[int, list[Any]] = defaultdict(list)
    for deal in deals:
        login = _login_from_record(deal)
        if login is not None:
            deals_by_login[login].append(deal)
    lifecycles_by_login = {
        login: build_client_trade_lifecycles(login_deals, login)
        for login, login_deals in deals_by_login.items()
    }
    all_lifecycles = [row | {"login": login} for login, rows in lifecycles_by_login.items() for row in rows]
    news_window_seconds = max(int(news_window_minutes * 60), 1)
    selected_news_impacts = set(news_impacts if news_impacts is not None else ("high", "medium", "low"))
    if not selected_news_impacts or not selected_news_impacts <= {"high", "medium", "low"}:
        raise ValueError("Select at least one valid news impact: high, medium, or low.")
    calendar_feed = read_calendar_api()
    automatic_calendar = bool(calendar_feed and calendar_feed["status"] == "connected" and not calendar_events)
    calendar_source = calendar_events or (calendar_feed["events"] if automatic_calendar else [])
    news_rows = [row for row in normalize_calendar_events(calendar_source, from_ts - news_window_seconds, to_ts + news_window_seconds)
                 if row["impact"] in selected_news_impacts]
    news_error = None if news_rows else (calendar_feed["message"] if calendar_feed and calendar_feed["status"] != "connected" and not calendar_events
                                        else "No timed economic calendar releases with the selected impacts overlap this scan.")
    total_pnl_by_login = {login: sum(row["pnl"] for row in rows) for login, rows in lifecycles_by_login.items()}
    total_deposit_by_login: dict[int, float] = defaultdict(float)
    lookback_withdrawals_by_login: dict[int, float] = defaultdict(float)
    lookback_deposit_count_by_login: dict[int, int] = defaultdict(int)
    qualifying_deposits: list[tuple[int, int, int, float]] = []
    funding_uncertain_logins: set[int] = set()
    for deal in deals:
        login = _login_from_record(deal)
        action = _to_int(_value(deal, "Action", "action"), None)
        amount = _to_float(_value(deal, "Profit", "profit"), 0.0)
        comment = str(_value(deal, "Comment", "comment", default="") or "").lower()
        ambiguous_funding = any(word in comment for word in ("credit", "reversal", "transfer", "correction", "bonus", "adjust", "cancel", "internal"))
        if login is not None and action == DEAL_BALANCE and ambiguous_funding:
            funding_uncertain_logins.add(login)
        elif login is not None and action == DEAL_BALANCE and amount > 0:
            total_deposit_by_login[login] += amount
            lookback_deposit_count_by_login[login] += 1
            ticket = _to_int(_value(deal, "Deal", "deal", "Ticket", "ticket"), 0) or 0
            deal_ts = _deal_timestamp(deal)
            if ticket > 0 and from_ts <= deal_ts <= to_ts and amount >= deposit_alert_threshold:
                qualifying_deposits.append((login, ticket, deal_ts, amount))
        elif login is not None and action == DEAL_BALANCE and amount < 0:
            lookback_withdrawals_by_login[login] += abs(amount)

    # A recent deposit alone does not establish that an account is genuinely
    # low-funded. Load complete histories only for accounts with recent closed
    # trades so the detector can also consider lifetime funding and capital at
    # the beginning of the selected window.
    funding_logins = {
        login for login, realized in total_pnl_by_login.items()
        if realized >= LOW_DEPOSIT_HIGH_PROFIT_MIN
    }
    lifetime_deposits_by_login: dict[int, float] = defaultdict(float)
    starting_balance_by_login: dict[int, float] = defaultdict(float)
    if funding_logins:
        # Never request all-time history for every selected group here. The
        # candidates already have recent closed trades, so login-scoped history
        # is both authoritative and orders of magnitude smaller.
        login_history_supported = any(
            getattr(api_handle, method_name, None) is not None
            for method_name in ("DealRequestByLogins", "DealRequestByLoginsNumPy")
        )
        if login_history_supported:
            lifetime_deals, lifetime_error = request_deals_by_logins(
                api_handle, funding_logins, 0, to_ts
            )
        else:
            lifetime_deals, lifetime_warnings = _fetch_scoped_deals(
                api_handle, groups, 0, to_ts, funding_logins
            )
            lifetime_error = "; ".join(lifetime_warnings) or None
        returned_logins = {
            login for login in (_login_from_record(row) for row in lifetime_deals)
            if login is not None
        }
        missing_logins = funding_logins - returned_logins
        if login_history_supported and (lifetime_error or missing_logins):
            if lifetime_error:
                warnings.append(f"Lifetime funding bulk request: {lifetime_error}")
            # Some Manager wrappers marshal only the first login in a bulk
            # request. Retry only missing candidates individually; do not fall
            # back to a multi-year, all-groups scan.
            for login in sorted(missing_logins):
                login_deals, login_error = request_deals_by_login(
                    api_handle, login, 0, to_ts
                )
                if login_error:
                    warnings.append(
                        f"Lifetime funding login {login}: {login_error}"
                    )
                    continue
                lifetime_deals.extend(login_deals)
        seen_lifetime_deals: set[int] = set()
        for deal in lifetime_deals:
            login = _login_from_record(deal)
            if login not in funding_logins:
                continue
            ticket = _to_int(_value(deal, "Deal", "deal", "Ticket", "ticket"), 0) or 0
            if ticket and ticket in seen_lifetime_deals:
                continue
            if ticket:
                seen_lifetime_deals.add(ticket)
            action = _to_int(_value(deal, "Action", "action"), None)
            profit = _to_float(_value(deal, "Profit", "profit"), 0.0)
            comment = str(_value(deal, "Comment", "comment", default="") or "").lower()
            ambiguous_funding = any(word in comment for word in ("credit", "reversal", "transfer", "correction", "bonus", "adjust", "cancel", "internal"))
            if action == DEAL_BALANCE and ambiguous_funding:
                funding_uncertain_logins.add(login)
            elif action == DEAL_BALANCE and profit > 0:
                lifetime_deposits_by_login[login] += profit
            deal_ts = _to_int(_value(deal, "Time", "time"), 0) or 0
            if deal_ts < from_ts:
                starting_balance_by_login[login] += (
                    profit
                    + _to_float(_value(deal, "Commission", "commission"), 0.0)
                    + _to_float(_value(deal, "Fee", "fee"), 0.0)
                    + _to_float(_value(deal, "Storage", "storage", "Swap", "swap"), 0.0)
                )

    if cached_positions is not None:
        positions = [
            row for row in cached_positions
            if _to_int(row.get("Login"), 0) in allowed_logins
        ]
    else:
        positions = []
        positions_error = None
        # Use the same compressed group masks as the user/deal requests. Asking
        # for positions by 100k+ logins creates hundreds of avoidable SDK calls.
        if request_groups:
            for group in request_groups:
                group_positions, err = request_positions_by_group(api_handle, group)
                if err:
                    warnings.append(f"{group}: {err}")
                else:
                    positions.extend(group_positions)
        else:
            positions, positions_error = request_positions_by_logins(api_handle, allowed_logins)
            if positions_error:
                positions = []
                for group in groups:
                    group_positions, err = request_positions_by_group(api_handle, group)
                    if err:
                        warnings.append(f"{group}: {err}")
                    else:
                        positions.extend(group_positions)
    seen_positions: set[int] = set()
    filtered_positions: list[Any] = []
    for position in positions:
        login = _login_from_record(position)
        ticket = _to_int(_value(position, "Position", "position", "Ticket", "ticket"), 0) or 0
        if login not in allowed_logins or (ticket and ticket in seen_positions):
            continue
        if ticket:
            seen_positions.add(ticket)
        filtered_positions.append(position)
    positions = filtered_positions
    active_logins = {_login_from_record(position) for position in positions} | set(lifecycles_by_login)
    if cached_users is not None:
        cached_accounts = {
            _to_int(row.get("Login"), 0): row
            for row in cached_users if _to_int(row.get("Login"), 0) in allowed_logins
        }
        accounts, account_warnings = cached_accounts, []
    else:
        accounts, account_warnings = request_accounts_by_groups(api_handle, groups, allowed_logins)
        warnings.extend(account_warnings)

    events: list[dict[str, Any]] = []

    fraud_rules = {"latency_arbitrage", "balance_below_credit"}
    behavior_rules = {"short_hold_scalping", "scalpers_hft", "single_trade_profit_concentration", "symbol_profit_concentration"}
    def add_event(detector: str, login: int, *, severity: str = "warning", symbol: str = "", occurred_at: str = "", evidence: str, value: float | None = None, position_id: int | None = None, deal_id: int | None = None, triggering_values: dict[str, Any] | None = None, thresholds: dict[str, Any] | None = None, data_quality: str = "VERIFIED"):
        events.append({
            "detector": detector, "rule": detector, "classification": detector.upper(),
            "category": "fraud_indicator" if detector in fraud_rules else "behavioral_indicator" if detector in behavior_rules else "risk_indicator",
            "severity": severity, "login": login,
            "name": users_by_login.get(login, {}).get("name", ""), "group": group_map.get(login, ""),
            "account_currency": users_by_login.get(login, {}).get("currency", ""),
            "symbol": symbol, "occurred_at": occurred_at, "timestamp": occurred_at or now.isoformat(), "evidence": evidence,
            "value": value, "position_id": position_id, "deal_id": deal_id,
            "triggering_values": triggering_values if triggering_values is not None else {"value": value},
            "thresholds": thresholds or {}, "data_quality": data_quality,
        })

    balance_credit_checked = 0
    for login, account in accounts.items():
        if login not in allowed_logins:
            continue
        balance = _to_float(_value(account, "Balance", "balance", default=None), None)
        credit = _to_float(_value(account, "Credit", "credit", default=None), None)
        if balance is None or credit is None:
            continue
        balance_credit_checked += 1
        if credit > 0 and balance < credit:
            currency = users_by_login.get(login, {}).get("currency") or "Unknown"
            shortfall = credit - balance
            add_event(
                "balance_below_credit", login, severity="high",
                evidence=(f"MT5 account balance {_format_money_text(balance)} {currency} is below "
                          f"credit {_format_money_text(credit)} {currency} by {_format_money_text(shortfall)} {currency}."),
                value=round(shortfall, 2),
                triggering_values={"balance": balance, "credit": credit, "shortfall": shortfall, "currency": currency},
                thresholds={"condition": "balance < credit", "minimum_credit": 0},
            )
    if balance_credit_checked < len(allowed_logins):
        warnings.append(
            f"Balance Below Credit: checked {balance_credit_checked} of {len(allowed_logins)} scoped accounts; "
            "accounts without both MT5 balance and credit values could not be evaluated."
        )

    # LastIP and Agent are live MT5 user fields. A shared last IP is a review
    # signal only; it does not prove simultaneous access or affiliate abuse.
    public_ips: dict[str, set[int]] = defaultdict(set)
    for login, user in users_by_login.items():
        address = str(user.get("last_ip") or "")
        try:
            if address and ipaddress.ip_address(address).is_global:
                public_ips[address].add(login)
        except ValueError:
            continue
    ip_evidence_available = bool(public_ips)
    checked_ip_logins = set().union(*public_ips.values()) if public_ips else set()
    if len(checked_ip_logins) < len(allowed_logins):
        warnings.append(
            f"IP Control: checked public LastIP for {len(checked_ip_logins)} of {len(allowed_logins)} "
            "scoped accounts; missing or non-public addresses were not evaluated."
        )
    for address, logins_at_ip in public_ips.items():
        if len(logins_at_ip) < 2:
            continue
        for login in sorted(logins_at_ip):
            add_event(
                "ip_control", login, severity="warning",
                evidence=(f"MT5 LastIP {address} is shared by {len(logins_at_ip)} scoped accounts. "
                          "This is a last-login snapshot, not proof of simultaneous access."),
                value=float(len(logins_at_ip)),
                triggering_values={"last_ip": address, "scoped_logins": sorted(logins_at_ip)},
                thresholds={"minimum_accounts": 2, "source": "MTUser.LastIP"},
                data_quality="PARTIAL",
            )

    affiliate_evidence_available = False
    comparable_affiliates = 0
    for login, user in users_by_login.items():
        agent_login = _to_int(user.get("agent_login"), 0) or 0
        agent = users_by_login.get(agent_login)
        if agent_login <= 0 or agent_login == login or agent is None:
            continue
        client_ip = str(user.get("last_ip") or "")
        agent_ip = str(agent.get("last_ip") or "")
        if client_ip not in public_ips or agent_ip not in public_ips:
            continue
        affiliate_evidence_available = True
        comparable_affiliates += 1
        if client_ip and client_ip == agent_ip and client_ip in public_ips:
            add_event(
                "affiliate_cheating", login, severity="warning",
                evidence=(f"MT5 Agent {agent_login} and referred login {login} share LastIP {client_ip}. "
                          "Review referral ownership and access history; this is not proof of abuse."),
                triggering_values={"agent_login": agent_login, "client_login": login, "last_ip": client_ip},
                thresholds={"condition": "agent and referred client share public LastIP"},
                data_quality="PARTIAL",
            )
    if any((_to_int(user.get("agent_login"), 0) or 0) > 0 for user in users_by_login.values()):
        warnings.append(
            f"Affiliate Cheating: compared {comparable_affiliates} in-scope agent/client pairs with public "
            "LastIP values; other referrals need agent ownership and access history."
        )

    for login, ticket, deal_ts, amount in qualifying_deposits:
        currency = users_by_login.get(login, {}).get("currency") or "Unknown"
        add_event(
            "deposit_alert", login, deal_id=ticket,
            occurred_at=datetime.fromtimestamp(deal_ts, timezone.utc).isoformat(),
            evidence=f"MT5 balance deposit deal #{ticket}: {_format_money_text(amount)} {currency} meets the configured {_format_money_text(deposit_alert_threshold)} {currency} threshold",
            value=round(amount, 2),
            triggering_values={"deal_id": ticket, "deposit_amount": amount, "currency": currency, "mt5_action": DEAL_BALANCE},
            thresholds={"minimum_deposit_amount": deposit_alert_threshold, "currency": currency},
            data_quality="PARTIAL",  # MT5 balance action alone does not verify external payment origin.
        )

    # Closed-deal detectors.
    for login, rows in lifecycles_by_login.items():
        scalp_rows = [row for row in rows if row["hold_seconds"] <= scalp_seconds and row["pnl"] > 0]
        if len(scalp_rows) >= scalp_sequence_min:
            ordered_scalps = sorted(scalp_rows, key=lambda row: row["open_ts"])
            intervals = [current["open_ts"] - previous["open_ts"] for previous, current in zip(ordered_scalps, ordered_scalps[1:])]
            rapid_count = sum(0 <= interval <= 2 for interval in intervals)
            hft = len(scalp_rows) >= max(scalp_sequence_min, 10) and rapid_count >= 5
            add_event(
                "scalpers_hft" if hft else "short_hold_scalping", login,
                severity="high" if hft else "warning",
                evidence=f"{len(scalp_rows)} profitable short-hold trades, {rapid_count} inter-trade intervals <= 2s, hold <= {scalp_seconds}s",
                value=round(sum(row["pnl"] for row in scalp_rows), 2),
                triggering_values={"trade_count": len(scalp_rows), "rapid_intervals": rapid_count, "inter_trade_seconds": intervals, "max_hold_seconds": max(row["hold_seconds"] for row in scalp_rows), "gross_positive_realized_pnl": round(sum(row["pnl"] for row in scalp_rows), 2)},
                thresholds={"minimum_trades": scalp_sequence_min, "hft_minimum_trades": max(scalp_sequence_min, 10), "hft_rapid_intervals": 5, "rapid_interval_seconds": 2, "max_hold_seconds": scalp_seconds},
            )
        for row in rows:
            symbol_currencies = {row["symbol"][:3].upper(), row["symbol"][3:6].upper()}
            relevant_news = [news for news in news_rows if news["currency"] in symbol_currencies]
            closest_news = min(relevant_news, key=lambda news: abs(news["time"] - row["open_ts"]), default=None)
            if closest_news and abs(closest_news["time"] - row["open_ts"]) <= news_window_seconds:
                offset = row["open_ts"] - closest_news["time"]
                timing = f"{abs(offset)}s {'after' if offset >= 0 else 'before'}"
                news_time = datetime.fromtimestamp(closest_news["time"], timezone.utc).isoformat()
                add_event(
                    "news_trading", login,
                    severity="high" if closest_news["impact"] == "high" else "warning",
                    symbol=row["symbol"], occurred_at=row["open_time"],
                    position_id=row["position_id"], value=round(row["pnl"], 2),
                    evidence=(
                        f"Trade opened {timing} {closest_news['impact']}-impact economic calendar event "
                        f"'{closest_news['title']}' ({closest_news['currency']}) at {news_time}; "
                        f"held {round(row['hold_seconds'], 1)}s, P&L {_format_money_text(row['pnl'])}"
                    ),
                    triggering_values={"trade_open_ts": row["open_ts"], "calendar_event_ts": closest_news["time"], "offset_seconds": offset, "hold_seconds": row["hold_seconds"], "realized_pnl": row["pnl"], "currency": closest_news["currency"], "impact": closest_news["impact"]},
                    data_quality="VERIFIED" if automatic_calendar else "PARTIAL",
                )
            if row["pnl"] >= sharp_profit_threshold and row["hold_seconds"] <= maximum_duration_minutes * 60:
                add_event("sharp_deals", login, severity="high", symbol=row["symbol"], occurred_at=row["close_time"], evidence=f"{_format_money_text(row['pnl'])} in {round(row['hold_seconds']/60, 1)} minutes", value=row["pnl"], position_id=row["position_id"], triggering_values={"realized_pnl": row["pnl"], "hold_seconds": row["hold_seconds"]})
        realized = total_pnl_by_login.get(login, 0.0)
        lookback_deposit = total_deposit_by_login.get(login, 0.0)
        lifetime_deposits = lifetime_deposits_by_login.get(login, 0.0)
        starting_balance = max(starting_balance_by_login.get(login, 0.0), 0.0)
        starting_capital = starting_balance + lookback_deposit
        if login not in funding_uncertain_logins and 0 < lookback_deposit <= LOW_DEPOSIT_ALERT_THRESHOLD and realized >= max(
            LOW_DEPOSIT_HIGH_PROFIT_MIN, lookback_deposit * LOW_DEPOSIT_PROFIT_MULTIPLE
        ):
            add_event(
                "low_deposit_high_profit",
                login,
                severity="warning",
                evidence=(
                    f"Lookback deposit {_format_money_text(lookback_deposit)} produced "
                    f"{_format_money_text(realized)} realized profit across {len(rows)} deal(s); "
                    "review the lifetime and starting-capital detectors for funding context"
                ),
                value=round(realized, 2),
            )
        if login not in funding_uncertain_logins and 0 < lifetime_deposits <= LOW_DEPOSIT_ALERT_THRESHOLD and realized >= max(
            LOW_DEPOSIT_HIGH_PROFIT_MIN, lifetime_deposits * LOW_DEPOSIT_PROFIT_MULTIPLE
        ):
            add_event(
                "lifetime_deposit_high_profit", login, severity="high",
                evidence=(
                    f"Lifetime cumulative deposits {_format_money_text(lifetime_deposits)} produced "
                    f"{_format_money_text(realized)} realized profit in the {lookback_hours}-hour window"
                ), value=round(realized, 2),
            )
        if login not in funding_uncertain_logins and 0 < starting_capital <= LOW_DEPOSIT_ALERT_THRESHOLD and realized >= max(
            LOW_DEPOSIT_HIGH_PROFIT_MIN, starting_capital * LOW_DEPOSIT_PROFIT_MULTIPLE
        ):
            add_event(
                "starting_capital_high_profit", login, severity="high",
                evidence=(
                    f"Starting balance {_format_money_text(starting_balance)} plus lookback deposits "
                    f"{_format_money_text(lookback_deposit)} gave {_format_money_text(starting_capital)} "
                    f"starting capital versus {_format_money_text(realized)} realized profit"
                ), value=round(realized, 2),
            )

        deposit_count = lookback_deposit_count_by_login.get(login, 0)
        if login not in funding_uncertain_logins and deposit_count >= 3:
            add_event(
                "repeated_funding", login,
                evidence=f"{deposit_count} separate deposits totaling {_format_money_text(lookback_deposit)} in the {lookback_hours}-hour window",
                value=float(deposit_count),
            )
        withdrawals = lookback_withdrawals_by_login.get(login, 0.0)
        if login not in funding_uncertain_logins and realized > 0 and withdrawals >= max(LOW_DEPOSIT_ALERT_THRESHOLD, realized * 0.8):
            add_event(
                "profit_withdrawal_pressure", login, severity="high",
                evidence=(
                    f"Withdrawals {_format_money_text(withdrawals)} are at least 80% of recent realized "
                    f"profit {_format_money_text(realized)}; review liquidity and payment exposure"
                ), value=round(withdrawals, 2),
            )
        if (
            login not in funding_uncertain_logins
            and lookback_deposit >= LOW_DEPOSIT_ALERT_THRESHOLD
            and withdrawals >= LOW_DEPOSIT_ALERT_THRESHOLD
            and min(lookback_deposit, withdrawals) >= max(lookback_deposit, withdrawals) * 0.8
        ):
            churn_ratio = round(min(lookback_deposit, withdrawals) / max(lookback_deposit, withdrawals) * 100.0, 1)
            add_event(
                "funding_churn", login,
                evidence=(
                    f"Deposits {_format_money_text(lookback_deposit)} and withdrawals "
                    f"{_format_money_text(withdrawals)} are {churn_ratio}% matched inside the lookback; "
                    "review payment cycling and bonus-abuse exposure"
                ), value=churn_ratio,
            )
        winning_rows = [row for row in rows if row["pnl"] > 0]
        gross_winning_profit = sum(row["pnl"] for row in winning_rows)
        largest_winner = max(winning_rows, key=lambda row: row["pnl"], default=None)
        if (
            len(rows) >= concentration_min_trades and largest_winner and gross_winning_profit >= LOW_DEPOSIT_HIGH_PROFIT_MIN
            and largest_winner["pnl"] >= gross_winning_profit * 0.8
        ):
            concentration_pct = round(largest_winner["pnl"] / gross_winning_profit * 100.0, 1)
            add_event(
                "single_trade_profit_concentration", login,
                symbol=largest_winner["symbol"], occurred_at=largest_winner["close_time"],
                position_id=largest_winner["position_id"],
                evidence=(
                    f"One trade generated {concentration_pct}% of gross winning profit "
                    f"({_format_money_text(largest_winner['pnl'])} of {_format_money_text(gross_winning_profit)})"
                ), value=concentration_pct,
                triggering_values={"completed_trades": len(rows), "winning_trades": len(winning_rows), "largest_winner_pnl": largest_winner["pnl"], "gross_positive_realized_pnl": round(gross_winning_profit, 2), "concentration_pct": concentration_pct},
            )
        winning_profit_by_symbol: dict[str, float] = defaultdict(float)
        for row in winning_rows:
            winning_profit_by_symbol[row["symbol"] or "Unknown"] += row["pnl"]
        if len(rows) >= concentration_min_trades and len(winning_rows) >= 2 and gross_winning_profit >= LOW_DEPOSIT_HIGH_PROFIT_MIN:
            top_symbol, top_symbol_profit = max(winning_profit_by_symbol.items(), key=lambda item: item[1])
            if top_symbol_profit >= gross_winning_profit * 0.8:
                symbol_pct = round(top_symbol_profit / gross_winning_profit * 100.0, 1)
                add_event(
                    "symbol_profit_concentration", login, symbol=top_symbol,
                    evidence=(
                        f"{top_symbol} generated {symbol_pct}% of gross winning profit "
                        f"({_format_money_text(top_symbol_profit)} of {_format_money_text(gross_winning_profit)})"
                    ), value=symbol_pct,
                    triggering_values={"completed_trades": len(rows), "winning_trades": len(winning_rows), "symbol_profit": round(top_symbol_profit, 2), "gross_positive_realized_pnl": round(gross_winning_profit, 2), "concentration_pct": symbol_pct},
                )
        if realized >= max(minimum_total_realized_pnl, sharp_profit_threshold):
            add_event("achieved_profit", login, evidence=f"Realized P&L reached {_format_money_text(realized)} in the live lookback", value=round(realized, 2))

    # Current exposure/account detectors.
    normalized_by_login: dict[int, list[dict[str, Any]]] = defaultdict(list)
    symbol_specs: dict[str, Any] = {}
    symbol_get = getattr(api_handle, "SymbolGet", None)
    for position in positions:
        login = _login_from_record(position)
        if login is None:
            continue
        symbol = str(_value(position, "Symbol", "symbol", default="") or "")
        if symbol not in symbol_specs:
            try:
                symbol_specs[symbol] = symbol_get(symbol) if symbol_get else None
            except Exception:
                symbol_specs[symbol] = None
        spec = symbol_specs[symbol]
        contract_size = _to_float(_value(position, "ContractSize", "contract_size", default=None), 0.0) or _to_float(_value(spec, "ContractSize", "contract_size", "TradeContractSize", default=None), 0.0)
        price = _to_float(_value(position, "PriceCurrent", "price_current", default=None), 0.0)
        account_currency = str(users_by_login.get(login, {}).get("currency") or _value(accounts.get(login), "Currency", "currency", default="") or "").upper()
        profit_currency = str(_value(spec, "CurrencyProfit", "currency_profit", default="") or "").upper()
        conversion = _to_float(_value(position, "RateProfit", "rate_profit", default=None), 0.0)
        if account_currency and profit_currency and account_currency == profit_currency:
            conversion = 1.0
        elif conversion <= 0:
            conversion = 0.0
        lots = max(_position_volume_lots(position), 0.0)
        normalized_by_login[login].append({
            "position_id": _to_int(_value(position, "Position", "position", "Ticket", "ticket"), 0) or 0,
            "symbol": symbol,
            "side": _position_action(position),
            "lots": lots, "contract_size": contract_size, "price": price, "currency_conversion": conversion,
            "exposure": lots * contract_size * price * conversion if contract_size > 0 and price > 0 and conversion > 0 else None,
            "profit": _to_float(_value(position, "Profit", "profit"), 0.0),
        })
    for login, rows in normalized_by_login.items():
        gross_lots = sum(row["lots"] for row in rows)
        account = accounts.get(login)
        balance = _to_float(_value(account, "Balance", "balance"), 0.0)
        equity_value = _value(account, "Equity", "equity", default=None)
        equity = _to_float(equity_value, 0.0) if equity_value is not None else None
        capital = equity if equity is not None and equity > 0 else balance if balance > 0 else None
        complete_exposure = all(row["exposure"] is not None for row in rows)
        gross_exposure = sum(row["exposure"] or 0.0 for row in rows)
        if gross_lots >= large_account_lots and capital and complete_exposure and gross_exposure / capital >= large_account_exposure_ratio:
            add_event("large_volume_account", login, evidence=f"{round(gross_lots, 2)} lots, exposure {round(gross_exposure, 2)} vs capital {round(capital, 2)} ({round(gross_exposure/capital, 2)}x)", value=round(gross_exposure / capital, 2), triggering_values={"gross_lots": gross_lots, "gross_exposure": gross_exposure, "capital": capital, "exposure_ratio": gross_exposure / capital}, thresholds={"minimum_lots": large_account_lots, "minimum_exposure_ratio": large_account_exposure_ratio})
        sides_by_symbol: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            sides_by_symbol[row["symbol"]].add(row["side"])
            if row["lots"] >= large_order_lots and capital and row["exposure"] is not None and row["exposure"] / capital >= large_order_exposure_ratio:
                add_event("large_volume_order", login, symbol=row["symbol"], evidence=f"Open position #{row['position_id']} is {round(row['lots'], 2)} lots, converted exposure {round(row['exposure'], 2)} vs capital {round(capital, 2)} ({round(row['exposure']/capital, 2)}x)", value=round(row["exposure"] / capital, 2), position_id=row["position_id"], triggering_values={"lots": row["lots"], "contract_size": row["contract_size"], "price": row["price"], "currency_conversion": row["currency_conversion"], "exposure": row["exposure"], "capital": capital, "exposure_ratio": row["exposure"] / capital}, thresholds={"minimum_lots": large_order_lots, "minimum_exposure_ratio": large_order_exposure_ratio})
        for symbol, sides in sides_by_symbol.items():
            if {"BUY", "SELL"}.issubset(sides):
                add_event("hedge_accounts", login, symbol=symbol, evidence="Simultaneous BUY and SELL exposure on the same symbol; risk/behavior review only", triggering_values={"symbol": symbol, "buy_positions": sum(row["symbol"] == symbol and row["side"] == "BUY" for row in rows), "sell_positions": sum(row["symbol"] == symbol and row["side"] == "SELL" for row in rows)}, thresholds={"minimum_buy_positions": 1, "minimum_sell_positions": 1})
        margin_level = _to_float(_value(account, "MarginLevel", "margin_level"), 0.0)
        if equity is not None and equity <= mini_equity_threshold:
            add_event("mini_accounts", login, evidence=f"Active equity is {_format_money_text(equity)}", value=round(equity, 2), triggering_values={"equity": equity})
        if (equity is not None and equity <= 0) or (margin_level > 0 and margin_level <= stopout_margin_level):
            equity_text = _format_money_text(equity) if equity is not None else "unavailable"
            add_event("failed_stopouts", login, severity="critical", evidence=f"{len(rows)} open positions remain; equity {equity_text}, margin level {round(margin_level, 2)}%", value=round(margin_level, 2), triggering_values={"open_positions": len(rows), "equity": equity, "margin_level": margin_level}, data_quality="VERIFIED" if equity is not None and _value(account, "MarginLevel", "margin_level", default=None) is not None else "PARTIAL")

    # Latency arbitrage: only profitable short-duration trades are tick-checked.
    latency_candidates = [
        row for row in all_lifecycles
        if row["pnl"] >= profit_threshold
        and row["hold_seconds"] <= maximum_duration_minutes * 60
        and (not check_total_realized_pnl or total_pnl_by_login.get(row["login"], 0.0) >= minimum_total_realized_pnl)
    ]
    latency_data_available = False
    checked_candidates = 0
    tick_errors: list[str] = []
    unchecked_candidates: list[dict[str, Any]] = []
    for row in sorted(latency_candidates, key=lambda item: item["pnl"], reverse=True)[:ANTIFRAUD_MAX_TICK_CHECKS]:
        ticks, err = request_tick_history(api_handle, row["symbol"], row["open_ts"] - max(int(gap_seconds * 4), 30), row["open_ts"] + 3)
        if err:
            tick_errors.append(f"{row['symbol']}: {err}")
            unchecked_candidates.append({"login": row["login"], "position_id": row["position_id"], "status": "UNCHECKED", "reason": err})
            continue
        times = sorted({_tick_time_msc(tick) for tick in ticks if _tick_time_msc(tick) > 0 and _to_float(_value(tick, "bid", "Bid"), 0.0) > 0 and _to_float(_value(tick, "ask", "Ask"), 0.0) > 0})
        open_msc = row["open_time_msc"]
        if not open_msc or len(times) < 2 or not (times[0] < open_msc <= times[-1]):
            unchecked_candidates.append({"login": row["login"], "position_id": row["position_id"], "status": "UNCHECKED", "reason": "Missing precise MT5 open time or complete valid bid/ask tick coverage"})
            continue
        latency_data_available = True
        checked_candidates += 1
        matching_gap = None
        for previous, current in zip(times, times[1:]):
            gap = (current - previous) / 1000.0
            if gap >= gap_seconds and current == open_msc:
                matching_gap = gap
                break
        if matching_gap is not None:
            add_event("latency_arbitrage", row["login"], severity="warning", symbol=row["symbol"], occurred_at=row["open_time"], evidence=f"Timing indicator: exact MT5 open time matches the first quote after a {round(matching_gap, 3)}s tick gap; closed in {round(row['hold_seconds']/60, 2)}m for {_format_money_text(row['pnl'])}. Dealer review required; timing is not proof of fraud.", value=row["pnl"], position_id=row["position_id"], triggering_values={"open_time_msc": open_msc, "gap_seconds": matching_gap, "realized_pnl": row["pnl"], "hold_seconds": row["hold_seconds"]}, thresholds={"minimum_gap_seconds": gap_seconds, "minimum_profit": profit_threshold, "maximum_duration_minutes": maximum_duration_minutes})

    agent_postings = [deal for deal in deals if _to_int(_value(deal, "Action", "action"), -1) in {DEAL_COMMISSION_AGENT_DAILY, DEAL_COMMISSION_AGENT_MONTHLY, DEAL_COMMISSION_AGENT_INSTANT}]
    agent_commission = sum(
        _to_float(_value(deal, "Profit", "profit", default=None) if _value(deal, "Profit", "profit", default=None) is not None else _value(deal, "Commission", "commission", default=None), 0.0)
        for deal in agent_postings
    )
    if agent_postings:
        add_event("agent_commissions", 0, severity="info", evidence=f"{len(agent_postings)} agent commission postings total {_format_money_text(agent_commission)}", value=round(agent_commission, 2), triggering_values={"posting_count": len(agent_postings), "commission_total": round(agent_commission, 2)})

    # Normalize and correlate once, after all detectors have evaluated the same MT5 snapshot.
    rule_thresholds = {
        "sharp_deals": {"minimum_profit": sharp_profit_threshold, "maximum_duration_minutes": maximum_duration_minutes},
        "news_trading": {"window_minutes": news_window_minutes, "impacts": sorted(selected_news_impacts)},
        "achieved_profit": {"minimum_realized_pnl": max(minimum_total_realized_pnl, sharp_profit_threshold)},
        "low_deposit_high_profit": {"maximum_deposits": LOW_DEPOSIT_ALERT_THRESHOLD, "minimum_profit": LOW_DEPOSIT_HIGH_PROFIT_MIN, "profit_multiple": LOW_DEPOSIT_PROFIT_MULTIPLE},
        "lifetime_deposit_high_profit": {"maximum_deposits": LOW_DEPOSIT_ALERT_THRESHOLD, "minimum_profit": LOW_DEPOSIT_HIGH_PROFIT_MIN, "profit_multiple": LOW_DEPOSIT_PROFIT_MULTIPLE},
        "starting_capital_high_profit": {"maximum_capital": LOW_DEPOSIT_ALERT_THRESHOLD, "minimum_profit": LOW_DEPOSIT_HIGH_PROFIT_MIN, "profit_multiple": LOW_DEPOSIT_PROFIT_MULTIPLE},
        "mini_accounts": {"maximum_equity": mini_equity_threshold},
        "failed_stopouts": {"maximum_margin_level": stopout_margin_level, "maximum_equity": 0},
        "repeated_funding": {"minimum_deposits": 3},
        "deposit_alert": {"minimum_deposit_amount": deposit_alert_threshold},
        "profit_withdrawal_pressure": {"minimum_withdrawals": LOW_DEPOSIT_ALERT_THRESHOLD, "profit_fraction": 0.8},
        "funding_churn": {"minimum_deposits": LOW_DEPOSIT_ALERT_THRESHOLD, "minimum_withdrawals": LOW_DEPOSIT_ALERT_THRESHOLD, "match_fraction": 0.8},
        "single_trade_profit_concentration": {"minimum_completed_trades": concentration_min_trades, "minimum_gross_positive_pnl": LOW_DEPOSIT_HIGH_PROFIT_MIN, "minimum_fraction": 0.8},
        "symbol_profit_concentration": {"minimum_completed_trades": concentration_min_trades, "minimum_winning_trades": 2, "minimum_gross_positive_pnl": LOW_DEPOSIT_HIGH_PROFIT_MIN, "minimum_fraction": 0.8},
    }
    funding_profit_rules = {"low_deposit_high_profit", "lifetime_deposit_high_profit", "starting_capital_high_profit"}
    preferred_funding_rule = {"starting_capital_high_profit": 3, "lifetime_deposit_high_profit": 2, "low_deposit_high_profit": 1}
    best_funding: dict[int, dict[str, Any]] = {}
    for event in events:
        if event["rule"] in funding_profit_rules:
            login = event["login"]
            if preferred_funding_rule[event["rule"]] > preferred_funding_rule.get(best_funding.get(login, {}).get("rule"), 0):
                best_funding[login] = event
    normalized_events: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for event in events:
        rule, login = event["rule"], event["login"]
        if rule in funding_profit_rules and best_funding.get(login) is not event:
            continue
        event["thresholds"] = event["thresholds"] or rule_thresholds.get(rule, {})
        values = event["triggering_values"]
        if login in lifecycles_by_login:
            values.setdefault("completed_trades", len(lifecycles_by_login[login]))
            values.setdefault("realized_pnl", round(total_pnl_by_login.get(login, 0.0), 2))
        if rule in funding_profit_rules | {"repeated_funding", "profit_withdrawal_pressure", "funding_churn", "deposit_alert"}:
            values.update({"lookback_deposits": round(total_deposit_by_login.get(login, 0.0), 2), "lookback_withdrawals": round(lookback_withdrawals_by_login.get(login, 0.0), 2), "lifetime_deposits": round(lifetime_deposits_by_login.get(login, 0.0), 2), "starting_capital": round(max(starting_balance_by_login.get(login, 0.0), 0.0) + total_deposit_by_login.get(login, 0.0), 2)})
            event["data_quality"] = "PARTIAL"  # Balance actions alone cannot prove external payment origin.
        if rule in funding_profit_rules:
            event["correlated_rules"] = sorted(candidate["rule"] for candidate in events if candidate["login"] == login and candidate["rule"] in funding_profit_rules and candidate is not event)
        identity = ([rule, login, event.get("position_id"), values.get("calendar_event_ts")]
                    if rule == "news_trading" else [rule, login, event.get("position_id"), event.get("deal_id"), event.get("symbol"), event.get("evidence")])
        event["event_id"] = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:16]
        if event["event_id"] in seen_event_ids:
            continue
        seen_event_ids.add(event["event_id"])
        normalized_events.append(event)
    events = normalized_events
    related = defaultdict(list)
    for event in events:
        related[(event["login"], event["category"])].append(event["event_id"])
    for event in events:
        event["related_event_ids"] = [identifier for identifier in related[(event["login"], event["category"])][:21] if identifier != event["event_id"]][:20]

    counts = Counter(event["detector"] for event in events)
    if not deals:
        warnings.append(
            f"No MT5 deals were returned for {len(groups)} selected group(s) in the "
            f"{lookback_hours}-hour lookback. Deal-based detectors have no data to evaluate."
        )
    detector_defs = [
        ("latency_arbitrage", "Fraud indicators", "Latency Timing Anomaly", "Exact trade timing at a verified tick gap is an indicator for review, not proof of fraud.", latency_data_available or not latency_candidates, "TickHistoryRequest"),
        ("short_hold_scalping", "Behavioral indicators", "Short-Hold Scalping", "Profitable short-hold trades without sufficient high-frequency evidence.", True, "Deals"),
        ("scalpers_hft", "Behavioral indicators", "High-Frequency Scalping", "Short-hold trades plus repeated rapid inter-trade intervals.", True, "Deals"),
        ("sharp_deals", "Trading", "Sharp Deals", "Unusually profitable transactions closed inside the configured duration.", True, "Deals"),
        ("news_trading", "Trading", "News Trading", "Trades opened near matching-currency economic calendar releases at the selected impact levels.", bool(calendar_source), "Calendar API + Deals"),
        ("achieved_profit", "Trading", "Achieved Profit", "Accounts exceeding realized P&L thresholds in the live lookback.", True, "Deals"),
        ("low_deposit_high_profit", "Trading", "Lookback Deposit / High Profit", "Recent profit compared with deposits inside the selected lookback.", True, "Lookback deals + balance postings"),
        ("lifetime_deposit_high_profit", "Trading", "Lifetime Deposits / High Profit", "Recent profit compared with all cumulative account deposits.", True, "Lifetime deals + balance postings"),
        ("starting_capital_high_profit", "Trading", "Starting Capital / High Profit", "Recent profit compared with starting balance plus lookback deposits.", True, "Lifetime deals + balance postings"),
        ("hedge_accounts", "Risk", "Hedge Accounts", "Simultaneous opposing exposure on the same symbol.", True, "Positions"),
        ("failed_stopouts", "Risk", "Failed Stop Outs", "Open positions remain at critical equity or margin level.", True, "Accounts + positions"),
        ("balance_below_credit", "Fraud indicators", "Balance Below Credit", "Alerts when an account with positive credit has a balance below its credit amount.", balance_credit_checked > 0, "MT5 account balance + credit"),
        ("mini_accounts", "Risk", "Mini Accounts", "Active accounts below the configured equity threshold.", True, "Accounts"),
        ("large_volume_account", "Trade Flow", "Large Volumes by Account", "Summarized open volume exceeds the account threshold.", True, "Positions"),
        ("large_volume_order", "Trade Flow", "Large Volumes by Order", "Individual open position exceeds the lot threshold.", True, "Positions"),
        ("agent_commissions", "Affiliate", "Agent Commissions", "Agent commission postings detected in MT5 deals.", True, "Deals"),
        ("repeated_funding", "Funding Risk", "Repeated Funding", "Multiple separate deposits inside the lookback may indicate payment structuring.", True, "Balance postings"),
        ("deposit_alert", "Funding Risk", "Deposit Alert", "Individual MT5 balance deposits meeting the configured amount threshold.", True, "Balance deals"),
        ("profit_withdrawal_pressure", "Funding Risk", "Profit Withdrawal Pressure", "Large withdrawals relative to recent realized profit and broker liquidity.", True, "Deals + balance postings"),
        ("single_trade_profit_concentration", "Funding Risk", "Single-Trade Profit Concentration", "One deal generated at least 80% of gross winning profit.", True, "Closed deals"),
        ("funding_churn", "Funding Risk", "Deposit / Withdrawal Churn", "Closely matched deposits and withdrawals may indicate payment cycling or bonus abuse.", True, "Balance postings"),
        ("symbol_profit_concentration", "Funding Risk", "Symbol Profit Concentration", "One symbol generated at least 80% of gross winning profit across multiple trades.", True, "Closed deals"),
        ("rejected_orders", "Additional", "Rejected Orders", "Requires retained server request logs.", False, "Server request journal"),
        ("trade_request_flood", "Additional", "Trade Requests Flood", "Requires timestamped server request logs.", False, "Server request journal"),
        ("ip_control", "Additional", "IP Control", "Accounts sharing a public MT5 LastIP; last-login snapshot only.", ip_evidence_available, "MTUser.LastIP"),
        ("cid_control", "Additional", "CID Control", "Requires CID values in server login/execution journals.", False, "Server connection journal"),
        ("invalid_balances", "Additional", "Invalid Balances", "Requires a full balance ledger baseline, not a partial lookback.", False, "Full account ledger"),
        ("overdue_credits", "Additional", "Overdue Credits", "Requires credit-expiration metadata not exposed by this profile.", False, "Credit expiration"),
        ("stealing_quotes", "Additional", "Stealing Quotes", "Requires gateway/Data Feed Center connection journals.", False, "Gateway journal"),
        ("affiliate_cheating", "Affiliate", "Affiliate Cheating", "Agent and referred client sharing a public MT5 LastIP; review signal only.", affiliate_evidence_available, "MTUser.Agent + LastIP"),
    ]
    deal_detector_keys = {
        "latency_arbitrage", "short_hold_scalping", "scalpers_hft", "sharp_deals", "achieved_profit",
        "news_trading",
        "low_deposit_high_profit", "lifetime_deposit_high_profit",
        "starting_capital_high_profit", "agent_commissions", "repeated_funding", "deposit_alert",
        "profit_withdrawal_pressure", "single_trade_profit_concentration",
        "funding_churn", "symbol_profit_concentration",
    }
    cards = []
    for key, category, name, description, available, source in detector_defs:
        category = "Fraud indicators" if key in fraud_rules else "Behavioral indicators" if key in behavior_rules else "Risk indicators" if available else "Data required"
        status = "active" if available else "data_required"
        if status == "active" and key in deal_detector_keys and not deals:
            status = "no_data"
        if status == "active" and key == "news_trading" and not news_rows:
            status = "no_data"
        cards.append({
            "key": key, "category": category, "name": name, "description": description,
            "events": counts.get(key, 0), "status": status, "source": source,
        })
    severity_order = {"critical": 0, "high": 1, "warning": 2, "info": 3}
    events.sort(key=lambda event: (severity_order.get(event["severity"], 9), -(event["value"] or 0)))
    if latency_candidates and not latency_data_available:
        warnings.append("Latency Arbitrage: candidate trades exist, but MT5 returned no ticks for their symbols/times.")
    warnings.extend(tick_errors[:5])
    if news_error:
        warnings.append(f"News Trading: {news_error}")
    client_events: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        login = _to_int(event.get("login"), 0)
        if login > 0:
            client_events[login].append(event)
    ip_values = sorted({
        str(users_by_login.get(login, {}).get("last_ip", "")).strip()
        for login in set(client_events)
        if str(users_by_login.get(login, {}).get("last_ip", "")).strip()
    })
    # Anti-fraud must not wait on an external geolocation service. Reuse any
    # intelligence already populated by live risk; uncached IPs remain clearly
    # marked unavailable until that background/live path resolves them.
    client_ip_details = enrich_ip_addresses(ip_values, allow_network=False) if ip_values else {}
    client_summaries: list[dict[str, Any]] = []
    for login in sorted(set(client_events) | set(lifecycles_by_login) | set(normalized_by_login)):
        lifecycle_rows = lifecycles_by_login.get(login, [])
        open_rows = normalized_by_login.get(login, [])
        event_rows = client_events.get(login, [])
        if not lifecycle_rows and not open_rows and not event_rows:
            continue
        analysis = analyse_client_lifecycles(lifecycle_rows, scalping_seconds=scalp_seconds) if lifecycle_rows else {
            "metrics": {"toxicity_score": 0.0},
            "patterns": {},
        }
        detector_counts = Counter(str(event.get("detector") or "") for event in event_rows)
        last_ip = str(users_by_login.get(login, {}).get("last_ip", "") or "")
        ip_info = client_ip_details.get(last_ip, {})
        severity = "critical" if any(event.get("severity") == "critical" for event in event_rows) else "high" if any(event.get("severity") == "high" for event in event_rows) else "warning" if event_rows else "info"
        signals = []
        if detector_counts.get("failed_stopouts"):
            signals.append("failed_stopouts")
        if detector_counts.get("latency_arbitrage"):
            signals.append("latency_arbitrage")
        if detector_counts.get("scalpers_hft"):
            signals.append("scalpers_hft")
        if detector_counts.get("large_volume_order") or detector_counts.get("large_volume_account"):
            signals.append("large_volume")
        if detector_counts.get("low_deposit_high_profit"):
            signals.append("low_deposit_high_profit")
        if detector_counts.get("lifetime_deposit_high_profit"):
            signals.append("lifetime_deposit_high_profit")
        if detector_counts.get("starting_capital_high_profit"):
            signals.append("starting_capital_high_profit")
        if ip_info.get("vps_status") == "detected":
            signals.append("vps_detected")
        account = accounts.get(login)
        balance = _to_float(_value(account, "Balance", "balance"), 0.0)
        equity_value = _value(account, "Equity", "equity", default=None)
        equity = _to_float(equity_value, 0.0) if equity_value is not None else None
        floating_pnl = round(sum(row["profit"] for row in open_rows), 2)
        if balance > 0 and balance <= SMALL_BALANCE_ALERT_THRESHOLD and floating_pnl >= max(OUTSIZED_PROFIT_MIN_USD, balance * OUTSIZED_PROFIT_MULTIPLE):
            signals.append("small_balance_outsized_profit")
        client_summaries.append({
            "login": login,
            "name": users_by_login.get(login, {}).get("name", ""),
            "group": group_map.get(login, ""),
            "severity": severity,
            "toxicity_score": round(_to_float(analysis.get("metrics", {}).get("toxicity_score"), 0.0), 1),
            "last_ip": last_ip,
            "location": ip_info.get("location", ""),
            "provider": ip_info.get("provider", ""),
            "asn": ip_info.get("asn", ""),
            "vps_status": ip_info.get("vps_status", "unknown"),
            "vps_confidence": ip_info.get("vps_confidence", 0),
            "balance": round(balance, 2),
            "total_deposit": round(total_deposit_by_login.get(login, 0.0), 2),
            "lookback_deposit": round(total_deposit_by_login.get(login, 0.0), 2),
            "lifetime_deposits": round(lifetime_deposits_by_login.get(login, 0.0), 2),
            "starting_balance": round(max(starting_balance_by_login.get(login, 0.0), 0.0), 2),
            "starting_capital": round(
                max(starting_balance_by_login.get(login, 0.0), 0.0)
                + total_deposit_by_login.get(login, 0.0), 2
            ),
            "total_deals": len(lifecycle_rows),
            "total_profit": round(sum(row["pnl"] for row in lifecycle_rows), 2),
            "scalp_deals": sum(1 for row in lifecycle_rows if row["hold_seconds"] <= scalp_seconds and row["pnl"] > 0),
            "scalp_profit": round(sum(row["pnl"] for row in lifecycle_rows if row["hold_seconds"] <= scalp_seconds and row["pnl"] > 0), 2),
            "latency_deals": detector_counts.get("latency_arbitrage", 0),
            "latency_profit": round(sum(_to_float(event.get("value"), 0.0) for event in event_rows if event.get("detector") == "latency_arbitrage"), 2),
            "sharp_deals": detector_counts.get("sharp_deals", 0),
            "sharp_profit": round(sum(_to_float(event.get("value"), 0.0) for event in event_rows if event.get("detector") == "sharp_deals"), 2),
            "large_order_count": detector_counts.get("large_volume_order", 0),
            "large_order_lots": round(sum(_to_float((event.get("triggering_values") or {}).get("lots"), 0.0) for event in event_rows if event.get("detector") == "large_volume_order"), 2),
            "open_positions": len(open_rows),
            "gross_open_lots": round(sum(_to_float(row.get("lots"), 0.0) for row in open_rows), 2),
            "floating_pnl": floating_pnl,
            "equity": round(equity, 2) if equity is not None else None,
            "total_events": len(event_rows),
            "signals": signals,
            "recent_evidence": [str(event.get("evidence") or "") for event in event_rows[:5] if str(event.get("evidence") or "").strip()],
        })
    return {
        "generated_at": now.isoformat(), "selected_groups": groups,
        "summary": {
            "events": len(events), "critical": sum(event["severity"] == "critical" for event in events),
            "accounts": len({event["login"] for event in events if event["login"]}),
            "active_detectors": sum(card["status"] == "active" for card in cards),
            "data_required": sum(card["status"] == "data_required" for card in cards),
            "deals_scanned": len(deals), "positions_scanned": len(positions),
            "latency_candidates": len(latency_candidates), "latency_checked": checked_candidates,
            "no_data_detectors": sum(card["status"] == "no_data" for card in cards),
            "scan_duration_ms": round((time_module.monotonic() - scan_started) * 1000),
        },
        "settings": {
            "lookback_hours": lookback_hours, "gap_seconds": gap_seconds, "profit_threshold": profit_threshold,
            "check_total_realized_pnl": check_total_realized_pnl, "minimum_total_realized_pnl": minimum_total_realized_pnl,
            "maximum_duration_minutes": maximum_duration_minutes, "scalp_seconds": scalp_seconds,
            "large_order_lots": large_order_lots, "large_account_lots": large_account_lots,
        },
        "detectors": cards, "events": events[:2000], "unchecked_candidates": unchecked_candidates, "client_summaries": client_summaries[:500], "warnings": _dedupe_warnings(warnings),
    }


def build_performance_snapshot(
    api_handle,
    groups: list[str],
    excluded_logins=None,
    lookback_days: int = 365,
    cached_users: list[dict[str, Any]] | None = None,
    request_groups: list[str] | None = None,
):
    """Build daily, weekly, monthly and rolling-overall realized P&L leaderboards."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=lookback_days)
    excluded = _normalize_login_set(excluded_logins)
    if cached_users is not None:
        allowed_groups = set(groups)
        scoped_users = [
            row for row in cached_users
            if str(row.get("Group", "")) in allowed_groups
            and _to_int(row.get("Login"), 0) not in excluded
        ]
        users_by_login = {
            int(row["Login"]): {
                "name": str(row.get("Name", "")),
                "currency": str(row.get("Currency", "USD")),
                "last_ip": str(row.get("LastIP", "")),
            }
            for row in scoped_users if _to_int(row.get("Login"), 0) > 0
        }
        group_map = {
            int(row["Login"]): str(row.get("Group", ""))
            for row in scoped_users if _to_int(row.get("Login"), 0) > 0
        }
        warnings = []
    else:
        users_by_login, group_map, warnings = _collect_user_scope(
            api_handle, groups, excluded
        )
    allowed_logins = set(users_by_login)
    # The bounded 31-day UI window is fast on the broker's bulk-login history
    # path and avoids native per-group requests that can stall on large groups.
    deals, deal_warnings = _fetch_scoped_deals(
        api_handle,
        groups,
        int(start.timestamp()),
        int(now.timestamp()),
        allowed_logins,
        request_groups=request_groups,
    )
    warnings.extend(deal_warnings)
    day_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    week_start = day_start - timedelta(days=day_start.weekday())
    month_start = day_start.replace(day=1)
    periods = {
        "daily": day_start,
        "weekly": week_start,
        "monthly": month_start,
        "overall": start,
    }
    totals = {key: defaultdict(float) for key in periods}
    trades = {key: defaultdict(int) for key in periods}
    trade_deals = 0
    closing_deals = 0
    missing_timestamps = 0
    for deal in deals:
        login = _login_from_record(deal)
        if login not in allowed_logins:
            continue
        action = _to_int(_value(deal, "Action", "action"), None)
        volume = _deal_volume_units(deal)
        if not _is_trade_deal(deal, action, volume):
            continue
        trade_deals += 1
        entry = _to_int(_value(deal, "Entry", "entry"), None)
        entry_text = str(_value(deal, "Entry", "entry", "EntryType", "entry_type", default="") or "").strip().lower()
        profit = _to_float(_value(deal, "Profit", "profit"), 0.0)
        is_close = entry in CLOSED_ENTRIES or entry_text in {
            "out", "out_by", "inout", "entry_out", "entry_out_by", "entry_inout", "close", "closed",
        }
        # Some wrappers omit Entry while still returning a realized trade
        # posting. Non-zero Profit is safe evidence that this is not an opening leg.
        if entry is None and not entry_text and profit != 0.0:
            is_close = True
        if not is_close:
            continue
        closed_ts = _deal_timestamp(deal)
        if closed_ts <= 0:
            missing_timestamps += 1
            continue
        closing_deals += 1
        closed = datetime.fromtimestamp(closed_ts, tz=timezone.utc)
        realized_pnl = (
            profit
            + _to_float(_value(deal, "Commission", "commission"), 0.0)
            + _to_float(_value(deal, "Fee", "fee"), 0.0)
            + _to_float(_value(deal, "Storage", "storage", "Swap", "swap"), 0.0)
        )
        for key, boundary in periods.items():
            if closed >= boundary:
                totals[key][login] += realized_pnl
                trades[key][login] += 1

    def leaderboard(key: str):
        ranked = [{
            "login": login,
            "name": users_by_login.get(login, {}).get("name", ""),
            "group": group_map.get(login, ""),
            "pnl": round(value, 2),
            "trades": trades[key][login],
        } for login, value in totals[key].items() if value]
        ranked.sort(key=lambda item: item["pnl"], reverse=True)
        winners = [item for item in ranked if item["pnl"] > 0][:25]
        losers = sorted(
            (item for item in ranked if item["pnl"] < 0),
            key=lambda item: item["pnl"],
        )[:25]
        return {
            "winners": winners,
            "losers": losers,
            "accounts": ranked[:5000],
            "net_pnl": round(sum(totals[key].values()), 2),
        }

    return {
        "generated_at": now.isoformat(), "selected_groups": groups,
        "overall_window_days": lookback_days,
        "periods": {key: leaderboard(key) for key in periods},
        "data_quality": {
            "deals_returned": len(deals),
            "trade_deals": trade_deals,
            "closing_deals": closing_deals,
            "closing_deals_missing_timestamp": missing_timestamps,
            "timezone": "UTC",
            "period_starts": {key: boundary.isoformat() for key, boundary in periods.items()},
        },
        "warnings": _dedupe_warnings(warnings),
    }


def _performance_cache_key(req: "PerformanceRequest") -> tuple[Any, ...]:
    """Canonical key for an expensive historical leaderboard query."""
    return (
        tuple(sorted({str(group).strip() for group in req.selected_groups if str(group).strip()})),
        tuple(sorted(_normalize_login_set(req.excluded_logins))),
        str(req.group_filter_rules or "").strip(),
        int(req.lookback_days),
    )


def _performance_cache_db_key(key: tuple[Any, ...]) -> str:
    return hashlib.sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()


def _get_cached_performance(
    key: tuple[Any, ...], *, allow_stale: bool = False
) -> dict[str, Any] | None:
    now = time_module.monotonic()
    with _PERFORMANCE_CACHE_LOCK:
        cached = _PERFORMANCE_CACHE.get(key)
        if cached is not None:
            age = now - cached[0]
            if age < PERFORMANCE_CACHE_SECONDS or allow_stale:
                payload = copy.deepcopy(cached[1])
                payload["cache"] = {
                    "hit": True, "stale": age >= PERFORMANCE_CACHE_SECONDS,
                    "ttl_seconds": PERFORMANCE_CACHE_SECONDS,
                }
                return payload
    if not allow_stale:
        return None
    with _conn() as con:
        row = con.execute(
            "SELECT payload_json, generated_at FROM performance_snapshots WHERE cache_key=?",
            (_performance_cache_db_key(key),),
        ).fetchone()
    if not row:
        return None
    payload = _json_column(row["payload_json"], {})
    payload["cache"] = {
        "hit": True, "stale": True, "persistent": True,
        "stored_at": row["generated_at"], "ttl_seconds": PERFORMANCE_CACHE_SECONDS,
    }
    return payload


def _store_cached_performance(key: tuple[Any, ...], payload: dict[str, Any]) -> None:
    with _PERFORMANCE_CACHE_LOCK:
        if len(_PERFORMANCE_CACHE) >= 64:
            oldest = min(_PERFORMANCE_CACHE, key=lambda item: _PERFORMANCE_CACHE[item][0])
            _PERFORMANCE_CACHE.pop(oldest, None)
        _PERFORMANCE_CACHE[key] = (time_module.monotonic(), copy.deepcopy(payload))
    with _conn() as con:
        con.execute(
            """INSERT INTO performance_snapshots(cache_key,payload_json,generated_at)
               VALUES (?,?,datetime('now')) ON CONFLICT(cache_key) DO UPDATE SET
               payload_json=excluded.payload_json, generated_at=datetime('now')""",
            (_performance_cache_db_key(key), json.dumps(payload)),
        )


def build_stress_snapshot(
    api_handle,
    groups: list[str],
    excluded_logins: list[int] | None = None,
    *,
    shock_percent: float = -5.0,
    margin_level_floor: float = 120.0,
    concentration_limit: float = 70.0,
    symbol_gross_limit: float = 1_000_000.0,
) -> dict[str, Any]:
    excluded = _normalize_login_set(excluded_logins)
    users_by_login, group_map, warnings = _collect_user_scope(api_handle, groups, excluded)
    allowed_logins = set(users_by_login)
    positions: list[Any] = []
    seen_positions: set[int] = set()
    for group in groups:
        rows, err = request_positions_by_group(api_handle, group)
        if err:
            warnings.append(f"{group}: {err}")
            continue
        for position in rows:
            login = _login_from_record(position)
            ticket = _to_int(_value(position, "Position", "position", "Ticket", "ticket"), 0) or 0
            if login not in allowed_logins or (ticket and ticket in seen_positions):
                continue
            if ticket:
                seen_positions.add(ticket)
            positions.append(position)

    accounts, account_warnings = request_accounts_by_logins(api_handle, allowed_logins)
    warnings.extend(account_warnings)
    symbol_configs: dict[str, Any] = {}
    symbol_method = getattr(api_handle, "SymbolGet", None)
    symbol_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "symbol": "", "buy_lots": 0.0, "sell_lots": 0.0, "gross_lots": 0.0,
        "gross_exposure": 0.0, "net_exposure": 0.0, "floating_pnl": 0.0,
        "shock_pnl": 0.0, "positions": 0,
    })
    account_positions: dict[int, list[dict[str, Any]]] = defaultdict(list)
    shock_multiplier = float(shock_percent) / 100.0

    for position in positions:
        login = _login_from_record(position)
        if login is None:
            continue
        symbol = str(_value(position, "Symbol", "symbol", default="Unknown") or "Unknown")
        if symbol not in symbol_configs:
            config = None
            if symbol_method is not None:
                try:
                    candidate = symbol_method(symbol)
                    config = None if isinstance(candidate, bool) else candidate
                except Exception:
                    config = None
            symbol_configs[symbol] = config
        config = symbol_configs[symbol]
        lots = max(_position_volume_lots(position), 0.0)
        side = _position_action(position)
        price = _to_float(_value(position, "PriceCurrent", "price_current", "PriceOpen", "price_open"), 0.0)
        contract_size = _to_float(
            _value(position, "ContractSize", "contract_size"),
            _to_float(_value(config, "ContractSize", "contract_size", "TradeContractSize"), 1.0),
        )
        if contract_size <= 0:
            contract_size = 1.0
        exposure = abs(lots * contract_size * price)
        signed_exposure = exposure if side == "BUY" else -exposure
        shock_pnl = signed_exposure * shock_multiplier
        floating = _to_float(_value(position, "Profit", "profit"), 0.0) + _to_float(_value(position, "Storage", "storage", "Swap", "swap"), 0.0)
        normalized = {
            "position_id": _to_int(_value(position, "Position", "position", "Ticket", "ticket"), 0) or 0,
            "login": login, "symbol": symbol, "side": side, "lots": round(lots, 4),
            "price": round(price, 8), "contract_size": contract_size,
            "exposure": round(exposure, 2), "signed_exposure": round(signed_exposure, 2),
            "floating_pnl": round(floating, 2), "shock_pnl": round(shock_pnl, 2),
        }
        account_positions[login].append(normalized)
        aggregate = symbol_rows[symbol]
        aggregate["symbol"] = symbol
        aggregate["buy_lots" if side == "BUY" else "sell_lots"] += lots
        aggregate["gross_lots"] += lots
        aggregate["gross_exposure"] += exposure
        aggregate["net_exposure"] += signed_exposure
        aggregate["floating_pnl"] += floating
        aggregate["shock_pnl"] += shock_pnl
        aggregate["positions"] += 1

    watchlist: list[dict[str, Any]] = []
    account_rows: list[dict[str, Any]] = []
    for login, account in accounts.items():
        rows = account_positions.get(login, [])
        balance = _to_float(_value(account, "Balance", "balance"), 0.0)
        equity_value = _value(account, "Equity", "equity", default=None)
        if equity_value is None:
            warnings.append(f"Login {login}: authoritative equity is unavailable; excluded from stress totals.")
            continue
        equity = _to_float(equity_value, 0.0)
        margin = _to_float(_value(account, "Margin", "margin"), 0.0)
        current_margin_level = _to_float(_value(account, "MarginLevel", "margin_level"), 0.0)
        gross_exposure = sum(row["exposure"] for row in rows)
        symbol_exposure: dict[str, float] = defaultdict(float)
        for row in rows:
            symbol_exposure[row["symbol"]] += row["exposure"]
        concentration = max(symbol_exposure.values(), default=0.0) / max(gross_exposure, 1e-12) * 100.0
        shock_pnl = sum(row["shock_pnl"] for row in rows)
        projected_equity = equity + shock_pnl
        projected_margin_level = projected_equity / margin * 100.0 if margin > 0 else 0.0
        reasons: list[str] = []
        if rows and current_margin_level and current_margin_level < margin_level_floor:
            reasons.append(f"Current margin level < {margin_level_floor}%")
        if rows and margin > 0 and projected_margin_level < margin_level_floor:
            reasons.append(f"Projected margin level < {margin_level_floor}%")
        if rows and concentration >= concentration_limit:
            reasons.append(f"Concentration ≥ {concentration_limit}%")
        if projected_equity <= 0 and rows:
            reasons.append("Projected non-positive equity")
        row = {
            "login": login, "name": users_by_login.get(login, {}).get("name", ""),
            "group": group_map.get(login, ""), "balance": round(balance, 2),
            "equity": round(equity, 2), "margin": round(margin, 2),
            "margin_level": round(current_margin_level, 2), "positions": len(rows),
            "gross_exposure": round(gross_exposure, 2), "concentration_pct": round(concentration, 1),
            "shock_pnl": round(shock_pnl, 2), "projected_equity": round(projected_equity, 2),
            "projected_margin_level": round(projected_margin_level, 2), "reasons": reasons,
        }
        account_rows.append(row)
        if reasons:
            watchlist.append(row)

    policies = []
    group_method = getattr(api_handle, "GroupGet", None)
    for group in groups:
        config = None
        if group_method is not None:
            try:
                candidate = group_method(group)
                config = None if isinstance(candidate, bool) else candidate
            except Exception:
                config = None
        policies.append({
            "group": group,
            "currency": str(_value(config, "Currency", "currency", default="") or ""),
            "margin_call": _to_float(_value(config, "MarginCall", "margin_call"), 0.0),
            "margin_stopout": _to_float(_value(config, "MarginStopout", "margin_stopout"), 0.0),
            "margin_mode": str(_value(config, "MarginMode", "margin_mode", default="") or ""),
            "trade_mode": str(_value(config, "TradeMode", "trade_mode", default="") or ""),
            "leverage_default": _to_int(_value(config, "LeverageDefault", "leverage_default"), 0) or 0,
            "available": config is not None,
        })

    symbols = []
    for aggregate in symbol_rows.values():
        symbols.append({
            **aggregate,
            "buy_lots": round(aggregate["buy_lots"], 2), "sell_lots": round(aggregate["sell_lots"], 2),
            "gross_lots": round(aggregate["gross_lots"], 2), "gross_exposure": round(aggregate["gross_exposure"], 2),
            "net_exposure": round(aggregate["net_exposure"], 2), "floating_pnl": round(aggregate["floating_pnl"], 2),
            "shock_pnl": round(aggregate["shock_pnl"], 2),
            "limit_breach": aggregate["gross_exposure"] >= symbol_gross_limit,
        })
    symbols.sort(key=lambda row: row["gross_exposure"], reverse=True)
    watchlist.sort(key=lambda row: (row["projected_margin_level"] if row["margin"] > 0 else 999999, -len(row["reasons"])))
    account_rows.sort(key=lambda row: row["equity"], reverse=True)
    recommendations = [{
        "login": row["login"], "name": row["name"], "group": row["group"],
        "priority": "critical" if row["projected_equity"] <= 0 or (row["margin"] > 0 and row["projected_margin_level"] < 100) else "review",
        "actions": ["Review account exposure", "Confirm margin policy", "Consider reducing risk or moving to a watch group"],
        "reasons": row["reasons"],
    } for row in watchlist]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "selected_groups": groups,
        "scenario": {"shock_percent": shock_percent, "margin_level_floor": margin_level_floor, "concentration_limit": concentration_limit, "symbol_gross_limit": symbol_gross_limit},
        "summary": {
            "scoped_accounts": len(accounts), "active_accounts": len(account_positions), "positions": len(positions),
            "equity": round(sum(row["equity"] for row in account_rows), 2),
            "gross_exposure": round(sum(row["gross_exposure"] for row in symbols), 2),
            "net_exposure": round(sum(row["net_exposure"] for row in symbols), 2),
            "floating_pnl": round(sum(row["floating_pnl"] for row in symbols), 2),
            "shock_pnl": round(sum(row["shock_pnl"] for row in symbols), 2),
            "projected_equity": round(sum(row["projected_equity"] for row in account_rows), 2),
            "account_breaches": len(watchlist), "symbol_breaches": sum(row["limit_breach"] for row in symbols),
        },
        "symbols": symbols[:200], "watchlist": watchlist[:1000], "accounts": account_rows[:2000],
        "group_policies": policies, "recommendations": recommendations[:500],
        "warnings": _dedupe_warnings(warnings),
        "data_sources": {"accounts": "UserAccountRequestByLogins", "positions": "PositionRequestByGroup", "symbols": "SymbolGet", "groups": "GroupGet"},
    }


def build_client_live_snapshot(api_handle, login: int, user: Any = None, *, cached_positions=None, cached_account=None) -> dict[str, Any]:
    positions, positions_error = (cached_positions, None) if cached_positions is not None else request_positions_by_logins(api_handle, [login])
    account, account_error = (cached_account, None) if cached_account is not None else request_account(api_handle, login)
    warnings = [message for message in (positions_error, account_error) if message]
    symbol_method = getattr(api_handle, "SymbolGet", None)
    symbol_configs: dict[str, Any] = {}
    normalized_positions: list[dict[str, Any]] = []
    symbols: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "symbol": "", "buy_lots": 0.0, "sell_lots": 0.0, "gross_lots": 0.0,
        "net_lots": 0.0, "long_exposure": 0.0, "short_exposure": 0.0,
        "gross_exposure": 0.0, "net_exposure": 0.0, "floating_pnl": 0.0, "positions": 0,
    })
    for position in positions:
        if _login_from_record(position) != int(login):
            continue
        symbol = str(_value(position, "Symbol", "symbol", default="Unknown") or "Unknown")
        side = _position_action(position)
        lots = max(_position_volume_lots(position), 0.0)
        price = _to_float(_value(position, "PriceCurrent", "price_current", "PriceOpen", "price_open"), 0.0)
        if symbol not in symbol_configs:
            config = None
            if symbol_method is not None:
                try:
                    candidate = symbol_method(symbol)
                    config = None if isinstance(candidate, bool) else candidate
                except Exception:
                    config = None
            symbol_configs[symbol] = config
        contract_size = _to_float(
            _value(position, "ContractSize", "contract_size"),
            _to_float(_value(symbol_configs[symbol], "ContractSize", "contract_size", "TradeContractSize"), 1.0),
        )
        if contract_size <= 0:
            contract_size = 1.0
        exposure = abs(lots * contract_size * price)
        floating = _to_float(_value(position, "Profit", "profit"), 0.0) + _to_float(_value(position, "Storage", "storage", "Swap", "swap"), 0.0)
        row = {
            "position_id": _to_int(_value(position, "Position", "position", "Ticket", "ticket"), 0) or 0,
            "symbol": symbol, "side": side, "lots": round(lots, 4),
            "price_open": round(_to_float(_value(position, "PriceOpen", "price_open"), 0.0), 8),
            "price_current": round(price, 8), "contract_size": contract_size,
            "exposure": round(exposure, 2), "floating_pnl": round(floating, 2),
            "opened_at": datetime.fromtimestamp(_to_int(_value(position, "TimeCreate", "time_create", "Time", "time"), 0) or 0, tz=timezone.utc).isoformat() if (_to_int(_value(position, "TimeCreate", "time_create", "Time", "time"), 0) or 0) else "",
        }
        normalized_positions.append(row)
        aggregate = symbols[symbol]
        aggregate["symbol"] = symbol
        aggregate["buy_lots" if side == "BUY" else "sell_lots"] += lots
        aggregate["long_exposure" if side == "BUY" else "short_exposure"] += exposure
        aggregate["gross_lots"] += lots
        aggregate["gross_exposure"] += exposure
        aggregate["net_exposure"] += exposure if side == "BUY" else -exposure
        aggregate["floating_pnl"] += floating
        aggregate["positions"] += 1

    symbol_rows = []
    for aggregate in symbols.values():
        aggregate["net_lots"] = aggregate["buy_lots"] - aggregate["sell_lots"]
        symbol_rows.append({key: round(value, 2) if isinstance(value, float) else value for key, value in aggregate.items()})
    symbol_rows.sort(key=lambda row: row["gross_exposure"], reverse=True)
    normalized_positions.sort(key=lambda row: row["floating_pnl"])
    balance = _to_float(_value(account, "Balance", "balance"), 0.0)
    credit = _to_float(_value(account, "Credit", "credit"), 0.0)
    equity = _to_float(_value(account, "Equity", "equity"), 0.0)
    margin = _to_float(_value(account, "Margin", "margin"), 0.0)
    margin_free = _to_float(_value(account, "MarginFree", "FreeMargin", "margin_free"), 0.0)
    margin_level = _to_float(_value(account, "MarginLevel", "margin_level"), 0.0)
    gross_exposure = sum(row["gross_exposure"] for row in symbol_rows)
    concentration = max((row["gross_exposure"] for row in symbol_rows), default=0.0) / max(gross_exposure, 1e-12) * 100.0
    hedge_symbols = [row["symbol"] for row in symbol_rows if row["buy_lots"] > 0 and row["sell_lots"] > 0]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client": {
            "login": login,
            "name": str(_value(user, "Name", "name", default="") or ""),
            "group": str(_value(user, "Group", "group", default="") or ""),
            "last_login_ip": _ip_text(_value(user, "LastIP", "last_ip", "IP", "ip")),
        },
        "summary": {
            "balance": round(balance, 2), "credit": round(credit, 2), "equity": round(equity, 2),
            "margin": round(margin, 2), "margin_free": round(margin_free, 2), "margin_level": round(margin_level, 2),
            "floating_pnl": round(sum(row["floating_pnl"] for row in normalized_positions), 2),
            "open_positions": len(normalized_positions),
            "long_positions": sum(row["side"] == "BUY" for row in normalized_positions),
            "short_positions": sum(row["side"] == "SELL" for row in normalized_positions),
            "long_lots": round(sum(row["buy_lots"] for row in symbol_rows), 2),
            "short_lots": round(sum(row["sell_lots"] for row in symbol_rows), 2),
            "net_lots": round(sum(row["net_lots"] for row in symbol_rows), 2),
            "gross_lots": round(sum(row["gross_lots"] for row in symbol_rows), 2),
            "long_exposure": round(sum(row["long_exposure"] for row in symbol_rows), 2),
            "short_exposure": round(sum(row["short_exposure"] for row in symbol_rows), 2),
            "gross_exposure": round(gross_exposure, 2),
            "net_exposure": round(sum(row["net_exposure"] for row in symbol_rows), 2),
            "concentration_pct": round(concentration, 1), "hedge_symbols": hedge_symbols,
        },
        "symbols": symbol_rows, "positions": normalized_positions[:1000], "warnings": _dedupe_warnings(warnings),
    }


def _split_daily_rows_by_window(
    daily_rows: list[dict[str, Any]],
    opening_equity_date: date,
    closing_equity_date: date,
    summary_start: date | None,
    summary_end: date | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    opening_rows: list[dict[str, Any]] = []
    closing_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for row in daily_rows:
        row_date = _row_date(_value(row, "Datetime", "datetime", "Time", "time"))
        if row_date is None:
            continue
        if row_date == opening_equity_date:
            opening_rows.append(row)
        if row_date == closing_equity_date:
            closing_rows.append(row)
        if summary_start is not None and summary_end is not None and summary_start <= row_date <= summary_end:
            summary_rows.append(row)

    return opening_rows, closing_rows, summary_rows


def _group_path_parts(group: str) -> list[str]:
    return [part.strip() for part in re.split(r"[\\/]+", str(group or "")) if part.strip()]


def _group_matches_selection(group: str, selection: str) -> bool:
    group_parts = _group_path_parts(group)
    selection_parts = _group_path_parts(selection)
    if not group_parts or not selection_parts:
        return False
    if len(selection_parts) > len(group_parts):
        return False
    return group_parts[: len(selection_parts)] == selection_parts


def _group_pattern_matches(group: str, pattern: str) -> bool:
    pattern = str(pattern or "").strip()
    if not pattern:
        return False
    if any(ch in pattern for ch in "*?[]"):
        return fnmatch.fnmatch(group, pattern)
    return _group_matches_selection(group, pattern)


def apply_group_filters(all_groups: list[str], selected_groups: list[str] | None, group_filter_rules: str = "") -> list[str]:
    normalized_all_groups = [str(group).strip() for group in all_groups if str(group).strip()]
    if not normalized_all_groups:
        return []

    selected_patterns = [str(group).strip() for group in (selected_groups or []) if str(group).strip()]
    combined_groups = {
        group
        for group in normalized_all_groups
        if selected_patterns and any(_group_pattern_matches(group, pattern) for pattern in selected_patterns)
    }

    rules = []
    for line in str(group_filter_rules or "").splitlines():
        for part in line.split(","):
            rule = part.strip()
            if rule:
                rules.append(rule)

    if rules:
        for rule in rules:
            is_exclude = rule.startswith("!")
            pattern = rule[1:].strip() if is_exclude else rule
            if not pattern:
                continue
            matched = {group for group in normalized_all_groups if _group_pattern_matches(group, pattern)}
            if is_exclude:
                combined_groups.difference_update(matched)
            else:
                combined_groups.update(matched)

    return [group for group in normalized_all_groups if group in combined_groups]


def build_report(
    api,
    groups: list[str],
    report_date: date,
    allowed_logins: set[int] | None = None,
    excluded_logins: set[int] | None = None,
):
    from_ts, to_ts = _date_bounds_utc(report_date)

    users_all = []
    deals_all = []
    daily_all = []
    warnings = []
    allowed_set = None if allowed_logins is None else _normalize_login_set(allowed_logins)

    if allowed_logins is not None and not allowed_set:
        if excluded_logins:
            return pd.DataFrame(columns=REPORT_COLUMNS), ["No accounts remained after applying exclusions."]
        return pd.DataFrame(columns=REPORT_COLUMNS), ["No accounts available for the selected groups."]

    for group in groups:
        users, err = request_users_by_group(api, group)
        if err:
            warnings.append(f"{group}: {err}")
        else:
            users_all.extend(users)

        deals, err = request_deals_by_group(api, group, from_ts, to_ts)
        if err:
            warnings.append(f"{group}: {err}")
        else:
            deals_all.extend(deals)

        daily_rows, err = request_daily_by_group(api, group, from_ts, to_ts)
        if err:
            warnings.append(f"{group}: {err}")
        else:
            daily_all.extend(daily_rows)

    users_by_login = {}
    for user in users_all:
        login = _to_int(_value(user, "Login", "login"), None)
        if login is None:
            continue
        if allowed_set is not None and login not in allowed_set:
            continue
        if login not in users_by_login:
            users_by_login[login] = user

    daily_by_login = {}
    for daily_row in daily_all:
        login = _to_int(_value(daily_row, "Login", "login"), None)
        if login is None:
            continue
        if allowed_set is not None and login not in allowed_set:
            continue
        dt = _to_int(_value(daily_row, "Datetime", "datetime", "Time", "time"), 0) or 0
        prev = daily_by_login.get(login)
        if prev is None or dt >= prev[0]:
            daily_by_login[login] = (dt, daily_row)
    daily_by_login = {key: value[1] for key, value in daily_by_login.items()}

    deals_by_login = defaultdict(lambda: {"deposit": 0.0, "closed_pl": 0.0})
    for deal in deals_all:
        login = _to_int(_value(deal, "Login", "login"), None)
        if login is None:
            continue
        if allowed_set is not None and login not in allowed_set:
            continue

        action = _to_int(_value(deal, "Action", "action"), -999999)
        entry = _to_int(_value(deal, "Entry", "entry"), -999999)
        profit = _to_float(_value(deal, "Profit", "profit"), 0.0)
        volume_units = _deal_volume_units(deal)
        trade_like = _is_trade_deal(deal, action, volume_units)

        if action == DEAL_BALANCE:
            deals_by_login[login]["deposit"] += profit

        if trade_like and (entry in CLOSED_ENTRIES):
            deals_by_login[login]["closed_pl"] += profit

    # Match the Streamlit logic: the report should stay scoped to accounts that
    # actually appear in the daily snapshot or deal stream for the selected day.
    all_logins = sorted(set(daily_by_login) | set(deals_by_login))
    if not all_logins:
        return pd.DataFrame(columns=REPORT_COLUMNS), warnings

    account_cache: dict[int, Any] = {}
    account_errors: set[int] = set()

    def get_account_cached(login: int):
        if login in account_cache:
            return account_cache[login]
        account, err = request_account(api, login)
        if err and login not in account_errors:
            warnings.append(f"Login {login}: {err}")
            account_errors.add(login)
        account_cache[login] = account
        return account

    rows = []
    for login in all_logins:
        user = users_by_login.get(login)
        daily = daily_by_login.get(login)
        deal_stats = deals_by_login.get(login, {"deposit": 0.0, "closed_pl": 0.0})

        name = _value(user, "Name", "name")
        if not name:
            name = _value(daily, "Name", "name", default="") or ""

        deposit = _to_float(deal_stats.get("deposit"), 0.0)
        closed_pl = _to_float(deal_stats.get("closed_pl"), 0.0)

        if login not in deals_by_login and daily is not None:
            deposit = _to_float(_value(daily, "DailyBalance", "daily_balance"), 0.0)
            closed_pl = _to_float(_value(daily, "DailyProfit", "daily_profit"), 0.0)

        account = get_account_cached(login) if daily is None else None

        balance = _pick_float(
            _value(daily, "Balance", "balance"),
            _value(account, "Balance", "balance"),
            default=0.0,
        )
        credit = _pick_float(
            _value(daily, "Credit", "credit"),
            _value(account, "Credit", "credit"),
            default=0.0,
        )
        floating = _pick_float(
            _value(daily, "Floating", "floating"),
            _value(daily, "Profit", "profit"),
            _value(account, "Floating", "floating"),
            _value(account, "Profit", "profit"),
            default=0.0,
        )
        equity = (
            _daily_equity_value(daily)
            if daily is not None
            else _pick_float(_value(account, "Equity", "equity"), default=0.0)
        )
        margin = _pick_float(
            _value(daily, "Margin", "margin"),
            _value(account, "Margin", "margin"),
            default=0.0,
        )
        free_margin = _pick_float(
            _value(daily, "MarginFree", "FreeMargin", "margin_free"),
            _value(account, "MarginFree", "FreeMargin", "margin_free"),
            default=0.0,
        )
        prev_balance = _pick_float(
            _value(daily, "BalancePrevDay", "balance_prev_day"),
            balance - deposit - closed_pl,
            default=0.0,
        )

        rows.append(
            {
                "Login": int(login),
                "Name": str(name),
                "Prev Balance": round(prev_balance, 2),
                "Deposit": round(deposit, 2),
                "Closed P/L": round(closed_pl, 2),
                "Balance": round(balance, 2),
                "Credit": round(credit, 2),
                "Floating": round(floating, 2),
                "Equity": round(equity, 2),
                "Margin": round(margin, 2),
                "Free Margin": round(free_margin, 2),
            }
        )

    df = pd.DataFrame(rows, columns=REPORT_COLUMNS)
    return df.sort_values(by=["Login"], kind="stable").reset_index(drop=True), warnings


def build_summary(api, groups: list[str], date_from: date, date_to: date):
    warnings = []
    if api is None:
        return pd.DataFrame(columns=SUMMARY_COLUMNS), ["Not connected."]
    if not groups:
        return pd.DataFrame(columns=SUMMARY_COLUMNS), ["No groups selected."]

    start_date, end_date, from_ts, to_ts = _date_range_utc(date_from, date_to)
    if start_date != date_from or end_date != date_to:
        warnings.append("Date range was reversed; using Date From <= Date To.")

    users_by_login = {}
    deals_all = []

    for group in groups:
        users, err = request_users_by_group(api, group)
        if err:
            warnings.append(f"{group}: {err}")
        else:
            for user in users:
                login = _to_int(_value(user, "Login", "login"), None)
                if login is None or login <= 0:
                    continue
                if login not in users_by_login:
                    users_by_login[login] = str(_value(user, "Name", "name", default="") or "")

        deals, err = request_deals_by_group(api, group, from_ts, to_ts)
        if err:
            warnings.append(f"{group}: {err}")
        else:
            deals_all.extend(deals)

    if not deals_all:
        return pd.DataFrame(columns=SUMMARY_COLUMNS), warnings

    stats_by_login = defaultdict(
        lambda: {
            "deposit": 0.0,
            "withdraw": 0.0,
            "credit": 0.0,
            "additional": 0.0,
            "volume": 0.0,
            "commission": 0.0,
            "fee": 0.0,
            "swap": 0.0,
            "profit": 0.0,
        }
    )

    skipped_invalid_login = 0
    invalid_login_samples = []

    for deal in deals_all:
        raw_login = _value(deal, "Login", "login")
        login = _to_int(raw_login, None)
        if login is None or login <= 0:
            skipped_invalid_login += 1
            if len(invalid_login_samples) < 3:
                sample_deal = _value(deal, "Deal", "deal", "Ticket", "ticket", "Order", "order", default="?")
                sample_action = _to_int(_value(deal, "Action", "action"), None)
                invalid_login_samples.append(f"deal={sample_deal} login={raw_login!r} action={sample_action}")
            continue

        action = _to_int(_value(deal, "Action", "action"), None)
        if action is None:
            continue

        profit = _to_float(_value(deal, "Profit", "profit"), 0.0)
        volume_units = _deal_volume_units(deal)
        commission = _to_float(_value(deal, "Commission", "commission"), 0.0)
        fee = _to_float(_value(deal, "Fee", "fee"), 0.0)
        swap = _to_float(_value(deal, "Storage", "storage", "Swap", "swap"), 0.0)

        stats = stats_by_login[login]

        if action == DEAL_BALANCE:
            if profit > 0.0:
                stats["deposit"] += profit
            elif profit < 0.0:
                stats["withdraw"] += abs(profit)
        elif action == DEAL_CREDIT:
            stats["credit"] += profit
        elif action in ADDITIONAL_ACTIONS:
            stats["additional"] += profit
        elif action not in TRADING_ACTIONS and volume_units == 0.0 and profit != 0.0:
            stats["additional"] += profit

        if action in TRADING_ACTIONS:
            stats["volume"] += volume_units / 10000.0
            stats["profit"] += profit

        stats["commission"] += commission
        stats["fee"] += fee
        stats["swap"] += swap

    if skipped_invalid_login > 0:
        if stats_by_login:
            warnings.append(f"Ignored {skipped_invalid_login} server/internal deal(s) with invalid login (<=0).")
        else:
            sample_text = f" Sample(s): {'; '.join(invalid_login_samples)}." if invalid_login_samples else ""
            warnings.append(
                "No account-linked deals found for selected range. "
                f"Ignored {skipped_invalid_login} server/internal deal(s) with invalid login (<=0).{sample_text}"
            )

    if not stats_by_login:
        return pd.DataFrame(columns=SUMMARY_COLUMNS), warnings

    rows = []
    for login in sorted(stats_by_login):
        stats = stats_by_login[login]
        deposit = stats["deposit"]
        withdraw = stats["withdraw"]
        rows.append(
            {
                "Login": int(login),
                "Name": users_by_login.get(login, ""),
                "Deposit": round(deposit, 2),
                "Withdraw": round(withdraw, 2),
                "In/Out": round(deposit - withdraw, 2),
                "Credit": round(stats["credit"], 2),
                "Additional": round(stats["additional"], 2),
                "Volume": round(stats["volume"], 2),
                "Commission": round(stats["commission"], 2),
                "Fee": round(stats["fee"], 2),
                "Swap": round(stats["swap"], 2),
                "Profit": round(stats["profit"], 2),
            }
        )

    df = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    return df.sort_values(by=["Login"], kind="stable").reset_index(drop=True), warnings


def append_total_row(summary_df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(summary_df, pd.DataFrame) or summary_df.empty:
        return summary_df

    numeric_cols = [
        "Deposit",
        "Withdraw",
        "Credit",
        "Additional",
        "Volume",
        "Commission",
        "Fee",
        "Swap",
        "Profit",
        "Total Trades",
        "Winning Trades",
        "Losing Trades",
        "Breakeven Trades",
    ]
    totals = {}
    for column in numeric_cols:
        if column in summary_df.columns:
            totals[column] = pd.to_numeric(summary_df[column], errors="coerce").fillna(0.0).sum()
        else:
            totals[column] = 0.0

    total_trades = int(round(totals["Total Trades"]))
    if total_trades <= 0:
        total_trades = int(round(totals["Winning Trades"] + totals["Losing Trades"] + totals["Breakeven Trades"]))
    winning_trades = int(round(totals["Winning Trades"]))
    losing_trades = int(round(totals["Losing Trades"]))
    breakeven_trades = int(round(totals["Breakeven Trades"]))
    total_row = {
        "Login": "Total",
        "Name": "",
        "Deposit": round(totals["Deposit"], 2),
        "Withdraw": round(totals["Withdraw"], 2),
        "In/Out": round(totals["Deposit"] - totals["Withdraw"], 2),
        "Credit": round(totals["Credit"], 2),
        "Additional": round(totals["Additional"], 2),
        "Volume": round(totals["Volume"], 2),
        "Commission": round(totals["Commission"], 2),
        "Fee": round(totals["Fee"], 2),
        "Swap": round(totals["Swap"], 2),
        "Profit": round(totals["Profit"], 2),
        "Total Trades": total_trades,
        "Winning Trades": winning_trades,
        "Losing Trades": losing_trades,
        "Breakeven Trades": breakeven_trades,
        "Win %": round((winning_trades / total_trades) * 100.0, 1) if total_trades > 0 else 0.0,
        "Loss %": round((losing_trades / total_trades) * 100.0, 1) if total_trades > 0 else 0.0,
    }
    return pd.concat([summary_df, pd.DataFrame([total_row])], ignore_index=True)


def build_equity_summary_display(eq_report: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(eq_report, pd.DataFrame) or eq_report.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    summary_df = eq_report.copy()
    if "Name" not in summary_df.columns:
        summary_df["Name"] = ""

    for column in [
        "Deposit",
        "Withdrawal",
        "Net Credit",
        "Bonus",
        "Closed Lots",
        "Commission",
        "Fee",
        "Swap",
        "NET PNL USD",
    ]:
        if column not in summary_df.columns:
            summary_df[column] = 0.0
        summary_df[column] = pd.to_numeric(summary_df[column], errors="coerce").fillna(0.0)

    summary_df["Withdraw"] = summary_df["Withdrawal"]
    summary_df["Credit"] = summary_df["Net Credit"]
    summary_df["Additional"] = summary_df["Bonus"]
    summary_df["Volume"] = summary_df["Closed Lots"]
    summary_df["Profit"] = summary_df["NET PNL USD"]
    summary_df["In/Out"] = summary_df["Deposit"] - summary_df["Withdraw"]
    summary_df["Login"] = pd.to_numeric(summary_df["Login"], errors="coerce").astype("Int64")

    return summary_df[SUMMARY_COLUMNS].sort_values(by=["Login"], kind="stable").reset_index(drop=True)


def _dedupe_warnings(*warning_lists: list[str]) -> list[str]:
    seen = set()
    merged = []
    for warning_list in warning_lists:
        for warning in warning_list:
            if warning and warning not in seen:
                seen.add(warning)
                merged.append(warning)
    return merged


def _dataframe_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    clean_df = df.replace({np.nan: None, np.inf: None, -np.inf: None})
    return clean_df.to_dict(orient="records")


def _records_to_dataframe(value: Any, columns: list[str] | None = None) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if value is None:
        return pd.DataFrame(columns=columns or [])
    if isinstance(value, list):
        if not value:
            return pd.DataFrame(columns=columns or [])
        return pd.DataFrame(value)
    if isinstance(value, dict):
        if not value:
            return pd.DataFrame(columns=columns or [])
        return pd.DataFrame(value)
    return pd.DataFrame(columns=columns or [])


def _coerce_date_value(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _coerce_summary_range(value: Any) -> tuple[date | None, date | None]:
    if isinstance(value, dict):
        start_value = value.get("from") or value.get("start")
        end_value = value.get("to") or value.get("end")
    elif isinstance(value, (list, tuple)):
        start_value = value[0] if len(value) > 0 else None
        end_value = value[1] if len(value) > 1 else None
    else:
        return None, None
    return _coerce_date_value(start_value), _coerce_date_value(end_value)


@dataclass(frozen=True)
class WorkbookTheme:
    primary_navy: str = "F5F5F5"
    dark_background: str = "FFFFFF"
    border: str = "D1D5DB"
    accent_blue: str = "E5E7EB"
    success_green: str = "E5E7EB"
    danger_red: str = "E5E7EB"
    warning_amber: str = "E5E7EB"
    warning_fill: str = "F5F5F5"
    warning_text: str = "111827"
    soft_gray_text: str = "6B7280"
    primary_text: str = "111827"
    white: str = "FFFFFF"
    surface: str = "FFFFFF"
    surface_alt: str = "F5F5F5"


BROKERAGE_THEME = WorkbookTheme()


@lru_cache(maxsize=None)
def _fill(color: str) -> PatternFill:
    return PatternFill(fill_type="solid", fgColor=color)


@lru_cache(maxsize=None)
def _font(color: str, *, size: int = 11, bold: bool = False, italic: bool = False) -> Font:
    return Font(color=color, size=size, bold=bold, italic=italic)


@lru_cache(maxsize=None)
def _alignment(horizontal: str = "left", vertical: str = "center", wrap_text: bool = False) -> Alignment:
    return Alignment(horizontal=horizontal, vertical=vertical, wrap_text=wrap_text)


@lru_cache(maxsize=None)
def _body_border(color: str = BROKERAGE_THEME.border) -> Border:
    return Border(bottom=Side(style="thin", color=color))


@lru_cache(maxsize=None)
def _card_border(color: str = BROKERAGE_THEME.border) -> Border:
    side = Side(style="thin", color=color)
    return Border(left=side, right=side, top=side, bottom=side)


def _style_cell(
    cell,
    *,
    fill: PatternFill | None = None,
    font: Font | None = None,
    alignment: Alignment | None = None,
    border: Border | None = None,
    number_format: str | None = None,
):
    if fill is not None:
        cell.fill = fill
    if font is not None:
        cell.font = font
    if alignment is not None:
        cell.alignment = alignment
    if border is not None:
        cell.border = border
    if number_format is not None:
        cell.number_format = number_format


def _is_total_row(values: tuple[Any, ...]) -> bool:
    if not values:
        return False
    first = values[0]
    return isinstance(first, str) and first.strip().lower() == "total"


def _sanitize_equity_value(value: Any) -> float:
    numeric = _to_float(value, 0.0)
    if not math.isfinite(numeric):
        return 0.0
    return max(numeric, 0.0)


def _format_money_text(value: Any) -> str:
    numeric = _to_float(value, 0.0)
    if not math.isfinite(numeric):
        numeric = 0.0
    return f"{'-' if numeric < 0 else ''}${abs(numeric):,.2f}"


def _sanitize_equity_dataframe(df: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df.copy() if isinstance(df, pd.DataFrame) else df

    normalized = df.copy()
    for column in columns:
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce").fillna(0.0).clip(lower=0.0)
    return normalized


def _configure_sheet_layout(
    ws,
    *,
    zoom_scale: int = 95,
    landscape: bool = True,
    repeat_header_row: int | None = None,
    fit_to_width: int = 1,
    print_area: str | None = None,
):
    ws.sheet_view.showGridLines = True
    ws.sheet_view.zoomScale = zoom_scale
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.fitToWidth = fit_to_width
    ws.page_setup.fitToHeight = 0
    ws.page_margins.left = 0.35
    ws.page_margins.right = 0.35
    ws.page_margins.top = 0.45
    ws.page_margins.bottom = 0.45
    ws.page_margins.header = 0.15
    ws.page_margins.footer = 0.15
    ws.print_options.gridLines = True
    ws.print_options.horizontalCentered = False
    ws.print_options.verticalCentered = False
    if repeat_header_row is not None:
        ws.print_title_rows = f"{repeat_header_row}:{repeat_header_row}"
    if print_area:
        ws.print_area = print_area


def _write_title_block(
    ws,
    *,
    title: str,
    subtitle: str,
    metadata: list[tuple[str, str]],
    start_row: int = 1,
    start_col: int = 1,
    end_col: int | None = None,
    theme: WorkbookTheme = BROKERAGE_THEME,
) -> int:
    end_col = end_col or max(start_col + 7, ws.max_column or start_col + 7)
    start_letter = get_column_letter(start_col)
    end_letter = get_column_letter(end_col)

    ws.merge_cells(f"{start_letter}{start_row}:{end_letter}{start_row}")
    title_cell = ws.cell(start_row, start_col, title)
    _style_cell(
        title_cell,
        fill=_fill(theme.surface),
        font=_font(theme.primary_text, size=16, bold=True),
        alignment=_alignment("left"),
        border=_card_border(theme.border),
    )
    ws.row_dimensions[start_row].height = 24

    subtitle_row = start_row + 1
    ws.merge_cells(f"{start_letter}{subtitle_row}:{end_letter}{subtitle_row}")
    subtitle_cell = ws.cell(subtitle_row, start_col, subtitle)
    _style_cell(
        subtitle_cell,
        fill=_fill(theme.surface),
        font=_font(theme.soft_gray_text, size=10),
        alignment=_alignment("left"),
        border=_card_border(theme.border),
    )
    ws.row_dimensions[subtitle_row].height = 20

    spacer_row = start_row + 2
    ws.row_dimensions[spacer_row].height = 6

    if not metadata:
        return spacer_row

    usable_width = max(end_col - start_col + 1, 1)
    card_count = min(len(metadata), max(1, usable_width // 2))
    card_count = max(1, card_count)
    metadata = metadata[:card_count]
    base_span = usable_width // card_count
    remainder = usable_width % card_count
    label_row = start_row + 3
    value_row = start_row + 4
    for row in (label_row, value_row):
        ws.row_dimensions[row].height = 18

    cursor = start_col
    for index, (label, value) in enumerate(metadata):
        span = base_span + (1 if index < remainder else 0)
        span = max(span, 2 if usable_width >= card_count * 2 else 1)
        card_end = min(end_col, cursor + span - 1)
        ws.merge_cells(f"{get_column_letter(cursor)}{label_row}:{get_column_letter(card_end)}{label_row}")
        ws.merge_cells(f"{get_column_letter(cursor)}{value_row}:{get_column_letter(card_end)}{value_row}")
        label_cell = ws.cell(label_row, cursor, label.upper())
        value_cell = ws.cell(value_row, cursor, value)
        _style_cell(
            label_cell,
            fill=_fill(theme.surface_alt),
            font=_font(theme.primary_text, size=9, bold=True),
            alignment=_alignment("left"),
            border=_card_border(theme.border),
        )
        _style_cell(
            value_cell,
            fill=_fill(theme.surface),
            font=_font(theme.primary_text, size=11, bold=True),
            alignment=_alignment("left"),
            border=_card_border(theme.border),
        )
        cursor = card_end + 1

    return value_row


def _write_section_header(
    ws,
    *,
    row: int,
    start_col: int,
    end_col: int,
    title: str,
    subtitle: str | None = None,
    theme: WorkbookTheme = BROKERAGE_THEME,
) -> int:
    start_letter = get_column_letter(start_col)
    end_letter = get_column_letter(end_col)
    ws.merge_cells(f"{start_letter}{row}:{end_letter}{row}")
    header_cell = ws.cell(row, start_col, title)
    _style_cell(
        header_cell,
        fill=_fill(theme.surface_alt),
        font=_font(theme.primary_text, size=12, bold=True),
        alignment=_alignment("left"),
        border=_card_border(theme.border),
    )
    ws.row_dimensions[row].height = 22

    if subtitle:
        subtitle_row = row + 1
        ws.merge_cells(f"{start_letter}{subtitle_row}:{end_letter}{subtitle_row}")
        subtitle_cell = ws.cell(subtitle_row, start_col, subtitle)
        _style_cell(
            subtitle_cell,
            fill=_fill(theme.surface),
            font=_font(theme.soft_gray_text, size=10),
            alignment=_alignment("left"),
            border=_card_border(theme.border),
        )
        ws.row_dimensions[subtitle_row].height = 18
        return subtitle_row + 1

    return row + 1


def _write_metric_cards(
    ws,
    *,
    cards: list[dict[str, Any]],
    start_row: int,
    start_col: int = 1,
    card_span: int = 2,
    theme: WorkbookTheme = BROKERAGE_THEME,
) -> int:
    tone_fill = {
        "neutral": theme.surface_alt,
        "accent": theme.surface_alt,
        "positive": theme.surface_alt,
        "negative": theme.surface_alt,
    }
    tone_font = {
        "neutral": theme.primary_text,
        "accent": theme.primary_text,
        "positive": theme.primary_text,
        "negative": theme.primary_text,
    }

    row_label = start_row
    row_value = start_row + 1
    row_caption = start_row + 2
    for row in (row_label, row_value, row_caption):
        ws.row_dimensions[row].height = 22 if row == row_value else 18

    for index, card in enumerate(cards):
        card_col_start = start_col + (index * card_span)
        card_col_end = card_col_start + card_span - 1
        title = str(card.get("label") or "").upper()
        value = card.get("value", "")
        caption = card.get("caption", "")
        tone = str(card.get("tone") or "neutral")
        fill_color = tone_fill.get(tone, theme.primary_navy)
        font_color = tone_font.get(tone, theme.white)

        ws.merge_cells(f"{get_column_letter(card_col_start)}{row_label}:{get_column_letter(card_col_end)}{row_label}")
        ws.merge_cells(f"{get_column_letter(card_col_start)}{row_value}:{get_column_letter(card_col_end)}{row_value}")
        ws.merge_cells(f"{get_column_letter(card_col_start)}{row_caption}:{get_column_letter(card_col_end)}{row_caption}")

        label_cell = ws.cell(row_label, card_col_start, title)
        value_cell = ws.cell(row_value, card_col_start, value)
        caption_cell = ws.cell(row_caption, card_col_start, caption)

        _style_cell(
            label_cell,
            fill=_fill(theme.surface),
            font=_font(theme.primary_text, size=9, bold=True),
            alignment=_alignment("left"),
            border=_card_border(theme.border),
        )
        _style_cell(
            value_cell,
            fill=_fill(fill_color),
            font=_font(font_color, size=16, bold=True),
            alignment=_alignment("left"),
            border=_card_border(theme.border),
        )
        _style_cell(
            caption_cell,
            fill=_fill(theme.surface),
            font=_font(theme.soft_gray_text, size=9),
            alignment=_alignment("left"),
            border=_card_border(theme.border),
        )

    return row_caption + 1


def _apply_positive_negative_rules(
    ws,
    *,
    start_row: int,
    end_row: int,
    start_col: int,
    headers: list[str],
    columns: set[str],
    theme: WorkbookTheme = BROKERAGE_THEME,
):
    return


def _apply_table_widths(
    ws,
    *,
    df: pd.DataFrame,
    headers: list[str],
    start_col: int,
    sample_rows: int = 200,
):
    if df.empty or not headers:
        for offset, header in enumerate(headers, start=start_col):
            ws.column_dimensions[get_column_letter(offset)].width = min(max(len(str(header)) + 2, 12), 28)
        return

    for offset, header in enumerate(headers, start=start_col):
        letter = get_column_letter(offset)
        max_len = len(str(header))
        for value in df[header].head(sample_rows).tolist():
            if pd.isna(value):
                continue
            if isinstance(value, (datetime, date)):
                value_len = 10
            elif isinstance(value, float):
                value_len = len(f"{value:,.2f}")
            else:
                value_len = len(f"{value}")
            max_len = max(max_len, value_len)
        ws.column_dimensions[letter].width = min(max(max_len + 2, 12), 28)


def _collect_total_row_index(rows: list[tuple[Any, ...]]) -> int | None:
    for index, values in enumerate(rows):
        if _is_total_row(values):
            return index
    return None


def build_deals_dataframe(deal_dicts: list[dict[str, Any]]) -> pd.DataFrame:
    if not deal_dicts:
        return pd.DataFrame(columns=DEAL_TABLE_COLUMNS)

    df = pd.DataFrame(deal_dicts)
    for col in ["Volume", "Profit", "Commission", "Fee", "Swap", "PnL"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    if "Login" in df.columns:
        df["Login"] = pd.to_numeric(df["Login"], errors="coerce").astype("Int64")
    return df


def compute_client_report(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "Login",
                "Name",
                "Group",
                "Closed Lots",
                "NET PNL USD",
                "MT5 Trade Profit",
                "Total Trades",
                "Winning Trades",
                "Losing Trades",
                "Breakeven Trades",
                "Win %",
                "Loss %",
                "Commission",
                "Fee",
                "Swap",
            ]
        )

    agg = (
        df.groupby("Login", as_index=False)
        .agg(
            Name=("Name", "first"),
            Group=("Group", "first"),
            Closed_Lots=("Volume", "sum"),
            NET_PNL_USD=("PnL", "sum"),
            MT5_Trade_Profit=("Profit", "sum"),
            Total_Trades=("PnL", "count"),
            Winning_Trades=("PnL", lambda series: int((series > 0).sum())),
            Losing_Trades=("PnL", lambda series: int((series < 0).sum())),
            Breakeven_Trades=("PnL", lambda series: int((series == 0).sum())),
            Commission=("Commission", "sum"),
            Fee=("Fee", "sum"),
            Swap=("Swap", "sum"),
        )
        .rename(
            columns={
                "Closed_Lots": "Closed Lots",
                "NET_PNL_USD": "NET PNL USD",
                "MT5_Trade_Profit": "MT5 Trade Profit",
                "Total_Trades": "Total Trades",
                "Winning_Trades": "Winning Trades",
                "Losing_Trades": "Losing Trades",
                "Breakeven_Trades": "Breakeven Trades",
            }
        )
    )
    total_trades = pd.to_numeric(agg["Total Trades"], errors="coerce").fillna(0)
    agg["Win %"] = np.where(total_trades > 0, (agg["Winning Trades"] / total_trades) * 100.0, 0.0)
    agg["Loss %"] = np.where(total_trades > 0, (agg["Losing Trades"] / total_trades) * 100.0, 0.0)
    agg["Win %"] = pd.to_numeric(agg["Win %"], errors="coerce").fillna(0.0).round(1)
    agg["Loss %"] = pd.to_numeric(agg["Loss %"], errors="coerce").fillna(0.0).round(1)
    agg["Winning Trades"] = pd.to_numeric(agg["Winning Trades"], errors="coerce").fillna(0).astype(int)
    agg["Losing Trades"] = pd.to_numeric(agg["Losing Trades"], errors="coerce").fillna(0).astype(int)
    agg["Breakeven Trades"] = pd.to_numeric(agg["Breakeven Trades"], errors="coerce").fillna(0).astype(int)
    return agg.sort_values("NET PNL USD", ascending=False).reset_index(drop=True)


def compute_group_summary(report: pd.DataFrame) -> pd.DataFrame:
    if report.empty:
        return pd.DataFrame(
            columns=["Group", "Accounts", "Closed Lots", "NET PNL USD", "Total Trades"]
        )

    return (
        report.groupby("Group", as_index=False)
        .agg(
            Accounts=("Login", "nunique"),
            Closed_Lots=("Closed Lots", "sum"),
            NET_PNL_USD=("NET PNL USD", "sum"),
            Total_Trades=("Total Trades", "sum"),
        )
        .rename(
            columns={
                "Closed_Lots": "Closed Lots",
                "NET_PNL_USD": "NET PNL USD",
                "Total_Trades": "Total Trades",
            }
        )
        .sort_values("NET PNL USD", ascending=False)
        .reset_index(drop=True)
    )


def compute_kpis(report: pd.DataFrame) -> dict[str, Any]:
    if report.empty:
        return {
            "total_clients": 0,
            "total_pnl": 0.0,
            "trade_profit": 0.0,
            "total_trades": 0,
            "total_lots": 0.0,
            "total_commission": 0.0,
            "total_fee": 0.0,
            "total_swap": 0.0,
        }

    return {
        "total_clients": int(report["Login"].nunique()),
        "total_pnl": round(float(report["NET PNL USD"].sum()), 2),
        "trade_profit": round(float(report["MT5 Trade Profit"].sum()), 2),
        "total_trades": int(report["Total Trades"].sum()),
        "total_lots": round(float(report["Closed Lots"].sum()), 2),
        "total_commission": round(float(report["Commission"].sum()), 2),
        "total_fee": round(float(report["Fee"].sum()), 2),
        "total_swap": round(float(report["Swap"].sum()), 2),
    }


def compute_equity_report(
    oe_dicts: list[dict[str, Any]],
    ce_dicts: list[dict[str, Any]],
    summary_dicts: list[dict[str, Any]],
    group_map: dict[int, str],
    account_filter: list[int] | None = None,
) -> pd.DataFrame:
    def _to_df(dicts: list[dict[str, Any]]) -> pd.DataFrame:
        if not dicts:
            return pd.DataFrame()
        df = pd.DataFrame(dicts)
        if "Login" in df.columns:
            df["Login"] = pd.to_numeric(df["Login"], errors="coerce").astype("Int64")
        return df

    def _series(frame: pd.DataFrame, *columns: str) -> pd.Series:
        for column in columns:
            if column in frame.columns:
                return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
        return pd.Series(dtype=float)

    def _first_value(frame: pd.DataFrame, login: int, *columns: str, clip_nonnegative: bool = True) -> float:
        if frame.empty:
            return 0.0
        if login not in frame.index:
            return 0.0
        rec = frame.loc[login]
        if isinstance(rec, pd.DataFrame):
            rec = rec.iloc[-1]
        if isinstance(rec, pd.Series):
            for column in columns:
                for candidate in (
                    column,
                    str(column).lower(),
                    str(column).upper(),
                    str(column)[:1].lower() + str(column)[1:],
                ):
                    if candidate in rec.index:
                        value = _pick_float(rec[candidate], default=0.0)
                        return max(0.0, value) if clip_nonnegative else value
        else:
            value = _pick_float(
                *(_value(rec, column, column.lower(), column.upper()) for column in columns),
                default=0.0,
            )
            return max(0.0, value) if clip_nonnegative else value
        return 0.0

    oe_df = _to_df(oe_dicts)
    ce_df = _to_df(ce_dicts)
    sm_df = _to_df(summary_dicts)

    all_logins: set[int] = set()
    for frame in (oe_df, ce_df, sm_df):
        if not frame.empty and "Login" in frame.columns:
            all_logins.update(frame["Login"].dropna().astype(int).tolist())

    if account_filter is not None:
        all_logins = all_logins.intersection(set(account_filter))

    if not all_logins:
        return pd.DataFrame()

    oe_idx = oe_df.set_index("Login") if (not oe_df.empty and "Login" in oe_df.columns) else None
    ce_idx = ce_df.set_index("Login") if (not ce_df.empty and "Login" in ce_df.columns) else None
    sm_grp = sm_df.groupby("Login") if (not sm_df.empty and "Login" in sm_df.columns) else None

    rows: list[dict[str, Any]] = []
    for login in sorted(all_logins):
        oe_raw = _first_value(
            oe_idx,
            login,
            "ProfitEquity",
            "profit_equity",
            "Equity",
            "equity",
            clip_nonnegative=False,
        ) if oe_idx is not None else 0.0
        ce_raw = _first_value(
            ce_idx,
            login,
            "ProfitEquity",
            "profit_equity",
            "Equity",
            "equity",
            clip_nonnegative=False,
        ) if ce_idx is not None else 0.0
        oe_val = oe_raw
        ce_val = ce_raw

        summary_frame = sm_grp.get_group(login) if sm_grp is not None and login in sm_grp.groups else pd.DataFrame()
        deposit = 0.0
        withdrawal = 0.0
        net_dp_wd = 0.0
        net_credit = 0.0
        credit_in = 0.0
        credit_out = 0.0
        bonus = 0.0
        commission = 0.0
        fee = 0.0
        swap = 0.0
        closed_volume = 0.0
        closed_lots = 0.0

        if not summary_frame.empty:
            if any(column in summary_frame.columns for column in ("Deposit", "Withdraw", "In/Out")):
                deposit = float(_series(summary_frame, "Deposit", "deposit").sum())
                withdrawal = float(_series(summary_frame, "Withdraw", "withdraw").sum())
                net_dp_wd = deposit - withdrawal
            else:
                net_dp_wd = float(_series(summary_frame, "DailyBalance", "daily_balance", "Balance", "balance").sum())
                deposit = max(net_dp_wd, 0.0)
                withdrawal = abs(min(net_dp_wd, 0.0))

            net_credit = float(_series(summary_frame, "Credit", "credit", "DailyCredit", "daily_credit").sum())
            credit_in = max(net_credit, 0.0)
            credit_out = min(net_credit, 0.0)

            bonus = float(
                _series(summary_frame, "Additional", "additional", "Bonus", "bonus", "DailyBonus", "daily_bonus").sum()
            )
            commission = float(_series(summary_frame, "Commission", "commission").sum())
            fee = float(_series(summary_frame, "Fee", "fee").sum())
            swap = float(_series(summary_frame, "Swap", "swap", "Storage", "storage").sum())
            closed_volume = float(
                _series(summary_frame, "Volume", "volume", "Closed Volume", "closed_volume", "VolumeClosed", "volume_closed").sum()
            )
            if closed_volume == 0.0 and "Closed Lots" in summary_frame.columns:
                closed_volume = float(_series(summary_frame, "Closed Lots", "closed_lots").sum())
            closed_lots = closed_volume / 2.0 if closed_volume else 0.0

        difference = ce_val - oe_val
        net_pnl = ce_val - oe_val - net_dp_wd - net_credit - bonus
        net_pnl_pct = (net_pnl / oe_val * 100.0) if oe_val > 0 else 0.0

        rows.append(
            {
                "Login": login,
                "Group": group_map.get(login, ""),
                "Opening Equity": round(oe_val, 2),
                "Closing Equity": round(ce_val, 2),
                "Difference": round(difference, 2),
                "Net D/W": round(net_dp_wd, 2),
                "Deposit": round(deposit, 2),
                "Withdrawal": round(withdrawal, 2),
                "Credit In": round(credit_in, 2),
                "Credit Out": round(credit_out, 2),
                "Net Credit": round(net_credit, 2),
                "Bonus": round(bonus, 2),
                "Commission": round(commission, 2),
                "Fee": round(fee, 2),
                "Swap": round(swap, 2),
                "Closed Volume": round(closed_volume, 2),
                "Closed Lots": round(closed_lots, 2),
                "Net P&L": round(net_pnl, 2),
                "Net P&L %": round(net_pnl_pct, 2),
                "LP P&L": round(net_pnl, 2),
            }
        )

    return pd.DataFrame(rows)


def compute_equity_group_summary(eq_report: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(eq_report, pd.DataFrame) or eq_report.empty:
        return pd.DataFrame()

    return (
        eq_report.groupby("Group", as_index=False)
        .agg(
            Accounts=("Login", "nunique"),
            Opening_Equity=("Opening Equity", "sum"),
            Closing_Equity=("Closing Equity", "sum"),
            Net_DW=("Net D/W", "sum"),
            Net_Credit=("Net Credit", "sum"),
            Bonus=("Bonus", "sum"),
            Net_PnL=("Net P&L", "sum"),
        )
        .sort_values("Net_PnL", ascending=False)
        .reset_index(drop=True)
    )


def compute_equity_kpis(eq_report: pd.DataFrame) -> dict[str, Any]:
    if not isinstance(eq_report, pd.DataFrame) or eq_report.empty:
        return {
            "total_accounts": 0,
            "total_oe": 0.0,
            "total_ce": 0.0,
            "total_net_dw": 0.0,
            "total_net_credit": 0.0,
            "total_bonus": 0.0,
            "total_net_pnl": 0.0,
            "profitable_accounts": 0,
            "losing_accounts": 0,
        }

    return {
        "total_accounts": int(eq_report["Login"].nunique()),
        "total_oe": round(float(eq_report["Opening Equity"].sum()), 2),
        "total_ce": round(float(eq_report["Closing Equity"].sum()), 2),
        "total_net_dw": round(float(eq_report["Net D/W"].sum()), 2),
        "total_net_credit": round(float(eq_report["Net Credit"].sum()), 2),
        "total_bonus": round(float(eq_report["Bonus"].sum()), 2),
        "total_net_pnl": round(float(eq_report["Net P&L"].sum()), 2),
        "profitable_accounts": int((eq_report["Net P&L"] > 0).sum()),
        "losing_accounts": int((eq_report["Net P&L"] < 0).sum()),
    }


def compute_account_category_summary(eq_report: pd.DataFrame, new_reg_count: int = 0) -> pd.DataFrame:
    if not isinstance(eq_report, pd.DataFrame) or eq_report.empty:
        return pd.DataFrame(columns=["Category", "Number of Accounts"])

    ce = eq_report["Closing Equity"]
    ndw = eq_report["Net D/W"].abs()
    ncr = eq_report["Net Credit"].abs()
    bon = eq_report["Bonus"].abs()

    zero_eq = int((ce == 0).sum())
    low_eq = int(((ce > 0) & (ce <= 100)).sum())
    high_eq = int((ce > 100).sum())
    total = len(eq_report)
    active = int(((ce > 0) | (ndw > 0) | (ncr > 0) | (bon > 0)).sum())
    inactive = total - active

    rows = [
        ("0 Equity", zero_eq),
        ("0 - 100 Equity", low_eq),
        ("100 Above Equity", high_eq),
        ("Total", total),
        ("Active Accounts", active),
        ("Inactive Accounts", inactive),
        ("New Registered Ac", new_reg_count),
    ]
    return pd.DataFrame(rows, columns=["Category", "Number of Accounts"])


def summarize_deals_records(deals_all: list[dict[str, Any]], users_by_login: dict[int, str]) -> tuple[pd.DataFrame, list[str]]:
    if not deals_all:
        return pd.DataFrame(
            columns=[
                *SUMMARY_COLUMNS,
                "Total Trades",
                "Winning Trades",
                "Losing Trades",
                "Breakeven Trades",
                "Win %",
                "Loss %",
            ]
        ), []

    stats_by_login = defaultdict(
        lambda: {
            "deposit": 0.0,
            "withdraw": 0.0,
            "credit": 0.0,
            "additional": 0.0,
            "volume": 0.0,
            "commission": 0.0,
            "fee": 0.0,
            "swap": 0.0,
            "profit": 0.0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "breakeven_trades": 0,
        }
    )

    warnings: list[str] = []
    skipped_invalid_login = 0
    invalid_login_samples: list[str] = []

    for deal in deals_all:
        raw_login = _value(deal, "Login", "login")
        login = _to_int(raw_login, None)
        if login is None or login <= 0:
            skipped_invalid_login += 1
            if len(invalid_login_samples) < 3:
                sample_deal = _value(deal, "Deal", "deal", "Ticket", "ticket", "Order", "order", default="?")
                sample_action = _to_int(_value(deal, "Action", "action"), None)
                invalid_login_samples.append(f"deal={sample_deal} login={raw_login!r} action={sample_action}")
            continue

        action = _to_int(_value(deal, "Action", "action"), None)
        profit = _to_float(_value(deal, "Profit", "profit"), 0.0)
        volume_units = _deal_volume_units(deal)
        commission = _to_float(_value(deal, "Commission", "commission"), 0.0)
        fee = _to_float(_value(deal, "Fee", "fee"), 0.0)
        swap = _to_float(_value(deal, "Storage", "storage", "Swap", "swap"), 0.0)
        trade_like = _is_trade_deal(deal, action, volume_units)

        stats = stats_by_login[login]

        if action == DEAL_BALANCE:
            if profit > 0.0:
                stats["deposit"] += profit
            elif profit < 0.0:
                stats["withdraw"] += abs(profit)
        elif action == DEAL_CREDIT:
            stats["credit"] += profit
        elif action in ADDITIONAL_ACTIONS:
            stats["additional"] += profit
        elif not trade_like and action not in TRADING_ACTIONS and volume_units == 0.0 and profit != 0.0:
            stats["additional"] += profit

        if trade_like:
            stats["volume"] += volume_units / 10000.0
            stats["profit"] += profit

        if trade_like:
            stats["total_trades"] += 1
            if profit > 0.0:
                stats["winning_trades"] += 1
            elif profit < 0.0:
                stats["losing_trades"] += 1
            else:
                stats["breakeven_trades"] += 1

        stats["commission"] += commission
        stats["fee"] += fee
        stats["swap"] += swap

    if skipped_invalid_login > 0:
        if stats_by_login:
            warnings.append(f"Ignored {skipped_invalid_login} server/internal deal(s) with invalid login (<=0).")
        else:
            sample_text = f" Sample(s): {'; '.join(invalid_login_samples)}." if invalid_login_samples else ""
            warnings.append(
                "No account-linked deals found for selected range. "
                f"Ignored {skipped_invalid_login} server/internal deal(s) with invalid login (<=0).{sample_text}"
            )

    if not stats_by_login:
        return pd.DataFrame(columns=SUMMARY_COLUMNS), warnings

    rows = []
    for login in sorted(stats_by_login):
        stats = stats_by_login[login]
        deposit = stats["deposit"]
        withdraw = stats["withdraw"]
        total_trades = int(stats["total_trades"])
        winning_trades = int(stats["winning_trades"])
        losing_trades = int(stats["losing_trades"])
        breakeven_trades = int(stats["breakeven_trades"])
        rows.append(
            {
                "Login": int(login),
                "Name": users_by_login.get(login, ""),
                "Deposit": round(deposit, 2),
                "Withdraw": round(withdraw, 2),
                "In/Out": round(deposit - withdraw, 2),
                "Credit": round(stats["credit"], 2),
                "Additional": round(stats["additional"], 2),
                "Volume": round(stats["volume"], 2),
                "Commission": round(stats["commission"], 2),
                "Fee": round(stats["fee"], 2),
                "Swap": round(stats["swap"], 2),
                "Profit": round(stats["profit"], 2),
                "Total Trades": total_trades,
                "Winning Trades": winning_trades,
                "Losing Trades": losing_trades,
                "Breakeven Trades": breakeven_trades,
                "Win %": round((winning_trades / total_trades) * 100.0, 1) if total_trades > 0 else 0.0,
                "Loss %": round((losing_trades / total_trades) * 100.0, 1) if total_trades > 0 else 0.0,
            }
        )

    df = pd.DataFrame(
        rows,
        columns=[
            *SUMMARY_COLUMNS,
            "Total Trades",
            "Winning Trades",
            "Losing Trades",
            "Breakeven Trades",
            "Win %",
            "Loss %",
        ],
    )
    return df.sort_values(by=["Login"], kind="stable").reset_index(drop=True), warnings


def build_deal_report(
    api,
    groups: list[str],
    from_date: date,
    to_date: date,
    excluded_logins: set[int] | None = None,
):
    warnings = []
    start_date, end_date, from_ts, to_ts = _date_range_utc(from_date, to_date)
    if start_date != from_date or end_date != to_date:
        warnings.append("Date range was reversed; using Date From <= Date To.")

    users_by_login = {}
    deal_rows = []
    excluded_set = _normalize_login_set(excluded_logins)

    for group in groups:
        users, err = request_users_by_group(api, group)
        if err:
            warnings.append(f"{group}: {err}")
        else:
            for user in users:
                login = _to_int(_value(user, "Login", "login"), None)
                if login is None:
                    continue
                if login in excluded_set:
                    continue
                users_by_login[login] = {
                    "name": str(_value(user, "Name", "name", default="") or ""),
                    "group": str(_value(user, "Group", "group", default=group) or group),
                }

        deals, err = request_deals_by_group(api, group, from_ts, to_ts)
        if err:
            warnings.append(f"{group}: {err}")
            continue

        for deal in deals:
            login = _to_int(_value(deal, "Login", "login"), None)
            if login is None or login <= 0:
                continue
            if login in excluded_set:
                continue

            action = _to_int(_value(deal, "Action", "action"), None)
            volume_units = _deal_volume_units(deal)
            if not _is_trade_deal(deal, action, volume_units):
                continue

            user_meta = users_by_login.get(login, {})
            profit = _to_float(_value(deal, "Profit", "profit"), 0.0)
            commission = _to_float(_value(deal, "Commission", "commission"), 0.0)
            fee = _to_float(_value(deal, "Fee", "fee"), 0.0)
            swap = _to_float(_value(deal, "Storage", "storage", "Swap", "swap"), 0.0)
            pnl = profit + commission + fee + swap
            volume = volume_units / 10000.0
            time_value = _to_int(_value(deal, "Time", "time", "Datetime", "datetime"), 0) or 0

            deal_rows.append(
                {
                    "Login": int(login),
                    "Name": user_meta.get("name", ""),
                    "Group": user_meta.get("group", group),
                    "Symbol": str(_value(deal, "Symbol", "symbol", default="") or ""),
                    "Volume": round(volume, 2),
                    "Time": datetime.fromtimestamp(time_value, tz=timezone.utc).isoformat() if time_value else "",
                    "Profit": round(profit, 2),
                    "Commission": round(commission, 2),
                    "Fee": round(fee, 2),
                    "Swap": round(swap, 2),
                    "PnL": round(pnl, 2),
                }
            )

    deals_df = build_deals_dataframe(deal_rows)
    client_report = compute_client_report(deals_df)
    group_summary = compute_group_summary(client_report)
    kpis = compute_kpis(client_report)
    return deals_df, client_report, group_summary, kpis, warnings


def _group_root_name(group: str) -> str:
    parts = [part.strip() for part in re.split(r"[\\/]+", str(group or "")) if part.strip()]
    return parts[0] if parts else str(group or "")


def _snapshot_dataframe(
    daily_rows: list[dict[str, Any]],
    users_by_login: dict[int, dict[str, str]],
    snapshot_date: date,
) -> pd.DataFrame:
    """Build an EOD snapshot from MT5 daily rows for the exact report date."""
    latest_by_login: dict[int, tuple[date, int, dict[str, Any]]] = {}
    for order, row in enumerate(daily_rows):
        login = _to_int(_value(row, "Login", "login"), None)
        if login is None or login not in users_by_login:
            continue
        row_day = _row_date(_value(row, "Datetime", "datetime", "Time", "time"))
        if row_day != snapshot_date:
            continue
        previous = latest_by_login.get(login)
        candidate = (row_day, order, row)
        if previous is None or candidate[:2] >= previous[:2]:
            latest_by_login[login] = candidate

    rows: list[dict[str, Any]] = []
    for login in sorted(latest_by_login):
        _, _, row = latest_by_login[login]
        meta = users_by_login.get(login, {})
        equity = _daily_equity_value(row)
        rows.append(
            {
                "Login": int(login),
                "Name": meta.get("name", str(_value(row, "Name", "name", default="") or "")),
                "Equity": round(float(equity), 2),
            }
        )

    return pd.DataFrame(rows, columns=["Login", "Name", "Equity"])


def _floor_negative_equity_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df

    normalized = df.copy()
    for column in ("Equity", "equity", "ProfitEquity", "profit_equity"):
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce").fillna(0.0).clip(lower=0.0)
    return normalized


def _floor_negative_equity_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            normalized_rows.append(row)
            continue

        normalized = dict(row)
        for column in ("Equity", "equity"):
            if column in normalized:
                normalized[column] = max(_to_float(normalized[column], 0.0), 0.0)
        normalized_rows.append(normalized)

    return normalized_rows


def _write_sheet_table(
    ws,
    df: pd.DataFrame,
    *,
    start_row: int = 1,
    start_col: int = 1,
    money_cols: set[str] | None = None,
    percent_cols: set[str] | None = None,
    int_cols: set[str] | None = None,
    date_cols: set[str] | None = None,
    conditional_cols: set[str] | None = None,
    theme: WorkbookTheme = BROKERAGE_THEME,
    alternate_rows: bool = True,
    warning_rows: bool = False,
    freeze_panes: str | None = None,
    auto_filter: bool = True,
    auto_width: bool = True,
):
    """Write a clean native Excel range without colors, borders, or custom fonts."""
    del conditional_cols, theme, alternate_rows, warning_rows
    money_cols = money_cols or set()
    percent_cols = percent_cols or set()
    int_cols = int_cols or set()
    date_cols = date_cols or set()
    headers = list(df.columns)

    for column_index, header in enumerate(headers, start=start_col):
        ws.cell(start_row, column_index, header)

    rows = list(df.itertuples(index=False, name=None))
    for row_offset, values in enumerate(rows, start=1):
        for column_offset, header in enumerate(headers):
            value = values[column_offset]
            if value is None or pd.isna(value):
                value = None
            cell = ws.cell(start_row + row_offset, start_col + column_offset, value)
            if header in money_cols:
                cell.number_format = "#,##0.00"
            elif header in percent_cols:
                cell.number_format = "0.00"
            elif header in int_cols:
                cell.number_format = "0"
            elif header in date_cols:
                cell.number_format = "yyyy-mm-dd"

    if headers:
        first_letter = get_column_letter(start_col)
        last_letter = get_column_letter(start_col + len(headers) - 1)
        ws.freeze_panes = freeze_panes or f"{first_letter}{start_row + 1}"
        if auto_filter:
            ws.auto_filter.ref = f"{first_letter}{start_row}:{last_letter}{start_row + len(rows)}"
        if auto_width:
            _apply_table_widths(ws, df=df, headers=headers, start_col=start_col)
    ws.sheet_view.showGridLines = True


def _write_styled_sheet_table(
    ws,
    df: pd.DataFrame,
    *,
    start_row: int = 1,
    start_col: int = 1,
    money_cols: set[str] | None = None,
    percent_cols: set[str] | None = None,
    int_cols: set[str] | None = None,
    date_cols: set[str] | None = None,
    conditional_cols: set[str] | None = None,
    theme: WorkbookTheme = BROKERAGE_THEME,
    alternate_rows: bool = True,
    warning_rows: bool = False,
    freeze_panes: str | None = None,
    auto_filter: bool = True,
    auto_width: bool = True,
):
    money_cols = money_cols or set()
    percent_cols = percent_cols or set()
    int_cols = int_cols or set()
    date_cols = date_cols or set()
    conditional_cols = conditional_cols or set()

    headers = list(df.columns)
    header_fill = _fill(theme.surface_alt)
    header_font = _font(theme.primary_text, bold=True)
    body_font = _font(theme.primary_text, size=11)
    body_fill = _fill(theme.surface)
    body_fill_alt = _fill(theme.surface_alt)
    warning_fill = _fill(theme.surface_alt)
    warning_font = _font(theme.primary_text, size=11, bold=True)
    total_fill = _fill(theme.surface_alt)
    total_font = _font(theme.primary_text, size=11, bold=True)
    body_alignment_left = _alignment("left")
    body_alignment_right = _alignment("right")
    body_alignment_center = _alignment("center")

    header_row = start_row
    last_col = start_col + max(len(headers) - 1, 0)
    start_letter = get_column_letter(start_col)
    end_letter = get_column_letter(last_col) if headers else start_letter

    for col_idx, header in enumerate(headers, start=start_col):
        cell = ws.cell(header_row, col_idx, header)
        _style_cell(
            cell,
            fill=header_fill,
            font=header_font,
            alignment=_alignment("center"),
            border=_card_border(theme.border),
        )
    ws.row_dimensions[header_row].height = 22

    rows = list(df.itertuples(index=False, name=None))
    total_row_index = _collect_total_row_index(rows)
    total_row_number = header_row + 1 + total_row_index if total_row_index is not None else None

    for row_offset, row_values in enumerate(rows, start=1):
        excel_row = header_row + row_offset
        row_is_total = _is_total_row(row_values)
        row_fill = total_fill if row_is_total else warning_fill if warning_rows else body_fill_alt if alternate_rows and row_offset % 2 == 0 else body_fill
        row_font = total_font if row_is_total else warning_font if warning_rows else body_font
        ws.row_dimensions[excel_row].height = 21 if not row_is_total else 22

        for col_idx, header in enumerate(headers, start=start_col):
            value = row_values[col_idx - start_col]
            if value is None or (isinstance(value, float) and math.isnan(value)) or pd.isna(value):
                value = None
            cell = ws.cell(excel_row, col_idx, value)
            if row_is_total:
                _style_cell(
                    cell,
                    fill=total_fill,
                    font=total_font,
                    alignment=body_alignment_right if header in int_cols | money_cols | percent_cols else body_alignment_left,
                    border=_card_border(theme.border),
                )
                if header in money_cols:
                    cell.number_format = "#,##0.00"
                elif header in percent_cols:
                    cell.number_format = "0.00"
                elif header in int_cols:
                    cell.number_format = "0"
                elif header in date_cols:
                    cell.number_format = "yyyy-mm-dd"
                continue

            if warning_rows:
                fill = warning_fill
                font = warning_font
            else:
                fill = body_fill_alt if alternate_rows and row_offset % 2 == 0 else body_fill
                font = row_font

            if header in money_cols:
                number_format = "#,##0.00"
                alignment = body_alignment_right
            elif header in percent_cols:
                number_format = "0.00"
                alignment = body_alignment_right
            elif header in int_cols:
                number_format = "0"
                alignment = body_alignment_right
            elif header in date_cols:
                number_format = "yyyy-mm-dd"
                alignment = body_alignment_center
            else:
                number_format = None
                alignment = body_alignment_left

            _style_cell(
                cell,
                fill=fill,
                font=font,
                alignment=alignment,
                border=_body_border(theme.border),
                number_format=number_format,
            )

    if headers:
        freeze_target = freeze_panes or f"{start_letter}{header_row + 1}"
        ws.freeze_panes = freeze_target
        if auto_filter:
            ws.auto_filter.ref = f"{start_letter}{header_row}:{end_letter}{header_row + len(rows)}"

        if auto_width:
            _apply_table_widths(ws, df=df, headers=headers, start_col=start_col)

        data_start_row = header_row + 1
        data_end_row = header_row + len(rows)
        if total_row_number is not None and total_row_number >= data_start_row:
            data_end_row = total_row_number - 1
        if conditional_cols and data_end_row >= data_start_row:
            _apply_positive_negative_rules(
                ws,
                start_row=data_start_row,
                end_row=data_end_row,
                start_col=start_col,
                headers=headers,
                columns=conditional_cols,
                theme=theme,
            )

    return None


def _build_brokerage_sheet(
    ws,
    *,
    totals: dict[str, float],
    lp_summary: pd.DataFrame,
    all_clients_pnl: float,
    report_label: str,
    profile_name: str = "",
    selected_groups_label: str = "",
    summary_window_label: str = "",
    generated_at: datetime | None = None,
    show_lp_summary: bool = False,
):
    """Create a plain summary sheet using ordinary cells and native gridlines."""
    generated = generated_at or datetime.now(timezone.utc)
    metadata = [
        ("Profile", profile_name or "Selected profile"),
        ("Groups", selected_groups_label or "All groups"),
        ("Window", summary_window_label or report_label),
        ("Generated", generated.strftime("%Y-%m-%d %H:%M UTC")),
    ]
    ws.cell(1, 2, "Brokerage Summary")
    for offset, (label, value) in enumerate(metadata, start=3):
        ws.cell(offset, 2, label)
        ws.cell(offset, 3, value)

    total_accounts = int(totals.get("total_accounts", 0) or 0)
    total_trades = int(totals.get("total_trades", 0) or 0)
    gross_exposure = float(totals.get("gross_exposure", 0.0) or 0.0)
    total_pnl = float(totals.get("total_pnl", all_clients_pnl) or all_clients_pnl or 0.0)
    summary_rows = [
        ("Opening Equity", _format_money_text(totals.get("opening_equity", 0.0))),
        ("Closing Equity", _format_money_text(totals.get("closing_equity", 0.0))),
        ("Difference", _format_money_text(totals.get("difference", 0.0))),
        ("Deposit", _format_money_text(totals.get("deposit", 0.0))),
        ("Withdrawal", _format_money_text(totals.get("withdrawal", 0.0))),
        ("Credit In", _format_money_text(totals.get("credit_in", 0.0))),
        ("Credit Out", _format_money_text(totals.get("credit_out", 0.0))),
        ("Net Credit", _format_money_text(totals.get("net_credit", 0.0))),
        ("Additional", _format_money_text(totals.get("additional", 0.0))),
        ("Total LP PnL", _format_money_text(totals.get("total_lp_pnl", 0.0))),
        ("Client Book PnL", _format_money_text(all_clients_pnl)),
        ("Total PnL", _format_money_text(total_pnl)),
        ("Net Brokerage", _format_money_text(totals.get("net_brokerage", 0.0))),
        ("Total Trades", total_trades),
        ("Total Accounts", total_accounts),
        ("Gross Exposure", _format_money_text(gross_exposure)),
    ]
    summary_df = pd.DataFrame(summary_rows, columns=["Metric", "Value"])
    _write_sheet_table(ws, summary_df, start_row=8, start_col=2, auto_filter=False)

    if show_lp_summary:
        lp_sheet_df = lp_summary.copy() if isinstance(lp_summary, pd.DataFrame) else pd.DataFrame(columns=["LP Name", "PNL"])
        if not lp_sheet_df.empty and "PNL" in lp_sheet_df.columns:
            lp_total = round(float(pd.to_numeric(lp_sheet_df["PNL"], errors="coerce").fillna(0.0).sum()), 2)
            lp_sheet_df = pd.concat([lp_sheet_df, pd.DataFrame([{"LP Name": "Total", "PNL": lp_total}])], ignore_index=True)
        _write_sheet_table(ws, lp_sheet_df, start_row=8, start_col=5, money_cols={"PNL"})

    ws.freeze_panes = "B9"
    ws.column_dimensions["B"].width = 24
    ws.column_dimensions["C"].width = 24
    ws.sheet_view.showGridLines = True


def _build_styled_brokerage_sheet(
    ws,
    *,
    totals: dict[str, float],
    lp_summary: pd.DataFrame,
    all_clients_pnl: float,
    report_label: str,
    profile_name: str = "",
    selected_groups_label: str = "",
    summary_window_label: str = "",
    generated_at: datetime | None = None,
    show_lp_summary: bool = False,
):
    theme = BROKERAGE_THEME
    _configure_sheet_layout(ws, zoom_scale=105, landscape=True, fit_to_width=1)

    for col, width in {
        "A": 3,
        "B": 30,
        "C": 18,
        "D": 3,
        "E": 24,
        "F": 18,
        "G": 3,
        "H": 22,
        "I": 18,
        "J": 18,
        "K": 18,
    }.items():
        ws.column_dimensions[col].width = width

    metadata = [
        ("Profile", profile_name or "Selected profile"),
        ("Groups", selected_groups_label or "All groups"),
        ("Window", report_label),
        (
            "Generated",
            (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC"),
        ),
    ]
    next_row = _write_title_block(
        ws,
        title="Brokerage Summary",
        subtitle="Plain brokerage totals and LP performance in a standard Excel layout.",
        metadata=metadata,
        start_row=1,
        start_col=2,
        end_col=5,
        theme=theme,
    )

    total_accounts = int(totals.get("total_accounts", 0) or 0)
    total_trades = int(totals.get("total_trades", 0) or 0)
    gross_exposure = float(totals.get("gross_exposure", 0.0) or 0.0)
    total_pnl = float(totals.get("total_pnl", all_clients_pnl) or all_clients_pnl or 0.0)
    net_brokerage = float(totals.get("net_brokerage", 0.0) or 0.0)
    summary_rows = [
        ("Opening Equity", _format_money_text(totals.get("opening_equity", 0.0))),
        ("Closing Equity", _format_money_text(totals.get("closing_equity", 0.0))),
        ("Difference", _format_money_text(totals.get("difference", 0.0))),
        ("Deposit", _format_money_text(totals.get("deposit", 0.0))),
        ("Withdrawal", _format_money_text(totals.get("withdrawal", 0.0))),
        ("Credit In", _format_money_text(totals.get("credit_in", 0.0))),
        ("Credit Out", _format_money_text(totals.get("credit_out", 0.0))),
        ("Net Credit", _format_money_text(totals.get("net_credit", 0.0))),
        ("Additional", _format_money_text(totals.get("additional", 0.0))),
        ("Total LP PnL", _format_money_text(totals.get("total_lp_pnl", 0.0))),
        ("Client Book PnL", _format_money_text(all_clients_pnl)),
        ("Total PnL", _format_money_text(total_pnl)),
        ("Net Brokerage", _format_money_text(net_brokerage)),
        ("Total Trades", f"{total_trades:,}"),
        ("Total Accounts", f"{total_accounts:,}"),
        ("Gross Exposure", _format_money_text(gross_exposure)),
    ]
    summary_df = pd.DataFrame(summary_rows, columns=["Metric", "Value"])

    summary_section_row = next_row + 2
    summary_section_end = _write_section_header(
        ws,
        row=summary_section_row,
        start_col=2,
        end_col=5,
        title="Brokerage Summary",
        subtitle="Plain brokerage totals and cash-flow bridge in a standard Excel layout.",
        theme=theme,
    )

    summary_table_row = summary_section_end + 1
    summary_freeze = f"B{summary_table_row + 1}"
    _write_sheet_table(
        ws,
        summary_df,
        start_row=summary_table_row,
        start_col=2,
        theme=theme,
        alternate_rows=True,
        warning_rows=False,
        freeze_panes=summary_freeze,
        auto_filter=False,
    )

    summary_row_lookup = {str(metric): summary_table_row + index + 1 for index, metric in enumerate(summary_df["Metric"].tolist())}
    for metric_name in ("Difference", "Net Brokerage", "Total PnL"):
        row_idx = summary_row_lookup.get(metric_name)
        if row_idx is None:
            continue
        for col_idx in range(2, 4):
            cell = ws.cell(row_idx, col_idx)
            _style_cell(
                cell,
                fill=_fill(theme.surface_alt),
                font=_font(theme.primary_text, size=11, bold=True),
                alignment=_alignment("right" if col_idx == 3 else "left"),
                border=_card_border(theme.border),
            )
        ws.row_dimensions[row_idx].height = 21

    lp_section_row = summary_table_row + len(summary_df) + 3
    if show_lp_summary:
        lp_section_end = _write_section_header(
            ws,
            row=lp_section_row,
            start_col=2,
            end_col=5,
            title="LP Summary",
            subtitle="LP-level performance for the selected brokerage window.",
            theme=theme,
        )

        lp_sheet_df = lp_summary.copy() if isinstance(lp_summary, pd.DataFrame) else pd.DataFrame(columns=["LP Name", "PNL"])
        if not lp_sheet_df.empty and "PNL" in lp_sheet_df.columns:
            lp_total = round(float(pd.to_numeric(lp_sheet_df["PNL"], errors="coerce").fillna(0.0).sum()), 2)
            lp_sheet_df = pd.concat(
                [
                    lp_sheet_df,
                    pd.DataFrame([{"LP Name": "Total", "PNL": lp_total}]),
                ],
                ignore_index=True,
            )
        elif lp_sheet_df.empty:
            lp_sheet_df = pd.DataFrame([{"LP Name": "No LP Entries", "PNL": 0.0}])

        lp_table_row = lp_section_end + 1
        _write_sheet_table(
            ws,
            lp_sheet_df,
            start_row=lp_table_row,
            start_col=2,
            money_cols={"PNL"},
            theme=theme,
            alternate_rows=True,
            warning_rows=lp_sheet_df.shape[0] == 1 and str(lp_sheet_df.iloc[0].get("LP Name", "")).lower().startswith("no lp"),
            freeze_panes=summary_freeze,
            auto_filter=True,
        )

    ws.freeze_panes = summary_freeze
    ws.sheet_view.showGridLines = True
    ws.print_title_rows = f"{summary_table_row}:{summary_table_row}"


def _write_premium_table_sheet(
    ws,
    *,
    title: str,
    subtitle: str,
    metadata: list[tuple[str, str]],
    df: pd.DataFrame,
    start_col: int = 1,
    end_col: int | None = None,
    table_start_row: int | None = None,
    money_cols: set[str] | None = None,
    percent_cols: set[str] | None = None,
    int_cols: set[str] | None = None,
    date_cols: set[str] | None = None,
    conditional_cols: set[str] | None = None,
    warning_rows: bool = False,
    theme: WorkbookTheme = BROKERAGE_THEME,
    zoom_scale: int = 95,
    landscape: bool = True,
    print_title_row: int | None = None,
    freeze_panes: str | None = None,
):
    """Write an export sheet as one plain, tidy data table."""
    del title, subtitle, metadata, end_col, table_start_row, conditional_cols
    del warning_rows, theme, zoom_scale, landscape, print_title_row
    _write_sheet_table(
        ws,
        df,
        start_row=1,
        start_col=start_col,
        money_cols=money_cols,
        percent_cols=percent_cols,
        int_cols=int_cols,
        date_cols=date_cols,
        freeze_panes=freeze_panes,
    )
    return 1


def _write_styled_premium_table_sheet(
    ws,
    *,
    title: str,
    subtitle: str,
    metadata: list[tuple[str, str]],
    df: pd.DataFrame,
    start_col: int = 1,
    end_col: int | None = None,
    table_start_row: int | None = None,
    money_cols: set[str] | None = None,
    percent_cols: set[str] | None = None,
    int_cols: set[str] | None = None,
    date_cols: set[str] | None = None,
    conditional_cols: set[str] | None = None,
    warning_rows: bool = False,
    theme: WorkbookTheme = BROKERAGE_THEME,
    zoom_scale: int = 95,
    landscape: bool = True,
    print_title_row: int | None = None,
    freeze_panes: str | None = None,
):
    _configure_sheet_layout(ws, zoom_scale=zoom_scale, landscape=landscape, fit_to_width=1)
    table_width = max(len(df.columns), 1)
    end_col = end_col or max(start_col + table_width - 1, start_col + 7)
    header_end_row = _write_title_block(
        ws,
        title=title,
        subtitle=subtitle,
        metadata=metadata,
        start_row=1,
        start_col=start_col,
        end_col=end_col,
        theme=theme,
    )
    table_row = table_start_row or (header_end_row + 2)
    _write_sheet_table(
        ws,
        df,
        start_row=table_row,
        start_col=start_col,
        money_cols=money_cols,
        percent_cols=percent_cols,
        int_cols=int_cols,
        date_cols=date_cols,
        conditional_cols=conditional_cols,
        theme=theme,
        alternate_rows=True,
        warning_rows=warning_rows,
        freeze_panes=freeze_panes,
        auto_filter=True,
    )
    if print_title_row is not None:
        ws.print_title_rows = f"{print_title_row}:{print_title_row}"
    elif table_row is not None:
        ws.print_title_rows = f"{table_row}:{table_row}"
    return table_row


def _ranked_account_slice(
    accounts_df: pd.DataFrame,
    *,
    ascending: bool,
    start_rank: int = 10,
    end_rank: int = 20,
) -> pd.DataFrame:
    if not isinstance(accounts_df, pd.DataFrame) or accounts_df.empty:
        base_columns = list(accounts_df.columns) if isinstance(accounts_df, pd.DataFrame) else []
        return pd.DataFrame(columns=["Rank", *base_columns])

    sort_columns = ["NET PNL USD"]
    if "Login" in accounts_df.columns:
        sort_columns.append("Login")
        ascending_flags = [ascending, True]
    else:
        ascending_flags = [ascending]

    sorted_df = accounts_df.sort_values(by=sort_columns, ascending=ascending_flags, kind="stable").reset_index(drop=True)
    start_index = max(start_rank - 1, 0)
    stop_index = min(end_rank, len(sorted_df))
    sliced = sorted_df.iloc[start_index:stop_index].copy()
    if sliced.empty:
        return pd.DataFrame(columns=["Rank", *sorted_df.columns])

    sliced.insert(0, "Rank", list(range(start_rank, start_rank + len(sliced))))
    return sliced


def build_brokerage_export_payload(
    api_handle,
    groups: list[str],
    opening_equity_date: date,
    closing_equity_date: date,
    summary_from: date,
    summary_to: date,
    clip_negative_equity: bool = False,
    excluded_logins: list[int] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    excluded_login_set = _normalize_login_set(excluded_logins)
    users_by_login, group_map, scope_warnings = _collect_user_scope(api_handle, groups, excluded_login_set)
    warnings.extend(scope_warnings)
    scoped_logins = set(users_by_login)

    summary_start = summary_from
    summary_end = summary_to
    summary_available = summary_from <= summary_to
    default_no_gap = (
        summary_from == opening_equity_date + timedelta(days=1)
        and summary_to == closing_equity_date
        and opening_equity_date <= closing_equity_date
    )
    if not summary_available and not default_no_gap:
        summary_start, summary_end, _, _ = _date_range_utc(summary_from, summary_to)
        summary_available = True
        warnings.append("Summary date range was reversed; using Date From <= Date To.")

    summary_from_ts: int | None = None
    summary_to_ts: int | None = None
    if summary_available:
        _, _, summary_from_ts, summary_to_ts = _date_range_utc(summary_start, summary_end)

    combined_dates = [opening_equity_date, closing_equity_date]
    if summary_available:
        combined_dates.extend([summary_start, summary_end])
    combined_start = min(combined_dates)
    combined_end = max(combined_dates)
    _, _, combined_from_ts, combined_to_ts = _date_range_utc(combined_start, combined_end)

    daily_raw_rows, daily_warnings = _fetch_scoped_daily_records(
        api_handle,
        groups,
        combined_from_ts,
        combined_to_ts,
        scoped_logins,
    )
    warnings.extend(daily_warnings)

    daily_rows = daily_to_dicts(daily_raw_rows)
    if clip_negative_equity:
        daily_rows = _floor_negative_equity_rows(daily_rows)
    daily_summary_display = (
        build_daily_summary_display(daily_rows, summary_start, summary_end)
        if summary_available
        else build_daily_summary_display([], summary_from, summary_to)
    )

    # Use the same normalized daily series for snapshots and the daily summary.
    # This prevents the dashboard, account table, totals, and Excel export from
    # disagreeing about what constitutes equity.
    opening_df = _snapshot_dataframe(daily_rows, users_by_login, opening_equity_date)
    closing_df = _snapshot_dataframe(daily_rows, users_by_login, closing_equity_date)
    if clip_negative_equity:
        opening_df = _floor_negative_equity_dataframe(opening_df)
        closing_df = _floor_negative_equity_dataframe(closing_df)

    scoped_account_count = len(scoped_logins)
    opening_account_count = int(opening_df["Login"].nunique()) if not opening_df.empty else 0
    closing_account_count = int(closing_df["Login"].nunique()) if not closing_df.empty else 0
    opening_missing_count = max(scoped_account_count - opening_account_count, 0)
    closing_missing_count = max(scoped_account_count - closing_account_count, 0)
    if opening_missing_count:
        warnings.append(
            f"Opening snapshot is incomplete: {opening_missing_count} of {scoped_account_count} scoped account(s) "
            f"have no MT5 daily row for {opening_equity_date.isoformat()}."
        )
    if closing_missing_count:
        warnings.append(
            f"Closing snapshot is incomplete: {closing_missing_count} of {scoped_account_count} scoped account(s) "
            f"have no MT5 daily row for {closing_equity_date.isoformat()}."
        )

    trade_summary_df = pd.DataFrame(columns=SUMMARY_COLUMNS)
    if summary_available and summary_from_ts is not None and summary_to_ts is not None:
        deals_raw_rows, deal_warnings = _fetch_scoped_deals(
            api_handle,
            groups,
            summary_from_ts,
            summary_to_ts,
            scoped_logins,
        )
        warnings.extend(deal_warnings)
        trade_summary_df, trade_summary_warnings = summarize_deals_records(
            deals_raw_rows,
            {login: meta.get("name", "") for login, meta in users_by_login.items()},
        )
        warnings.extend(trade_summary_warnings)

    equity_report_df = compute_equity_report(
        opening_df.to_dict("records"),
        closing_df.to_dict("records"),
        trade_summary_df.to_dict("records"),
        group_map,
        None,
    )
    if equity_report_df.empty:
        equity_report_df = pd.DataFrame(
            columns=[
                "Login",
                "Group",
                "Opening Equity",
                "Closing Equity",
                "Difference",
                "Net D/W",
                "Deposit",
                "Withdrawal",
                "Credit In",
                "Credit Out",
                "Net Credit",
                "Bonus",
                "Commission",
                "Fee",
                "Swap",
                "Closed Volume",
                "Closed Lots",
                "Net P&L",
                "Net P&L %",
                "LP P&L",
            ]
        )

    full_df = equity_report_df.copy()
    if not trade_summary_df.empty and "Login" in trade_summary_df.columns:
        trade_metrics = trade_summary_df[
            ["Login", "Volume", "Commission", "Fee", "Swap"]
        ].rename(
            columns={
                "Volume": "Trade Closed Lots",
                "Commission": "Trade Commission",
                "Fee": "Trade Fee",
                "Swap": "Trade Swap",
            }
        )
        full_df = full_df.merge(trade_metrics, on="Login", how="left")
        trade_count_metrics = trade_summary_df[
            ["Login", "Total Trades", "Winning Trades", "Losing Trades", "Breakeven Trades", "Win %", "Loss %"]
        ].copy()
        full_df = full_df.merge(trade_count_metrics, on="Login", how="left")
        if "Trade Closed Lots" in full_df.columns:
            full_df["Closed Lots"] = pd.to_numeric(full_df["Trade Closed Lots"], errors="coerce").fillna(
                pd.to_numeric(full_df.get("Closed Lots", 0.0), errors="coerce").fillna(0.0)
            )
        if "Trade Commission" in full_df.columns:
            full_df["Commission"] = pd.to_numeric(full_df["Trade Commission"], errors="coerce").fillna(
                pd.to_numeric(full_df.get("Commission", 0.0), errors="coerce").fillna(0.0)
            )
        if "Trade Fee" in full_df.columns:
            full_df["Fee"] = pd.to_numeric(full_df["Trade Fee"], errors="coerce").fillna(
                pd.to_numeric(full_df.get("Fee", 0.0), errors="coerce").fillna(0.0)
            )
        if "Trade Swap" in full_df.columns:
            full_df["Swap"] = pd.to_numeric(full_df["Trade Swap"], errors="coerce").fillna(
                pd.to_numeric(full_df.get("Swap", 0.0), errors="coerce").fillna(0.0)
            )
        full_df = full_df.drop(
            columns=["Trade Closed Lots", "Trade Commission", "Trade Fee", "Trade Swap"],
            errors="ignore",
        )
    for column in [
        "Deposit",
        "Withdrawal",
        "Net D/W",
        "Credit In",
        "Credit Out",
        "Net Credit",
        "Bonus",
        "Commission",
        "Fee",
        "Swap",
        "Closed Volume",
        "Closed Lots",
        "Net P&L",
        "Net P&L %",
        "LP P&L",
    ]:
        if column not in full_df.columns:
            full_df[column] = 0.0
        full_df[column] = pd.to_numeric(full_df[column], errors="coerce").fillna(0.0)

    full_df["Name"] = full_df["Login"].map(lambda login: users_by_login.get(int(login), {}).get("name", "") if pd.notna(login) else "")
    full_df["Currency"] = full_df["Login"].map(lambda login: users_by_login.get(int(login), {}).get("currency", "USD") if pd.notna(login) else "USD")
    full_df["Deposit"] = full_df["Deposit"].round(2)
    full_df["Withdrawal"] = full_df["Withdrawal"].round(2)
    full_df["Credit In"] = full_df["Credit In"].round(2)
    full_df["Credit Out"] = full_df["Credit Out"].round(2)
    full_df["Net Credit"] = full_df["Net Credit"].round(2)
    full_df["Bonus"] = full_df["Bonus"].round(2)
    full_df["Commission"] = full_df["Commission"].round(2)
    full_df["Fee"] = full_df["Fee"].round(2)
    full_df["Swap"] = full_df["Swap"].round(2)
    full_df["Closed Volume"] = full_df["Closed Volume"].round(2)
    full_df["Closed Lots"] = full_df["Closed Lots"].round(2)
    full_df["NET DP/WD"] = full_df["Net D/W"].round(2)
    full_df["Credit"] = full_df["Net Credit"].round(2)
    full_df["NET PNL USD"] = full_df["Net P&L"].round(2)
    full_df["NET PNL %"] = np.where(
        full_df["Opening Equity"] > 0,
        (full_df["NET PNL USD"] / full_df["Opening Equity"]) * 100.0,
        0.0,
    )
    full_df["Additional"] = full_df["Bonus"].round(2)
    full_df["EOD Closing Equity Date"] = closing_equity_date.isoformat()
    for column in ["Total Trades", "Winning Trades", "Losing Trades", "Breakeven Trades"]:
        if column not in full_df.columns:
            full_df[column] = 0
        full_df[column] = pd.to_numeric(full_df[column], errors="coerce").fillna(0).astype(int)
    for column in ["Win %", "Loss %"]:
        if column not in full_df.columns:
            full_df[column] = 0.0
        full_df[column] = pd.to_numeric(full_df[column], errors="coerce").fillna(0.0).round(1)

    full_df = full_df.sort_values(by=["Login"], kind="stable").reset_index(drop=True)

    accounts_display = full_df[
        [
            "Login",
            "Name",
            "Group",
            "Currency",
            "Closed Lots",
            "Total Trades",
            "Winning Trades",
            "Losing Trades",
            "Breakeven Trades",
            "Win %",
            "Loss %",
            "NET DP/WD",
            "Credit",
            "Opening Equity",
            "Closing Equity",
            "NET PNL USD",
            "NET PNL %",
            "Deposit",
            "Withdrawal",
            "Commission",
            "Swap",
            "EOD Closing Equity Date",
        ]
    ].copy()

    group_summary_display = (
        full_df.groupby("Group", as_index=False)
        .agg(
            Accounts=("Login", "nunique"),
            Closed_Lots=("Closed Lots", "sum"),
            NET_DP_WD=("NET DP/WD", "sum"),
            Credit=("Credit", "sum"),
            NET_PNL_USD=("NET PNL USD", "sum"),
        )
        .sort_values("NET_PNL_USD", ascending=False)
        .reset_index(drop=True)
    )

    books_display = pd.DataFrame(
        [
            {
                "Accounts": int(full_df["Login"].nunique()),
                "Closed_Lots": round(float(full_df["Closed Lots"].sum()), 2),
                "NET_DP_WD": round(float(full_df["NET DP/WD"].sum()), 2),
                "Credit": round(float(full_df["Credit"].sum()), 2),
                "NET_PNL_USD": round(float(full_df["NET PNL USD"].sum()), 2),
            }
        ]
    )

    lp_summary = pd.DataFrame(columns=["LP Name", "PNL"])

    opening_total = round(float(opening_df["Equity"].sum()), 2) if not opening_df.empty else 0.0
    closing_total = round(float(closing_df["Equity"].sum()), 2) if not closing_df.empty else 0.0
    equity_diff = closing_total - opening_total
    if not trade_summary_df.empty:
        summary_deposit_series = pd.to_numeric(trade_summary_df.get("Deposit", 0.0), errors="coerce").fillna(0.0)
        summary_withdraw_series = pd.to_numeric(trade_summary_df.get("Withdraw", 0.0), errors="coerce").fillna(0.0)
        summary_credit_series = pd.to_numeric(trade_summary_df.get("Credit", 0.0), errors="coerce").fillna(0.0)
        summary_additional_series = pd.to_numeric(trade_summary_df.get("Additional", 0.0), errors="coerce").fillna(0.0)
        summary_profit_series = pd.to_numeric(trade_summary_df.get("Profit", 0.0), errors="coerce").fillna(0.0)
        deposit_total = round(float(summary_deposit_series.sum()), 2)
        withdrawal_total = round(float(summary_withdraw_series.sum()), 2)
        credit_in_total = round(float(summary_credit_series.clip(lower=0.0).sum()), 2)
        credit_out_total = round(abs(float(summary_credit_series.clip(upper=0.0).sum())), 2)
        additional_total = round(float(summary_additional_series.sum()), 2)
        net_credit_total = round(float(summary_credit_series.sum()), 2)
        all_clients_pnl = round(float(summary_profit_series.sum()), 2)
    else:
        deposit_total = 0.0
        withdrawal_total = 0.0
        credit_in_total = 0.0
        credit_out_total = 0.0
        additional_total = 0.0
        net_credit_total = 0.0
        all_clients_pnl = 0.0
    total_lp_pnl = round(float(lp_summary["PNL"].sum()), 2) if not lp_summary.empty else 0.0
    brokerage_pnl = round(equity_diff - deposit_total - net_credit_total - additional_total + withdrawal_total, 2)

    return {
        "opening_snapshot": opening_df,
        "closing_snapshot": closing_df,
        "equity_report": full_df,
        "trade_summary": trade_summary_df,
        "accounts_display": accounts_display,
        "group_summary": group_summary_display,
        "books_summary": books_display,
        "daily_summary": daily_summary_display,
        "lp_summary": lp_summary if not lp_summary.empty else pd.DataFrame(columns=["LP Name", "PNL"]),
        "brokerage_totals": {
            "opening_equity": opening_total,
            "closing_equity": closing_total,
            "difference": round(equity_diff, 2),
            "deposit": deposit_total,
            "withdrawal": withdrawal_total,
            "credit_in": credit_in_total,
            "credit_out": credit_out_total,
            "net_credit": net_credit_total,
            "additional": additional_total,
            "total_lp_pnl": total_lp_pnl,
            "client_a_book_pnl": all_clients_pnl,
            "all_clients_pnl": all_clients_pnl,
            "net_brokerage": brokerage_pnl,
        },
        "data_quality": {
            "scoped_accounts": scoped_account_count,
            "opening_snapshot_accounts": opening_account_count,
            "closing_snapshot_accounts": closing_account_count,
            "opening_missing_accounts": opening_missing_count,
            "closing_missing_accounts": closing_missing_count,
            "opening_negative_accounts": int((opening_df["Equity"] < 0).sum()) if not opening_df.empty else 0,
            "closing_negative_accounts": int((closing_df["Equity"] < 0).sum()) if not closing_df.empty else 0,
            "negative_equity_clipped": bool(clip_negative_equity),
        },
        "warnings": warnings,
        "summary_range": (summary_from, summary_to),
    }


def build_brokerage_export_workbook(payload: dict[str, Any]) -> BytesIO:
    payload = dict(payload or {})
    warnings = list(payload.get("warnings") or [])
    clip_negative_equity = bool(payload.get("clip_negative_equity", False))
    generated_at = datetime.now(timezone.utc)
    report_start, report_end = _coerce_summary_range(payload.get("summary_range"))
    summary_range_label = (
        f"{report_start.isoformat()} to {report_end.isoformat()}"
        if isinstance(report_start, date) and isinstance(report_end, date)
        else "Selected period"
    )
    profile_name = str(
        payload.get("profile_name")
        or payload.get("client_name")
        or payload.get("broker_name")
        or payload.get("server")
        or "Selected profile"
    ).strip()
    selected_groups = [
        str(group).strip()
        for group in (payload.get("selected_groups") or [])
        if str(group).strip()
    ]
    selected_groups_label = "All groups" if not selected_groups else f"{len(selected_groups)} groups selected"

    accounts_columns = [
        "Login",
        "Name",
        "Group",
        "Currency",
        "Closed Lots",
        "NET DP/WD",
        "Credit",
        "Opening Equity",
        "Closing Equity",
        "NET PNL USD",
        "NET PNL %",
        "Deposit",
        "Withdrawal",
        "Commission",
        "Swap",
        "EOD Closing Equity Date",
    ]
    opening_columns = ["Login", "Name", "Equity"]
    group_columns = ["Group", "Accounts", "Closed_Lots", "NET_DP_WD", "Credit", "NET_PNL_USD"]
    books_columns = ["Accounts", "Closed_Lots", "NET_DP_WD", "Credit", "NET_PNL_USD"]
    daily_columns = [
        "Date",
        "Opening Equity",
        "Closing Equity",
        "Deposit",
        "Withdraw",
        "Credit",
        "Additional",
        "Broker PnL",
        "Net PnL %",
    ]
    lp_columns = ["LP Name", "PNL"]

    accounts_display = _records_to_dataframe(payload.get("accounts_display"), columns=accounts_columns).reindex(
        columns=accounts_columns
    )
    opening_report = _records_to_dataframe(payload.get("opening_report"), columns=opening_columns).reindex(
        columns=opening_columns
    )
    closing_report = _records_to_dataframe(payload.get("closing_report"), columns=opening_columns).reindex(
        columns=opening_columns
    )
    group_summary = _records_to_dataframe(payload.get("group_summary"), columns=group_columns).reindex(
        columns=group_columns
    )
    books_summary = _records_to_dataframe(payload.get("books_summary"), columns=books_columns).reindex(
        columns=books_columns
    )
    summary = _records_to_dataframe(payload.get("summary"), columns=SUMMARY_COLUMNS)
    summary_with_total = _records_to_dataframe(payload.get("summary_with_total"), columns=SUMMARY_COLUMNS)
    if summary_with_total.empty and not summary.empty:
        summary_with_total = append_total_row(summary)
    daily_summary = _records_to_dataframe(payload.get("daily_summary"), columns=daily_columns).reindex(
        columns=daily_columns
    )
    lp_summary = _records_to_dataframe(payload.get("lp_summary"), columns=lp_columns).reindex(columns=lp_columns)

    if clip_negative_equity:
        opening_report = _sanitize_equity_dataframe(opening_report, ("Equity", "equity", "ProfitEquity", "profit_equity"))
        closing_report = _sanitize_equity_dataframe(closing_report, ("Equity", "equity", "ProfitEquity", "profit_equity"))
        accounts_display = _sanitize_equity_dataframe(accounts_display, ("Opening Equity", "Closing Equity"))
        daily_summary = _sanitize_equity_dataframe(daily_summary, ("Opening Equity", "Closing Equity"))

    top_winners = _ranked_account_slice(accounts_display, ascending=False, start_rank=1, end_rank=10)
    top_losers = _ranked_account_slice(accounts_display, ascending=True, start_rank=1, end_rank=10)
    if clip_negative_equity:
        top_winners = _sanitize_equity_dataframe(top_winners, ("Opening Equity", "Closing Equity"))
        top_losers = _sanitize_equity_dataframe(top_losers, ("Opening Equity", "Closing Equity"))

    opening_total = round(float(opening_report["Equity"].sum()), 2) if not opening_report.empty else 0.0
    closing_total = round(float(closing_report["Equity"].sum()), 2) if not closing_report.empty else 0.0
    difference = round(closing_total - opening_total, 2)

    summary_deposit_series = pd.to_numeric(summary.get("Deposit", 0.0), errors="coerce").fillna(0.0)
    summary_withdraw_series = pd.to_numeric(summary.get("Withdraw", 0.0), errors="coerce").fillna(0.0)
    summary_credit_series = pd.to_numeric(summary.get("Credit", 0.0), errors="coerce").fillna(0.0)
    summary_additional_series = pd.to_numeric(summary.get("Additional", 0.0), errors="coerce").fillna(0.0)
    summary_profit_series = pd.to_numeric(summary.get("Profit", 0.0), errors="coerce").fillna(0.0)

    deposit_total = round(float(summary_deposit_series.sum()), 2) if not summary.empty else 0.0
    withdrawal_total = round(float(summary_withdraw_series.sum()), 2) if not summary.empty else 0.0
    credit_in_total = round(float(summary_credit_series.clip(lower=0.0).sum()), 2) if not summary.empty else 0.0
    credit_out_total = round(abs(float(summary_credit_series.clip(upper=0.0).sum())), 2) if not summary.empty else 0.0
    net_credit_total = round(float(summary_credit_series.sum()), 2) if not summary.empty else 0.0
    additional_total = round(float(summary_additional_series.sum()), 2) if not summary.empty else 0.0
    all_clients_pnl = round(float(summary_profit_series.sum()), 2) if not summary.empty else 0.0
    total_lp_pnl = round(float(pd.to_numeric(lp_summary.get("PNL", 0.0), errors="coerce").fillna(0.0).sum()), 2)

    total_accounts = int(accounts_display["Login"].nunique()) if not accounts_display.empty and "Login" in accounts_display.columns else 0
    total_trades = int(summary.shape[0])
    gross_exposure = round(opening_total + closing_total, 2)
    brokerage_pnl = round(difference - deposit_total - net_credit_total - additional_total + withdrawal_total, 2)

    brokerage_totals = dict(payload.get("brokerage_totals") or {})
    brokerage_totals.update(
        {
            "opening_equity": opening_total,
            "closing_equity": closing_total,
            "difference": difference,
            "deposit": deposit_total,
            "withdrawal": withdrawal_total,
            "credit_in": credit_in_total,
            "credit_out": credit_out_total,
            "net_credit": net_credit_total,
            "additional": additional_total,
            "total_lp_pnl": total_lp_pnl,
            "client_a_book_pnl": all_clients_pnl,
            "all_clients_pnl": all_clients_pnl,
            "net_brokerage": brokerage_pnl,
            "total_pnl": all_clients_pnl,
            "total_trades": total_trades,
            "total_accounts": total_accounts,
            "gross_exposure": gross_exposure,
        }
    )

    base_metadata = [
        ("Profile", profile_name),
        ("Groups", selected_groups_label),
        ("Window", summary_range_label),
        ("Generated", generated_at.strftime("%Y-%m-%d %H:%M UTC")),
    ]

    payload["opening_report"] = opening_report
    payload["closing_report"] = closing_report
    payload["accounts_display"] = accounts_display
    payload["group_summary"] = group_summary
    payload["books_summary"] = books_summary
    payload["summary"] = summary
    payload["summary_with_total"] = summary_with_total
    payload["daily_summary"] = daily_summary
    payload["lp_summary"] = lp_summary
    payload["top_winners"] = top_winners
    payload["top_losers"] = top_losers
    payload["warnings"] = warnings
    payload["brokerage_totals"] = brokerage_totals
    payload["summary_range"] = (report_start, report_end)

    wb = Workbook()
    pnl_ws = wb.active
    pnl_ws.title = "PnL"
    _build_brokerage_sheet(
        pnl_ws,
        totals=brokerage_totals,
        lp_summary=lp_summary,
        all_clients_pnl=all_clients_pnl,
        report_label=summary_range_label,
        profile_name=profile_name,
        selected_groups_label=selected_groups_label,
        summary_window_label=summary_range_label,
        generated_at=generated_at,
        show_lp_summary=True,
    )

    _write_premium_table_sheet(
        wb.create_sheet("Accounts"),
        title="Accounts Overview",
        subtitle=(
            "Per-account opening and closing equity, cash flow, and net PnL. "
            + (
                "Negative opening or closing equity is floored to zero in the export."
                if clip_negative_equity
                else "Negative opening or closing equity is preserved as returned by MT5."
            )
        ),
        metadata=base_metadata,
        df=accounts_display,
        money_cols={
            "Closed Lots",
            "NET DP/WD",
            "Credit",
            "Opening Equity",
            "Closing Equity",
            "NET PNL USD",
            "Deposit",
            "Withdrawal",
            "Commission",
            "Swap",
        },
        percent_cols={"NET PNL %"},
        int_cols={"Login"},
        date_cols={"EOD Closing Equity Date"},
        conditional_cols={"Opening Equity", "Closing Equity", "NET PNL USD"},
        freeze_panes=None,
    )

    _write_premium_table_sheet(
        wb.create_sheet("Groups"),
        title="Group Summary",
        subtitle="Grouped net PnL and account totals for the selected brokerage scope.",
        metadata=base_metadata,
        df=group_summary,
        money_cols={"Closed_Lots", "NET_DP_WD", "Credit", "NET_PNL_USD"},
        int_cols={"Accounts"},
        conditional_cols={"NET_PNL_USD"},
    )

    _write_premium_table_sheet(
        wb.create_sheet("Books"),
        title="Books Summary",
        subtitle="Firm-level totals rolled up from the selected groups.",
        metadata=base_metadata,
        df=books_summary,
        money_cols={"Closed_Lots", "NET_DP_WD", "Credit", "NET_PNL_USD"},
        int_cols={"Accounts"},
        conditional_cols={"NET_PNL_USD"},
    )

    _write_premium_table_sheet(
        wb.create_sheet("Opening Snapshot"),
        title="Opening Snapshot",
        subtitle=(
            f"Opening equity snapshot for {payload.get('opening_equity_date', 'the selected date')}. "
            + (
                "Negative values are floored to zero before export."
                if clip_negative_equity
                else "Negative values are preserved as returned by MT5."
            )
        ),
        metadata=base_metadata,
        df=opening_report,
        money_cols={"Equity"},
        int_cols={"Login"},
        conditional_cols={"Equity"},
    )

    _write_premium_table_sheet(
        wb.create_sheet("Closing Snapshot"),
        title="Closing Snapshot",
        subtitle=(
            f"Closing equity snapshot for {payload.get('closing_equity_date', 'the selected date')}. "
            + (
                "Negative values are floored to zero before export."
                if clip_negative_equity
                else "Negative values are preserved as returned by MT5."
            )
        ),
        metadata=base_metadata,
        df=closing_report,
        money_cols={"Equity"},
        int_cols={"Login"},
        conditional_cols={"Equity"},
    )

    _write_premium_table_sheet(
        wb.create_sheet("Summary Equity"),
        title="Summary Equity",
        subtitle=f"Opening and closing equity by day with broker PnL and net percentage for {summary_range_label}.",
        metadata=base_metadata,
        df=daily_summary,
        money_cols={"Opening Equity", "Closing Equity", "Deposit", "Withdraw", "Credit", "Additional", "Broker PnL"},
        percent_cols={"Net PnL %"},
        conditional_cols={"Opening Equity", "Closing Equity", "Broker PnL"},
    )

    _write_premium_table_sheet(
        wb.create_sheet("Top Winners"),
        title="Top Winners",
        subtitle="Highest net PnL accounts for the selected brokerage window.",
        metadata=base_metadata,
        df=top_winners,
        money_cols={
            "Closed Lots",
            "NET DP/WD",
            "Credit",
            "Opening Equity",
            "Closing Equity",
            "NET PNL USD",
            "Deposit",
            "Withdrawal",
            "Commission",
            "Swap",
        },
        percent_cols={"NET PNL %"},
        int_cols={"Rank", "Login"},
        date_cols={"EOD Closing Equity Date"},
        conditional_cols={"NET PNL USD", "Opening Equity", "Closing Equity"},
    )

    _write_premium_table_sheet(
        wb.create_sheet("Top Losers"),
        title="Top Losers",
        subtitle="Lowest net PnL accounts for the selected brokerage window.",
        metadata=base_metadata,
        df=top_losers,
        money_cols={
            "Closed Lots",
            "NET DP/WD",
            "Credit",
            "Opening Equity",
            "Closing Equity",
            "NET PNL USD",
            "Deposit",
            "Withdrawal",
            "Commission",
            "Swap",
        },
        percent_cols={"NET PNL %"},
        int_cols={"Rank", "Login"},
        date_cols={"EOD Closing Equity Date"},
        conditional_cols={"NET PNL USD", "Opening Equity", "Closing Equity"},
    )

    _write_premium_table_sheet(
        wb.create_sheet("Trades Summary"),
        title="Trades Summary",
        subtitle="Deposit, withdrawal, credit, volume, and profit for the selected period.",
        metadata=base_metadata,
        df=summary_with_total,
        money_cols={"Deposit", "Withdraw", "In/Out", "Credit", "Additional", "Volume", "Commission", "Fee", "Swap", "Profit"},
        int_cols={"Login", "Total Trades", "Winning Trades", "Losing Trades", "Breakeven Trades"},
        percent_cols={"Win %", "Loss %"},
        conditional_cols={"Profit"},
    )

    lp_sheet_df = lp_summary.copy()
    if not lp_sheet_df.empty and "PNL" in lp_sheet_df.columns:
        lp_total = round(float(pd.to_numeric(lp_sheet_df["PNL"], errors="coerce").fillna(0.0).sum()), 2)
        lp_sheet_df = pd.concat([lp_sheet_df, pd.DataFrame([{"LP Name": "Total", "PNL": lp_total}])], ignore_index=True)

    _write_premium_table_sheet(
        wb.create_sheet("LP Summary"),
        title="LP Summary",
        subtitle="LP-level brokerage PnL breakdown for the selected window.",
        metadata=base_metadata,
        df=lp_sheet_df,
        money_cols={"PNL"},
        conditional_cols={"PNL"},
    )

    if warnings:
        _write_premium_table_sheet(
            wb.create_sheet("Warnings"),
            title="Warnings",
            subtitle="Backend warnings, exclusions, and data quality notes.",
            metadata=base_metadata,
            df=pd.DataFrame({"Warning": warnings}),
            warning_rows=True,
            theme=BROKERAGE_THEME,
            zoom_scale=95,
            landscape=True,
        )

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _looks_like_brokerage_export_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False

    required_keys = {
        "accounts_display",
        "group_summary",
        "books_summary",
        "daily_summary",
        "lp_summary",
        "brokerage_totals",
        "summary_range",
    }
    return required_keys.issubset(payload.keys())


def build_deal_export_workbook(payload: dict[str, Any]) -> BytesIO:
    payload = dict(payload or {})
    client_columns = [
        "Login",
        "Name",
        "Group",
        "Closed Lots",
        "NET PNL USD",
        "Total Trades",
        "Winning Trades",
        "Losing Trades",
        "Breakeven Trades",
        "Win %",
        "Loss %",
        "Commission",
        "Fee",
        "Swap",
    ]
    group_columns = ["Group", "Accounts", "Closed Lots", "NET PNL USD", "Total Trades"]
    deal_columns = DEAL_TABLE_COLUMNS

    client_report = _records_to_dataframe(payload.get("client_report"), columns=client_columns).reindex(
        columns=client_columns
    )
    group_summary = _records_to_dataframe(payload.get("groups"), columns=group_columns).reindex(
        columns=group_columns
    )
    deal_details = _records_to_dataframe(payload.get("deals"), columns=deal_columns).reindex(
        columns=deal_columns
    )
    warnings = list(payload.get("warnings") or [])
    kpis = dict(payload.get("kpis") or {})
    selected_groups = [
        str(group).strip()
        for group in (payload.get("selected_groups") or [])
        if str(group).strip()
    ]
    report_start, report_end = _coerce_summary_range(payload.get("report_range"))
    report_label = (
        f"{report_start.isoformat()} to {report_end.isoformat()}"
        if isinstance(report_start, date) and isinstance(report_end, date)
        else "Selected period"
    )

    numeric_client_columns = [
        "Login",
        "Closed Lots",
        "NET PNL USD",
        "Total Trades",
        "Winning Trades",
        "Losing Trades",
        "Breakeven Trades",
        "Win %",
        "Loss %",
        "Commission",
        "Fee",
        "Swap",
    ]
    for column in numeric_client_columns:
        if column in client_report.columns:
            client_report[column] = pd.to_numeric(client_report[column], errors="coerce").fillna(0)
    if "Login" in client_report.columns:
        client_report["Login"] = client_report["Login"].astype(int)
    for column in ["Total Trades", "Winning Trades", "Losing Trades", "Breakeven Trades"]:
        if column in client_report.columns:
            client_report[column] = client_report[column].round(0).astype(int)
    for column in ["Win %", "Loss %"]:
        if column in client_report.columns:
            client_report[column] = client_report[column].round(1)

    numeric_group_columns = ["Accounts", "Closed Lots", "NET PNL USD", "Total Trades"]
    for column in numeric_group_columns:
        if column in group_summary.columns:
            group_summary[column] = pd.to_numeric(group_summary[column], errors="coerce").fillna(0)
    for column in ["Accounts", "Total Trades"]:
        if column in group_summary.columns:
            group_summary[column] = group_summary[column].round(0).astype(int)

    numeric_deal_columns = ["Login", "Volume", "Profit", "Commission", "Fee", "Swap", "PnL"]
    for column in numeric_deal_columns:
        if column in deal_details.columns:
            deal_details[column] = pd.to_numeric(deal_details[column], errors="coerce").fillna(0)
    if "Login" in deal_details.columns:
        deal_details["Login"] = deal_details["Login"].astype(int)
    if "Volume" in deal_details.columns:
        deal_details["Volume"] = deal_details["Volume"].round(2)
    for column in ["Profit", "Commission", "Fee", "Swap", "PnL"]:
        if column in deal_details.columns:
            deal_details[column] = deal_details[column].round(2)

    total_clients = int(kpis.get("total_clients") or (client_report["Login"].nunique() if not client_report.empty else 0))
    total_pnl = float(kpis.get("total_pnl") or 0.0)
    total_lots = float(kpis.get("total_lots") or 0.0)
    total_trades = int(kpis.get("total_trades") or (client_report["Total Trades"].sum() if "Total Trades" in client_report.columns else 0))
    total_commission = float(kpis.get("total_commission") or 0.0)
    total_fee = float(kpis.get("total_fee") or 0.0)
    total_swap = float(kpis.get("total_swap") or 0.0)
    winning_trades = int(client_report["Winning Trades"].sum()) if "Winning Trades" in client_report.columns else 0
    losing_trades = int(client_report["Losing Trades"].sum()) if "Losing Trades" in client_report.columns else 0
    breakeven_trades = int(client_report["Breakeven Trades"].sum()) if "Breakeven Trades" in client_report.columns else 0

    selected_groups_label = "All groups" if not selected_groups else f"{len(selected_groups)} groups selected"
    summary_rows = [
        ("Report Window", report_label),
        ("Selected Groups", selected_groups_label),
        ("Total Clients", str(total_clients)),
        ("Net Client P&L", f"${total_pnl:,.2f}"),
        ("Closed Lots", f"{total_lots:,.2f}"),
        ("Total Trades", str(total_trades)),
        ("Winning Trades", str(winning_trades)),
        ("Losing Trades", str(losing_trades)),
        ("Breakeven Trades", str(breakeven_trades)),
        ("Commission", f"${total_commission:,.2f}"),
        ("Fee", f"${total_fee:,.2f}"),
        ("Swap", f"${total_swap:,.2f}"),
    ]
    summary_df = pd.DataFrame(summary_rows, columns=["Metric", "Value"])

    top_winners = (
        client_report.sort_values("NET PNL USD", ascending=False, kind="stable").head(10).reset_index(drop=True)
        if not client_report.empty and "NET PNL USD" in client_report.columns
        else client_report.head(0).copy()
    )
    top_losers = (
        client_report.sort_values("NET PNL USD", ascending=True, kind="stable").head(10).reset_index(drop=True)
        if not client_report.empty and "NET PNL USD" in client_report.columns
        else client_report.head(0).copy()
    )

    wb = Workbook()

    summary_ws = wb.active
    summary_ws.title = "Summary"
    _write_sheet_table(summary_ws, summary_df)

    all_pnl_ws = wb.create_sheet("All PnL")
    _write_sheet_table(
        all_pnl_ws,
        client_report,
        money_cols={"Closed Lots", "NET PNL USD", "Commission", "Fee", "Swap"},
        percent_cols={"Win %", "Loss %"},
        int_cols={"Login", "Total Trades", "Winning Trades", "Losing Trades", "Breakeven Trades"},
    )

    winners_ws = wb.create_sheet("Top Winners")
    _write_sheet_table(
        winners_ws,
        top_winners,
        money_cols={"Closed Lots", "NET PNL USD", "Commission", "Fee", "Swap"},
        percent_cols={"Win %", "Loss %"},
        int_cols={"Login", "Total Trades", "Winning Trades", "Losing Trades", "Breakeven Trades"},
    )

    losers_ws = wb.create_sheet("Top Losers")
    _write_sheet_table(
        losers_ws,
        top_losers,
        money_cols={"Closed Lots", "NET PNL USD", "Commission", "Fee", "Swap"},
        percent_cols={"Win %", "Loss %"},
        int_cols={"Login", "Total Trades", "Winning Trades", "Losing Trades", "Breakeven Trades"},
    )

    groups_ws = wb.create_sheet("Groups")
    _write_sheet_table(
        groups_ws,
        group_summary,
        money_cols={"Closed Lots", "NET PNL USD"},
        int_cols={"Accounts", "Total Trades"},
    )

    deals_ws = wb.create_sheet("Trade Details")
    _write_sheet_table(
        deals_ws,
        deal_details,
        money_cols={"Volume", "Profit", "Commission", "Fee", "Swap", "PnL"},
        int_cols={"Login"},
    )

    if warnings:
        warnings_ws = wb.create_sheet("Warnings")
        warnings_df = pd.DataFrame({"Warning": warnings})
        _write_sheet_table(warnings_ws, warnings_df)

    for worksheet in wb.worksheets:
        worksheet.sheet_view.showGridLines = False

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@contextmanager
def manager_session(server: str, login: int, password: str, timeout_ms: int = 30000):
    del server, login, password, timeout_ms
    try:
        manager = get_configured_manager()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Configured MT5 manager unavailable: {exc}") from exc
    with manager.api_lock:
        if manager.api is None:
            raise HTTPException(status_code=503, detail="Configured MT5 manager is reconnecting.")
        yield manager.api


class _LockedManagerProxy:
    """Serialize each SDK call without locking an entire multi-group report."""

    def __init__(self, manager):
        self._manager = manager

    def __getattr__(self, name: str):
        with self._manager.api_lock:
            if self._manager.api is None:
                raise RuntimeError("Configured MT5 manager is reconnecting.")
            attribute = getattr(self._manager.api, name)
            if not callable(attribute):
                return attribute

        def locked_call(*args, **kwargs):
            with self._manager.api_lock:
                if self._manager.api is None:
                    raise RuntimeError("Configured MT5 manager is reconnecting.")
                return getattr(self._manager.api, name)(*args, **kwargs)

        return locked_call


@contextmanager
def yielding_history_manager_session():
    """Share the configured Manager but yield its lock between SDK calls."""
    try:
        manager = get_configured_manager()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Configured MT5 manager unavailable: {exc}") from exc
    if not manager.is_connected:
        raise HTTPException(status_code=503, detail="Configured MT5 manager is reconnecting.")
    yield _LockedManagerProxy(manager)


@contextmanager
def market_manager_session(server: str, login: int, password: str):
    del server, login, password
    with yielding_history_manager_session() as api_handle:
        yield api_handle


@contextmanager
def fast_manager_session(server: str, login: int, password: str):
    """Reuse the persistent Manager connection when available to avoid reconnect latency."""
    try:
        import mt5_connection
        manager = mt5_connection.get_manager()
    except Exception:
        manager = None
    try:
        configured_server, configured_login, _ = configured_manager_credentials()
    except Exception:
        configured_server, configured_login = server, login
    if manager and manager.is_connected and manager.server == configured_server and manager.manager_login == configured_login:
        with manager.api_lock:
            if manager.api is not None:
                yield manager.api
                return
    with manager_session(server, login, password) as api_handle:
        yield api_handle


def _offline_manager_warning(exc: Exception | str | None = None) -> str:
    base = "Configured MT5 manager is offline."
    detail = str(exc or "").strip()
    if detail:
        return f"{base} {detail}"
    return base


def _empty_manager_dashboard(exc: Exception | str | None = None) -> dict[str, Any]:
    try:
        server, login, _ = configured_manager_credentials()
    except Exception:
        server, login = "", 0
    return {
        "status": "disconnected",
        "errors": [_offline_manager_warning(exc)],
        "last_update": None,
        "server_time_msc": None,
        "server_time_source": "unavailable",
        "server": server,
        "manager_login": login,
        "metrics": {},
        "totals": {
            "visible_accounts": 0,
            "enabled_accounts": 0,
            "open_positions": 0,
            "total_balance": 0.0,
            "total_equity": 0.0,
            "total_margin": 0.0,
            "total_free_margin": 0.0,
            "aggregate_margin_level": 0.0,
        },
        "users": [],
        "summary_rows": [],
        "exposure_rows": [],
    }


def _empty_live_risk_snapshot(exc: Exception | str | None = None) -> dict[str, Any]:
    return {
        "generated_at": None,
        "source_updated_at": None,
        "selected_groups": [],
        "summary": {
            "floating_pnl": None,
            "equity": None,
            "margin": None,
            "margin_free": None,
            "open_positions": None,
            "gross_lots": None,
            "flagged_accounts": None,
            "critical_accounts": None,
            "located_ips": None,
            "vps_accounts": None,
        },
        "accounts": [],
        "symbols": [],
        "positions": [],
        "network": {"map_points": [], "providers": [], "countries": [], "shared_clusters": []},
        "warnings": [_manager_connection_error_message(exc)] if exc else [],
        "status": "offline",
    }


def _empty_antifraud_snapshot(exc: Exception | str | None = None) -> dict[str, Any]:
    warning = str(exc).strip() if exc is not None else "MT5 anti-fraud data is unavailable."
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "events": 0,
            "critical": 0,
            "accounts": 0,
            "active_detectors": 0,
            "deals_scanned": 0,
            "positions_scanned": 0,
            "latency_checked": 0,
            "latency_candidates": 0,
        },
        "detectors": [],
        "events": [],
        "unchecked_candidates": [],
        "client_summaries": [],
        "warnings": [warning] if warning else [],
        "status": "offline",
    }


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip()


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)

    @model_validator(mode="after")
    def validate_password_change(self):
        if self.new_password == self.current_password:
            raise ValueError("New password must be different from the current password")
        if self.new_password.lower() in {"admin", "password", "changeme", "123456789012"}:
            raise ValueError("Choose a stronger password")
        return self


class MT5Credentials(BaseModel):
    # Retained as optional compatibility fields for older frontends. The
    # backend intentionally ignores them and only uses its .env identity.
    server: str = ""
    login: int = 0
    password: str = ""


class GroupScopedRequest(MT5Credentials):
    selected_groups: list[str] = Field(default_factory=list)
    excluded_logins: list[int] = Field(default_factory=list, max_length=10000)
    group_filter_rules: str = Field(default="", max_length=20000)
    profile_name: str | None = Field(default=None, max_length=160)

    @field_validator("selected_groups")
    @classmethod
    def normalize_selected_groups(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
        if any(len(value) > 240 for value in cleaned):
            raise ValueError("Group names cannot exceed 240 characters")
        return cleaned

    @field_validator("excluded_logins")
    @classmethod
    def normalize_excluded_logins(cls, values: list[int]) -> list[int]:
        if any(int(value) <= 0 for value in values):
            raise ValueError("Excluded logins must be positive integers")
        return list(dict.fromkeys(int(value) for value in values))


class DealReportRequest(GroupScopedRequest):
    from_date: date
    to_date: date


class BookPerformanceRequest(BaseModel):
    book: Literal["a_book", "b_book", "cent", "hybrid"]
    date_from: date
    date_to: date


class DealExportRequest(DealReportRequest):
    cached_report: dict[str, Any] | None = None


class BrokerageReportRequest(GroupScopedRequest):
    opening_equity_date: date
    closing_equity_date: date
    date_from: date | None = None
    date_to: date | None = None
    clip_negative_equity: bool = False


class BrokerageExportRequest(BrokerageReportRequest):
    cached_report: dict[str, Any] | None = None


class AuditedBrokerageRequest(GroupScopedRequest):
    opening_equity_date: date
    closing_equity_date: date
    date_from: date | None = None
    date_to: date | None = None
    selected_logins: list[int] = Field(default_factory=list, max_length=10000)
    book_mode: Literal["A-Book", "B-Book"] = "B-Book"
    allow_live: bool = False
    force_refresh: bool = False


class AuditedTransactionReviewRequest(BaseModel):
    login: int = Field(gt=0)
    deal: int = Field(gt=0)
    action: int
    expected_profit: float
    category: Literal["Deposit", "Credit In", "Credit Out", "Additional", "Withdrawal", "Equity performance"]
    reason: str = Field(min_length=10, max_length=500)


class AnalyticsRequest(GroupScopedRequest):
    opening_equity_date: date | None = None
    closing_equity_date: date | None = None
    date_from: date | None = None
    date_to: date | None = None
    period: str | None = None
    groups: list[str] = Field(default_factory=list)


class LiveRiskRequest(GroupScopedRequest):
    account_login: int | None = Field(default=None, ge=1)
    symbol: str = Field(default="", max_length=128)
    risk_category: Literal["", "critical", "high", "elevated", "normal"] = ""


class PerformanceRequest(GroupScopedRequest):
    lookback_days: int = Field(default=365, ge=31, le=3650)


class DatabaseResetRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=1, max_length=64)


class ClientPatternRequest(GroupScopedRequest):
    client_login: int
    from_date: date
    to_date: date
    scalping_seconds: int = Field(default=180, ge=1, le=3600)


class MarkoutRequest(GroupScopedRequest):
    client_login: int = Field(gt=0)
    from_date: date
    to_date: date


class ClientLiveRequest(GroupScopedRequest):
    client_login: int


class AntiFraudRequest(GroupScopedRequest):
    refresh_token: int = Field(default=0, ge=0)
    lookback_hours: int = Field(default=24, ge=1, le=168)
    gap_seconds: float = Field(default=3.0, ge=0.1, le=300)
    profit_threshold: float = Field(default=10.0, ge=0)
    check_total_realized_pnl: bool = False
    minimum_total_realized_pnl: float = 0.0
    maximum_duration_minutes: float = Field(default=5.0, ge=0.1, le=1440)
    scalp_seconds: int = Field(default=180, ge=1, le=3600)
    scalp_sequence_min: int = Field(default=3, ge=1, le=1000)
    sharp_profit_threshold: float = Field(default=100.0, ge=0)
    concentration_min_trades: int = Field(default=3, ge=2, le=1000)
    deposit_alert_threshold: float = Field(default=1000.0, gt=0)
    large_order_lots: float = Field(default=1.0, ge=0)
    large_account_lots: float = Field(default=5.0, ge=0)
    large_order_exposure_ratio: float = Field(default=1.0, ge=0)
    large_account_exposure_ratio: float = Field(default=5.0, ge=0)
    mini_equity_threshold: float = 100.0
    stopout_margin_level: float = Field(default=100.0, ge=0)
    news_window_minutes: float = Field(default=5.0, ge=0.1, le=120)
    news_impacts: list[Literal["high", "medium", "low"]] = Field(default_factory=lambda: ["high", "medium", "low"], min_length=1, max_length=3)
    calendar_events: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)
    excluded_symbols: list[str] = Field(default_factory=list)


class RiskEventActionRequest(BaseModel):
    status: Literal["NEW", "ACKNOWLEDGED", "UNDER_REVIEW", "ESCALATED", "RESOLVED", "CLOSED"]
    assigned_to: str | None = Field(default=None, max_length=120)
    note: str = Field(default="", max_length=4000)
    case_id: int | None = Field(default=None, ge=1)


def _antifraud_cache_key(req: AntiFraudRequest) -> str:
    """Key snapshots by the authoritative manager and the complete scan scope."""
    server, login, _ = configured_manager_credentials()
    cache_input = req.model_dump(exclude={"password", "refresh_token", "server", "login"})
    cache_input["configured_manager"] = {"server": server, "login": login}
    cache_input["schema_version"] = 2
    return hashlib.sha256(
        json.dumps(cache_input, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _remember_antifraud_monitor_scope(req: AntiFraudRequest, cache_key: str) -> None:
    """Persist only scan settings and group scope; never persist browser credentials."""
    safe_request = req.model_dump(exclude={"server", "login", "password", "refresh_token"})
    with _conn() as con:
        con.execute("""INSERT INTO antifraud_monitor_scopes(cache_key,request_json,last_scan_at,last_error,updated_at)
                       VALUES (?,?,datetime('now'),'',datetime('now'))
                       ON CONFLICT(cache_key) DO UPDATE SET request_json=excluded.request_json,
                       last_scan_at=excluded.last_scan_at,last_error='',updated_at=excluded.updated_at""", (cache_key, json.dumps(safe_request, sort_keys=True)))


class StressTestRequest(GroupScopedRequest):
    shock_percent: float = Field(default=-5.0, ge=-50.0, le=50.0)
    margin_level_floor: float = Field(default=120.0, ge=0)
    concentration_limit: float = Field(default=70.0, ge=0, le=100)
    symbol_gross_limit: float = Field(default=1_000_000.0, ge=0)


class TelegramTestRequest(BaseModel):
    message: str = "MT5 Brokerage Telegram notifications are connected."
    route: str = Field(default="default", pattern="^(default|vps|scalpers|win_rate|risk|antifraud|spread)$")


class TelegramAlertSettings(BaseModel):
    enabled: bool
    risk_alerts: bool
    antifraud_alerts: bool
    profit_alert_pct: float = Field(ge=0, le=10000)
    double_profit_alert_pct: float = Field(ge=0, le=10000)
    high_lot_threshold: float = Field(gt=0, le=100000)
    account_lot_threshold: float = Field(gt=0, le=100000)
    channel_routes: dict[str, str] = Field(default_factory=dict)

    @field_validator("channel_routes")
    @classmethod
    def normalize_channel_routes(cls, routes: dict[str, str]) -> dict[str, str]:
        allowed = set(TELEGRAM_ROUTE_ENV)
        if any(key not in allowed for key in routes):
            raise ValueError("Unsupported Telegram channel route")
        return {key: str(value).strip()[:120] for key, value in routes.items()}


class GoogleSheetsSyncRequest(BaseModel):
    """A client-visible live snapshot exported by an administrator only."""
    live_snapshot: dict[str, Any] = Field(default_factory=dict)


class BookGroupSettings(BaseModel):
    a_book: list[str] = Field(default_factory=list, max_length=2000)
    b_book: list[str] = Field(default_factory=list, max_length=2000)
    cent: list[str] = Field(default_factory=list, max_length=2000)
    hybrid: list[str] = Field(default_factory=list, max_length=2000)

    @field_validator("a_book", "b_book", "cent", "hybrid")
    @classmethod
    def normalize_groups(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))

    @model_validator(mode="after")
    def groups_belong_to_one_book(self):
        membership: dict[str, str] = {}
        for book, groups in (
            ("A Book", self.a_book),
            ("B Book", self.b_book),
            ("Cent", self.cent),
            ("Hybrid", self.hybrid),
        ):
            for group in groups:
                key = group.casefold()
                previous = membership.get(key)
                if previous:
                    raise ValueError(f"Group '{group}' is already assigned to {previous}")
                membership[key] = book
        return self


class OperationsRequest(GroupScopedRequest):
    pass


class CidAuditRequest(MT5Credentials):
    lookback_hours: int = Field(default=24, ge=1, le=24 * 365 * 30)
    from_date: date | None = None
    to_date: date | None = None
    all_history: bool = False

    @model_validator(mode="after")
    def validate_cid_range(self):
        if (self.from_date is None) != (self.to_date is None):
            raise ValueError("Both from_date and to_date are required for a custom range")
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must not be later than to_date")
        return self


class CaseCreateRequest(BaseModel):
    client_login: int = Field(gt=0)
    title: str = Field(min_length=2, max_length=200)
    assigned_to: str = Field(default="", max_length=120)
    status: str = Field(default="open", pattern="^(open|investigating|monitoring|resolved|closed)$")
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    summary: str = Field(default="", max_length=10000)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=500)

    @field_validator("title", "assigned_to", "summary")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()


class CaseUpdateRequest(BaseModel):
    assigned_to: str | None = Field(default=None, max_length=120)
    status: str | None = Field(default=None, pattern="^(open|investigating|monitoring|resolved|closed)$")
    priority: str | None = Field(default=None, pattern="^(low|medium|high|critical)$")
    summary: str | None = Field(default=None, max_length=10000)
    evidence: list[dict[str, Any]] | None = Field(default=None, max_length=500)
    note: str = Field(default="", max_length=10000)


class ExposureLimitRequest(BaseModel):
    scope_type: str = Field(pattern="^(account|group|symbol)$")
    scope_value: str = Field(min_length=1, max_length=160)
    metric: str = Field(pattern="^(gross_lots|net_lots|floating_loss|gross_exposure|margin_level_below|concentration_pct)$")
    threshold: float = Field(gt=0, le=1_000_000_000_000)
    warning_pct: float = Field(default=80, ge=1, le=100)
    escalation_level: str = Field(default="warning", pattern="^(info|warning|high|critical)$")
    enabled: bool = True

    @field_validator("scope_value")
    @classmethod
    def normalize_scope(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Scope value is required")
        return value

    @model_validator(mode="after")
    def validate_scope_metric(self):
        if self.scope_type == "account" and not self.scope_value.isdigit() and not (self.scope_value == "*" and self.metric in {"net_lots", "gross_lots"}):
            raise ValueError("Account scope must contain a numeric MT5 login")
        if self.scope_type == "symbol" and self.metric not in {"gross_lots", "net_lots", "gross_exposure"}:
            raise ValueError("Symbol limits support gross_lots, net_lots or gross_exposure")
        return self


class AlertAcknowledgeRequest(BaseModel):
    alert_key: str = Field(min_length=3, max_length=300)


class NotificationChannelRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    channel_type: str = Field(pattern="^(telegram|email|webhook)$")
    destination: str = Field(default="", max_length=1000)
    min_severity: str = Field(default="warning", pattern="^(info|warning|high|critical)$")
    enabled: bool = True

    @model_validator(mode="after")
    def validate_destination(self):
        self.name = self.name.strip()
        self.destination = self.destination.strip()
        if self.channel_type == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.destination):
            raise ValueError("A valid email destination is required")
        if self.channel_type == "webhook":
            allowed = ("https://", "http://") if os.getenv("ALLOW_INSECURE_WEBHOOKS", "false").lower() in {"1", "true", "yes"} else ("https://",)
            if not self.destination.startswith(allowed):
                raise ValueError("Webhook destination must use HTTPS")
        if self.channel_type == "telegram" and not self.destination:
            self.destination = "configured-chat"
        return self


class IntelligenceRequest(GroupScopedRequest):
    lookback_days: int = Field(default=90, ge=7, le=730)


class DealerReviewRequest(BaseModel):
    client_login: int = Field(gt=0)
    decision: str = Field(max_length=120)
    notes: str = Field(default="", max_length=10000)


class RiskCondition(BaseModel):
    type: str = Field(default="custom", min_length=2, max_length=80)
    field: str = Field(default="", max_length=120)
    operator: str = Field(pattern="^(gt|gte|lt|lte|equals|not_equals|contains|not_contains|in|not_in)$")
    value: Any
    window_days: int = Field(default=0, ge=0, le=3650)

    @model_validator(mode="after")
    def validate_custom_field(self):
        if self.type == "custom" and not self.field.strip():
            raise ValueError("Custom conditions require a data field")
        return self


class RiskAction(BaseModel):
    type: str = Field(pattern="^(move_group|disable_trading|disable_account|read_only|restrict_symbols|restrict_lot_size|set_leverage|add_label|monitoring_list|manual_review)$")
    value: Any = None
    target_group: str = Field(default="", max_length=240)
    symbols: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_action_configuration(self):
        if self.type in {"restrict_lot_size", "set_leverage"} and _to_float(self.value, 0) <= 0:
            raise ValueError(f"{self.type} requires a positive value")
        if self.type == "add_label" and not str(self.value or "").strip():
            raise ValueError("Risk labels cannot be empty")
        if self.type == "restrict_symbols" and not self.symbols:
            raise ValueError("Select at least one restricted symbol")
        return self


class RiskRuleRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=2000)
    enabled: bool = False
    priority: int = Field(default=100, ge=1, le=10000)
    source_group: str = Field(default="*", min_length=1, max_length=240)
    target_group: str = Field(default="", max_length=240)
    condition_logic: str = Field(default="AND", pattern="^(AND|OR)$")
    conditions: list[RiskCondition] = Field(min_length=1, max_length=20)
    actions: list[RiskAction] = Field(min_length=1, max_length=20)
    alert_events: list[str] = Field(default_factory=lambda: ["triggered", "executed", "failed", "approval_required", "reverted"], max_length=20)
    delay_minutes: int = Field(default=0, ge=0, le=43200)
    require_approval: bool = False
    revert_when_clear: bool = False
    simulation_mode: bool = True
    cooldown_minutes: int = Field(default=60, ge=0, le=525600)
    excluded_logins: list[int] = Field(default_factory=list, max_length=10000)

    @field_validator("excluded_logins")
    @classmethod
    def normalize_excluded_logins(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("Excluded account logins must be positive")
        return sorted(set(values))

    @field_validator("name", "description", "source_group", "target_group")
    @classmethod
    def strip_rule_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_group_action(self):
        move_actions = [action for action in self.actions if action.type == "move_group"]
        if move_actions and not self.target_group and not any(action.target_group for action in move_actions):
            raise ValueError("A target group is required when moving clients")
        allowed_events = {"all", "triggered", "executed", "failed", "approval_required", "approval_rejected", "reverted"}
        if any(event not in allowed_events for event in self.alert_events):
            raise ValueError("Unsupported Telegram alert event")
        return self


class RiskApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    note: str = Field(default="", max_length=4000)


class RiskManualOverride(BaseModel):
    client_login: int = Field(gt=0)
    client_name: str = Field(default="", max_length=160)
    current_group: str = Field(default="", max_length=240)
    actions: list[RiskAction] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=3, max_length=4000)


def _risk_manager_provider():
    import mt5_connection
    return mt5_connection.get_manager()


RISK_AUTOMATION = RiskAutomationService(DB_PATH, _risk_manager_provider, write_audit, send_telegram_notification)
NEWS_PUSH = NewsPushService(_conn, lambda: get_configured_manager())
MARKOUT_REPORTS = MarkoutReportCache()
SPREAD_ALERTS = SpreadAlertService(_conn, send_telegram_notification, lambda: TELEGRAM_ALERTS_ENABLED and bool(
    os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and
    (os.getenv("TELEGRAM_CHAT_ID_SPREAD", "").strip() or os.getenv("TELEGRAM_CHAT_ID", "").strip())))
RISK_AUTOMATION.ensure_schema()


def _clear_runtime_caches(now: float | None = None) -> dict[str, int]:
    """Clear bounded derived caches while preserving live data and alert safety state."""
    current = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    with _PERFORMANCE_CACHE_LOCK:
        performance_entries = len(_PERFORMANCE_CACHE)
        _PERFORMANCE_CACHE.clear()

    with _IP_INTEL_LOCK:
        expired_ips = [key for key, value in _IP_INTEL_CACHE.items() if value[0] <= current]
        for key in expired_ips:
            _IP_INTEL_CACHE.pop(key, None)

    with _TELEGRAM_LOCK:
        expired_alerts = [
            key for key, sent_at in _TELEGRAM_SENT.items()
            if current - sent_at > ALERT_DEDUPE_RETENTION_SECONDS
        ]
        for key in expired_alerts:
            _TELEGRAM_SENT.pop(key, None)

    market_entries = 0
    try:
        import mt5_connection
        engine = mt5_connection.get_market_engine()
        if engine is not None:
            market_entries = engine.clear_transient_cache()
    except Exception:
        logger.exception("Unable to clear the derived Market Watch cache")

    return {
        "performance": performance_entries,
        "market_watch": market_entries,
        "expired_ip": len(expired_ips),
        "expired_alert_dedupe": len(expired_alerts),
    }


def _prune_application_logs(
    directories: list[Path] | None = None,
    *,
    now: float | None = None,
    retention_seconds: float = APP_LOG_RETENTION_SECONDS,
) -> list[Path]:
    """Delete only stale app launcher logs; broker/MT5 journals are never candidates."""
    root = Path(__file__).resolve().parent
    search_dirs = directories or [root, root / "logs"]
    cutoff = (time_module.time() if now is None else float(now)) - float(retention_seconds)
    app_log_name = re.compile(
        r"^(?:(?:risk-(?:backend|frontend)|atomic-backend)\.(?:out|err)\.log|frontend-current\.(?:out|err)\.log)(?:\.\d+)?$",
        re.IGNORECASE,
    )
    deleted: list[Path] = []
    for directory in search_dirs:
        try:
            candidates = list(Path(directory).iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        for candidate in candidates:
            try:
                if not candidate.is_file() or not app_log_name.fullmatch(candidate.name):
                    continue
                if candidate.stat().st_mtime >= cutoff:
                    continue
                candidate.unlink()
                deleted.append(candidate)
            except (FileNotFoundError, PermissionError, OSError):
                # Active Windows log handles can be locked; keep them and retry
                # during the next maintenance window.
                continue
    return deleted


def _prune_mt5_journals(
    directories: list[Path] | None = None,
    *,
    now: float | None = None,
    retention_seconds: float = MT5_JOURNAL_RETENTION_SECONDS,
) -> list[Path]:
    """Retain recent dated MT5 journals and remove only expired generations."""
    root = Path(__file__).resolve().parent
    search_dirs = directories or [root / "logs"]
    cutoff = (time_module.time() if now is None else float(now)) - float(retention_seconds)
    journal_name = re.compile(r"^\d{8}\.log$", re.IGNORECASE)
    deleted: list[Path] = []
    for directory in search_dirs:
        try:
            candidates = list(Path(directory).iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        for candidate in candidates:
            try:
                if not candidate.is_file() or not journal_name.fullmatch(candidate.name):
                    continue
                if candidate.stat().st_mtime >= cutoff:
                    continue
                candidate.unlink()
                deleted.append(candidate)
            except (FileNotFoundError, PermissionError, OSError):
                continue
    return deleted


def _run_runtime_maintenance() -> dict[str, Any]:
    caches = _clear_runtime_caches()
    deleted_logs = [*_prune_application_logs(), *_prune_mt5_journals()]
    try:
        with _conn() as con:
            audit_rows = con.execute(
                "DELETE FROM audit_log WHERE created_at < datetime('now', ?)",
                (f"-{AUDIT_RETENTION_DAYS} days",),
            ).rowcount
            con.commit()
            con.execute("PRAGMA optimize")
            con.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception:
        logger.exception("SQLite runtime optimization failed")
        audit_rows = 0
    if any(caches.values()) or deleted_logs:
        logger.info(
            "Runtime maintenance cleared=%s deleted_logs=%d",
            caches,
            len(deleted_logs),
        )
    return {
        "caches": caches,
        "deleted_logs": [str(path) for path in deleted_logs],
        "deleted_audit_rows": max(int(audit_rows or 0), 0),
    }


def _runtime_maintenance_supervisor() -> None:
    _run_runtime_maintenance()
    while not _RUNTIME_MAINTENANCE_STOP.wait(RUNTIME_MAINTENANCE_INTERVAL_SECONDS):
        try:
            _run_runtime_maintenance()
        except Exception:
            logger.exception("Scheduled runtime maintenance failed")


def _risk_automation_supervisor() -> None:
    interval = max(int(os.getenv("RISK_AUTOMATION_INTERVAL_SECONDS", "15")), 5)
    while not _RUNTIME_MAINTENANCE_STOP.wait(interval):
        try:
            import mt5_connection
            for manager_id, manager in mt5_connection.get_managers().items():
                if not manager.is_connected:
                    continue
                token = mt5_connection.set_selected_manager(manager_id)
                try:
                    RISK_AUTOMATION.run(actor=f"system:{manager_id}")
                finally:
                    mt5_connection.reset_selected_manager(token)
        except Exception as exc:
            if "offline" not in str(exc).lower():
                logger.exception("Automated client risk evaluation failed")


def _cid_observer_supervisor() -> None:
    """Continuously retain only CIDs verified by MT5 online sessions."""
    interval = max(int(os.getenv("CID_OBSERVER_INTERVAL_SECONDS", "5")), 3)
    if _RUNTIME_MAINTENANCE_STOP.wait(3):
        return
    while not _RUNTIME_MAINTENANCE_STOP.is_set():
        try:
            import mt5_connection
            for manager_id, manager in mt5_connection.get_managers().items():
                if not manager.is_connected:
                    continue
                token = mt5_connection.set_selected_manager(manager_id)
                try:
                    _capture_online_cid_sessions()
                finally:
                    mt5_connection.reset_selected_manager(token)
        except Exception as exc:
            if "offline" not in str(exc).lower():
                logger.warning("CID observer refresh failed: %s", exc)
        if _RUNTIME_MAINTENANCE_STOP.wait(interval):
            return


def _book_for_group(group: str, settings: dict[str, Any]) -> str:
    """Return the configured execution book for an exact MT5 group name."""
    for key in ("a_book", "b_book", "cent", "hybrid"):
        if group in settings.get(key, []):
            return key
    return "unassigned"


def _capture_book_shifts(snapshot=None, *, manager_login: int | None = None) -> int:
    """Persist group changes with equity from the same atomic Manager snapshot."""
    if snapshot is None:
        import mt5_connection
        manager = mt5_connection.get_manager()
        if not manager or not manager.is_connected:
            return 0
        snapshot = manager.snapshot()
        manager_login = manager.manager_login
    if snapshot.last_update <= 0 or not snapshot.users:
        return 0
    settings = get_book_group_settings()
    captured = 0
    with _conn() as con:
        logins = list(dict.fromkeys(_to_int(user.get("Login"),0) for user in snapshot.users))
        if len(logins) > 5000:
            prior = {int(row["login"]):dict(row) for row in con.execute("SELECT * FROM book_membership_state")}
        else:
            prior = {}
            for offset in range(0,len(logins),500):
                batch = logins[offset:offset+500]
                placeholders = ",".join("?" for _ in batch)
                prior.update({int(row["login"]):dict(row) for row in con.execute(
                    "SELECT * FROM book_membership_state WHERE login IN ("+placeholders+")",batch)})
        pending_writes = 0
        for user in snapshot.users:
            login = _to_int(user.get("Login"), 0)
            group = str(user.get("Group", "") or "").strip()
            equity_value = user.get("Equity")
            if login <= 0 or not group or equity_value is None:
                continue
            equity = _to_float(equity_value, 0.0)
            floating_pnl = _to_float(
                user.get("Live Floating PnL", user.get("Floating PnL", 0.0)), 0.0
            )
            book = _book_for_group(group, settings)
            previous = prior.get(login)
            if previous and (str(previous["client_name"] or ""),previous["account_group"],previous["book"],
                             previous["equity"],previous["floating_pnl"]) == (
                             str(user.get("Name", "") or ""),group,book,equity,floating_pnl):
                continue
            if previous and previous["account_group"] != group:
                con.execute(
                    """INSERT OR IGNORE INTO book_shift_events
                       (manager_login,login,client_name,from_group,to_group,
                        from_book,to_book,equity_at_shift,floating_pnl_at_shift,
                        source_updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (manager_login or 0, login, str(user.get("Name", "") or ""),
                     previous["account_group"], group, previous["book"], book,
                     equity, floating_pnl, float(snapshot.last_update)),
                )
                captured += int(con.execute("SELECT changes()").fetchone()[0])
                if con.execute("SELECT changes()").fetchone()[0]:
                    logger.info(
                        "Book shift captured manager=%s login=%s group=%s->%s book=%s->%s equity=%.2f generation=%.3f",
                        manager_login or 0, login, previous["account_group"], group,
                        previous["book"], book, equity, float(snapshot.last_update),
                    )
            con.execute(
                """INSERT INTO book_membership_state
                   (login,client_name,account_group,book,equity,floating_pnl,
                    source_updated_at,observed_at)
                   VALUES (?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(login) DO UPDATE SET client_name=excluded.client_name,
                   account_group=excluded.account_group,book=excluded.book,
                   equity=excluded.equity,floating_pnl=excluded.floating_pnl,
                   source_updated_at=excluded.source_updated_at,
                   observed_at=datetime('now')""",
                (login, str(user.get("Name", "") or ""), group, book, equity, floating_pnl,
                 float(snapshot.last_update)),
            )
            pending_writes += 1
            # A full Manager snapshot can contain thousands of accounts. Release
            # the SQLite writer lock between batches so other live workers can
            # publish their own short transactions.
            if pending_writes >= 250:
                con.commit()
                pending_writes = 0
    return captured


def _book_shift_observer_supervisor() -> None:
    interval = max(float(os.getenv("BOOK_SHIFT_OBSERVER_SECONDS", "1")), 0.5)
    if _RUNTIME_MAINTENANCE_STOP.wait(3):
        return
    last_generations: dict[str, float] = {}
    native_cursors: dict[str, NativeMembershipCursor] = {}
    while not _RUNTIME_MAINTENANCE_STOP.is_set():
        try:
            import mt5_connection
            for manager_id, manager in mt5_connection.get_managers().items():
                if not manager.is_connected:
                    continue
                if getattr(manager, "native_mode", False):
                    token = mt5_connection.set_selected_manager(manager_id)
                    try:
                        cursor = native_cursors.setdefault(manager_id,NativeMembershipCursor())
                        batch = cursor.prepare(manager,get_book_group_settings())
                        if batch is not None:
                            if batch.snapshot.users:
                                _capture_book_shifts(batch.snapshot,manager_login=manager.manager_login)
                            cursor.commit(batch)
                            batch = None
                    finally:
                        mt5_connection.reset_selected_manager(token)
                    continue
                snapshot = manager.snapshot()
                if snapshot.last_update > last_generations.get(manager_id, 0.0):
                    _capture_book_shifts(snapshot, manager_login=manager.manager_login)
                    last_generations[manager_id] = snapshot.last_update
        except Exception as exc:
            if "offline" not in str(exc).lower():
                logger.warning("Book-shift observer failed: %s", exc)
        if _RUNTIME_MAINTENANCE_STOP.wait(interval):
            return


def _bbook_snapshot_values(snapshot, groups: list[str]) -> tuple[float, int]:
    """Return floating P&L and account count from one atomic cache generation."""
    selected = set(groups)
    users = [user for user in snapshot.users if str(user.get("Group", "")) in selected]
    floating = sum(
        _to_float(user.get("Live Floating PnL", user.get("Floating PnL", 0.0)), 0.0)
        for user in users
    )
    return round(floating, 2), len(users)


def _book_money_factor(book: str) -> float:
    """Normalize MT5 cent-account money (USC) into dashboard USD."""
    return 1.0 / CENT_ACCOUNT_DIVISOR if book == "cent" else 1.0


def _book_broker_pnl(book: str, client_pnl: float, swaps: float, commissions: float) -> float | None:
    """Return supported broker perspective; hybrid and cent need explicit routing policy."""
    if book == "b_book":
        return round(-client_pnl, 2)
    if book == "a_book":
        return round(-(swaps + commissions), 2)
    return None


def _book_live_values(snapshot, groups: list[str], book: str) -> dict[str, float | int]:
    selected = set(groups)
    users = [user for user in snapshot.users if str(user.get("Group", "")) in selected]
    factor = _book_money_factor(book)
    floating = sum(
        _to_float(user.get("Live Floating PnL", user.get("Floating PnL", 0.0)), 0.0)
        for user in users
    ) * factor
    balance = sum(_to_float(user.get("Balance"), 0.0) for user in users) * factor
    equity = sum(_to_float(user.get("Equity"), 0.0) for user in users) * factor
    return {
        "floating": round(floating, 2),
        "balance": round(balance, 2),
        "equity": round(equity, 2),
        "account_count": len(users),
    }


def _capture_book_pnl_snapshots() -> int:
    """Persist A-Book and B-Book summaries; history is fetched by this worker only."""
    import mt5_connection

    manager = mt5_connection.get_manager()
    if not manager or not manager.is_connected:
        return 0
    snapshot = manager.snapshot()
    if snapshot.last_update <= 0:
        return 0
    settings = get_book_group_settings()
    books = {
        book: list(settings.get(book) or [])
        for book in ("a_book", "b_book", "cent", "hybrid")
    }
    now = datetime.now(timezone.utc)
    day_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    warnings: list[str] = []
    incomplete_books: set[str] = set()
    deal_totals = {
        book: defaultdict(float) for book in ("a_book", "b_book", "cent", "hybrid")
    }
    try:
        with yielding_history_manager_session() as api_handle:
            for book, groups in books.items():
                factor = _book_money_factor(book)
                for group in _book_history_request_groups(groups):
                    deals, error = request_deals_by_group(
                        api_handle, group, int(day_start.timestamp()), int(now.timestamp())
                    )
                    if error:
                        warnings.append(f"{group}: {error}")
                        incomplete_books.add(book)
                        continue
                    for deal in deals:
                        action = _to_int(_value(deal, "Action", "action"), None)
                        amount = _to_float(_value(deal, "Profit", "profit"), 0.0) * factor
                        if action == DEAL_BALANCE:
                            if amount >= 0:
                                deal_totals[book]["deposits"] += amount
                            else:
                                deal_totals[book]["withdrawals"] += abs(amount)
                            continue
                        volume = _deal_volume_units(deal)
                        if not _is_trade_deal(deal, action, volume):
                            continue
                        entry = _to_int(_value(deal, "Entry", "entry"), None)
                        entry_text = str(
                            _value(deal, "Entry", "entry", "EntryType", "entry_type", default="") or ""
                        ).lower()
                        is_close = entry in CLOSED_ENTRIES or entry_text in {
                            "out", "out_by", "inout", "entry_out", "entry_out_by",
                            "entry_inout", "close", "closed",
                        }
                        if is_close:
                            deal_totals[book]["closed_profit"] += amount
                            deal_totals[book]["swaps"] += _to_float(
                                _value(deal, "Storage", "storage", "Swap", "swap"), 0.0
                            ) * factor
                        deal_totals[book]["commissions"] += (
                            _to_float(_value(deal, "Commission", "commission"), 0.0)
                            + _to_float(_value(deal, "Fee", "fee"), 0.0)
                        ) * factor
    except Exception as exc:
        incomplete_books.update(book for book, groups in books.items() if groups)
        logger.warning("B-Book history refresh failed; retaining live floating snapshot: %s", exc)

    stored = 0
    with _conn() as con:
        for book, groups in books.items():
            if not groups:
                continue
            if book in incomplete_books:
                continue
            live = _book_live_values(snapshot, groups, book)
            metrics = deal_totals[book]
            closed_swaps_comm = (
                metrics["closed_profit"] + metrics["swaps"] + metrics["commissions"]
            )
            con.execute(
                """INSERT INTO book_pnl_snapshots
                   (book,captured_at,source_updated_at,floating,balance,equity,
                    deposits,withdrawals,closed_profit,swaps,commissions,
                    closed_swaps_comm,ib_payout,account_count,groups_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(book,source_updated_at) DO UPDATE SET
                    floating=excluded.floating,balance=excluded.balance,equity=excluded.equity,
                    deposits=excluded.deposits,withdrawals=excluded.withdrawals,
                    closed_profit=excluded.closed_profit,swaps=excluded.swaps,
                    commissions=excluded.commissions,
                    closed_swaps_comm=excluded.closed_swaps_comm,
                    account_count=excluded.account_count,groups_json=excluded.groups_json""",
                (
                    book, now.isoformat(), float(snapshot.last_update), live["floating"],
                    live["balance"], live["equity"], round(metrics["deposits"], 2),
                    round(metrics["withdrawals"], 2), round(metrics["closed_profit"], 2),
                    round(metrics["swaps"], 2), round(metrics["commissions"], 2),
                    round(closed_swaps_comm, 2), 0.0, live["account_count"], json.dumps(groups),
                ),
            )
            stored += int(con.execute("SELECT changes()").fetchone()[0])
    if warnings:
        logger.warning("B-Book snapshot history warnings: %s", "; ".join(warnings[:5]))
    return stored


def _capture_bbook_pnl_snapshot() -> bool:
    """Compatibility wrapper for callers from the initial B-Book release."""
    return _capture_book_pnl_snapshots() > 0


def _bbook_pnl_observer_supervisor() -> None:
    if _RUNTIME_MAINTENANCE_STOP.wait(5):
        return
    while not _RUNTIME_MAINTENANCE_STOP.is_set():
        captured = 0
        try:
            captured = _capture_book_pnl_snapshots()
        except Exception:
            logger.exception("B-Book P&L snapshot refresh failed")
        wait_seconds = BBOOK_SNAPSHOT_INTERVAL_SECONDS if captured else 5
        if _RUNTIME_MAINTENANCE_STOP.wait(wait_seconds):
            return


def _risk_admin_actor(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication is required")
    if not bool(user.get("is_admin")):
        raise HTTPException(status_code=403, detail="Administrator permission is required")
    return str(user["username"])


def _operations_admin_actor(request: Request) -> str:
    """Use the authenticated administrator as the audit actor."""
    return _risk_admin_actor(request)


def _runtime_loop_exception_handler(loop, context):
    """Windows reports a peer closing a socket again during pipe cleanup."""
    exc = context.get("exception")
    message = str(context.get("message", ""))
    if (isinstance(exc, ConnectionResetError) and getattr(exc, "winerror", None) == 10054
            and "_ProactorBasePipeTransport._call_connection_lost" in message):
        logger.debug("Client disconnected during Windows transport cleanup")
        return
    previous = getattr(loop, "_rms_previous_exception_handler", None)
    if previous:
        previous(loop, context)
    else:
        loop.default_exception_handler(context)


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    loop = asyncio.get_running_loop()
    previous_exception_handler = loop.get_exception_handler()
    loop._rms_previous_exception_handler = previous_exception_handler
    loop.set_exception_handler(_runtime_loop_exception_handler)
    _RUNTIME_MAINTENANCE_STOP.clear()
    SPREAD_ALERTS.recover_interrupted()
    maintenance_thread = threading.Thread(
        target=_runtime_maintenance_supervisor,
        name="runtime-maintenance-supervisor",
        daemon=True,
    )
    background_threads = [maintenance_thread]
    maintenance_thread.start()
    if os.getenv("HISTORY_WORKER_ENABLED", os.getenv("MT5_MANAGER_AUTOCONNECT", "true")).lower() in {"1", "true", "yes", "on"}:
        history_thread = threading.Thread(target=_HISTORY_WORKER.run, name="historical-sync", daemon=True)
        background_threads.append(history_thread)
        history_thread.start()
    if os.getenv("MT5_MANAGER_AUTOCONNECT", "true").strip().lower() in {"1", "true", "yes", "on"}:
        manager_thread = threading.Thread(
            target=_manager_connection_supervisor,
            name="mt5-manager-supervisor",
            daemon=True,
        )
        reconciliation_thread = threading.Thread(
            target=_daily_operations_supervisor,
            name="daily-reconciliation-supervisor",
            daemon=True,
        )
        automation_thread = threading.Thread(
            target=_risk_automation_supervisor,
            name="risk-automation-supervisor",
            daemon=True,
        )
        cid_thread = threading.Thread(
            target=_cid_observer_supervisor,
            name="cid-observer-supervisor",
            daemon=True,
        )
        book_shift_thread = threading.Thread(
            target=_book_shift_observer_supervisor,
            name="book-shift-observer",
            daemon=True,
        )
        bbook_pnl_thread = threading.Thread(
            target=_bbook_pnl_observer_supervisor,
            name="bbook-pnl-observer",
            daemon=True,
        )
        google_sheets_thread = threading.Thread(
            target=_google_sheets_auto_sync_supervisor,
            name="google-sheets-auto-sync",
            daemon=True,
        )
        antifraud_thread = threading.Thread(
            target=_antifraud_background_supervisor,
            name="antifraud-background-scan",
            daemon=True,
        )
        push_thread = threading.Thread(target=_news_push_supervisor, name="news-push-delivery", daemon=True)
        spread_thread = threading.Thread(target=_spread_alert_supervisor, name="spread-alert-monitor", daemon=True)
        spread_delivery = threading.Thread(target=_spread_delivery_supervisor, name="spread-alert-delivery", daemon=True)
        background_threads.extend([
            manager_thread, reconciliation_thread, automation_thread, cid_thread,
            book_shift_thread, bbook_pnl_thread, google_sheets_thread, antifraud_thread, push_thread, spread_thread, spread_delivery,
        ])
        for thread in background_threads[1:]:
            if thread.ident is None:
                thread.start()
    else:
        logger.warning("MT5 manager auto-connect is disabled for this process.")
    try:
        yield
    finally:
        _RUNTIME_MAINTENANCE_STOP.set()
        loop.set_exception_handler(previous_exception_handler)
        try:
            import mt5_connection
            mt5_connection.stop_manager()
        except Exception:
            logger.exception("Unable to stop the MT5 Manager cleanly")
        for thread in background_threads:
            thread.join(timeout=5.0)


_API_DOCS_ENABLED = os.getenv("ENABLE_API_DOCS", "false").strip().lower() in {"1", "true", "yes", "on"}
api = FastAPI(
    title="MT5 Reporting API",
    version="2.0.0",
    lifespan=app_lifespan,
    docs_url="/docs" if _API_DOCS_ENABLED else None,
    redoc_url="/redoc" if _API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _API_DOCS_ENABLED else None,
)

from services.live_routes import LiveReadRoute
api.router.route_class = LiveReadRoute

@api.exception_handler(Exception)
async def global_exception_handler(request, exc):
    error_id = hashlib.sha256(f"{time_module.time_ns()}:{request.url.path}".encode()).hexdigest()[:12]
    logger.error("Unhandled request error id=%s path=%s: %s\n%s", error_id, request.url.path, exc, traceback.format_exc())
    return Response(
        content=json.dumps({"detail": "Internal server error", "error_id": error_id}),
        media_type="application/json",
        status_code=500,
        headers={"X-Error-ID": error_id},
    )

allowed_origins = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "").split(",") if origin.strip()]
api.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials="*" not in allowed_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-MT5-Manager-ID"],
)
api.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=4)
init_db()


@api.middleware("http")
async def select_manager_for_request(request: Request, call_next):
    """Use the one configured Manager for every request."""
    import mt5_connection
    token = mt5_connection.set_selected_manager("default")
    try:
        response = await call_next(request)
        return response
    finally:
        mt5_connection.reset_selected_manager(token)


@api.middleware("http")
async def authenticate_api_requests(request: Request, call_next):
    path = request.url.path
    public_paths = {"/api/health", "/api/ready", "/api/auth/login"}
    if path.startswith("/api/") and path not in public_paths and request.method != "OPTIONS":
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        token = token or request.cookies.get(SESSION_COOKIE_NAME, "")
        user = await asyncio.to_thread(_verify_session_token, token) if scheme.lower() == "bearer" and token else None
        if not user and not authorization and token:
            user = await asyncio.to_thread(_verify_session_token, token)
        if not user:
            return Response(
                content=json.dumps({"detail": "Authentication is required"}),
                media_type="application/json",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.state.user = user
        password_change_paths = {"/api/auth/session", "/api/auth/change-password", "/api/auth/logout"}
        if bool(user.get("must_change_pw")) and path not in password_change_paths:
            return JSONResponse(
                {"detail": "Password change is required", "code": "PASSWORD_CHANGE_REQUIRED"},
                status_code=403,
            )
        admin_only_prefixes = (
            "/api/operations/",
            "/api/risk-automation/",
            "/api/notifications/telegram/",
            "/api/notifications/news-push",
            "/api/notifications/spread/",
            "/api/integrations/",
        )
        if path.startswith(admin_only_prefixes):
            if not bool(user.get("is_admin")):
                return Response(
                    content=json.dumps({"detail": "Administrator permission is required"}),
                    media_type="application/json",
                    status_code=403,
                )
    response = await call_next(request)
    if path.startswith("/api/") and path not in public_paths and path != "/api/auth/logout":
        user = getattr(request.state, "user", None)
        if user:
            _set_session_cookie(response, _create_session_token(user))
    return response


@api.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; "
        "img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' https://static.cloudflareinsights.com; "
        "connect-src 'self' ws: wss: https://cloudflareinsights.com",
    )
    if SESSION_COOKIE_SECURE and request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@api.middleware("http")
async def reject_oversized_requests(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return JSONResponse({"detail": "Request body is too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length header"}, status_code=400)
    return await call_next(request)


@api.middleware("http")
async def audit_sensitive_http_actions(request: Request, call_next):
    response = await call_next(request)
    watched = {
        "/api/reports/deal/export": "report.deal_exported",
        "/api/reports/brokerage/export": "report.brokerage_exported",
        "/api/notifications/telegram/settings": "settings.telegram_updated",
        "/api/notifications/telegram/test": "notification.telegram_tested",
    }
    action = watched.get(request.url.path)
    if action and response.status_code < 400:
        user = getattr(request.state, "user", None)
        await asyncio.to_thread(write_audit, user["username"] if user else "system", action, "http", request.url.path)
    return response


def _daily_operations_supervisor() -> None:
    last_date = ""
    while not _RUNTIME_MAINTENANCE_STOP.is_set():
        try:
            current = datetime.now(timezone.utc).date().isoformat()
            if current != last_date:
                result = _store_daily_reconciliation()
                if result.get("status") == "complete":
                    last_date = current
        except Exception:
            logger.exception("Automated daily reconciliation failed")
        if _RUNTIME_MAINTENANCE_STOP.wait(300):
            return


def _resolve_groups_for_request(api_handle, selected_groups: list[str], group_filter_rules: str):
    normalized_selected = [
        str(group).strip()
        for group in (selected_groups or [])
        if str(group).strip()
    ]
    normalized_rules = str(group_filter_rules or "").strip()
    if not normalized_selected and not normalized_rules:
        return [], [], []
    if normalized_selected and not normalized_rules:
        return normalized_selected, normalized_selected, []

    all_groups, err = request_groups(api_handle)
    warnings = [err] if err else []
    groups = apply_group_filters(all_groups, selected_groups, group_filter_rules)
    if not groups:
        warnings.append("No groups matched the current selection.")
    return groups, all_groups, warnings


def _resolve_groups_from_available(available_groups: list[str], selected_groups: list[str], group_filter_rules: str):
    normalized_selected = [
        str(group).strip()
        for group in (selected_groups or [])
        if str(group).strip()
    ]
    normalized_rules = str(group_filter_rules or "").strip()
    if not normalized_selected and not normalized_rules:
        return [], available_groups, []
    if normalized_selected and not normalized_rules:
        available = set(available_groups)
        groups = [group for group in normalized_selected if group in available]
        missing = [group for group in normalized_selected if group not in available]
        warnings = [f"{len(missing)} selected group(s) are not present in the live manager cache."] if missing else []
        if not groups:
            warnings.append("No groups matched the current selection.")
        return groups, available_groups, warnings

    groups = apply_group_filters(available_groups, selected_groups, group_filter_rules)
    warnings = []
    if not groups:
        warnings.append("No groups matched the current selection.")
    return groups, available_groups, warnings


def _compress_complete_group_branches(
    groups: list[str], available_groups: list[str]
) -> list[str]:
    """Collapse fully selected tree branches into exact MT5 wildcard masks."""
    selected = {str(group).strip() for group in groups if str(group).strip()}
    available = {str(group).strip() for group in available_groups if str(group).strip()}
    remaining = set(selected)
    requests: list[str] = []
    prefixes = {
        "\\".join(group.split("\\")[:depth])
        for group in selected
        for depth in range(1, len(group.split("\\")))
    }
    for prefix in sorted(prefixes, key=lambda value: (value.count("\\"), value.lower(), value)):
        descendants = {group for group in available if group.startswith(f"{prefix}\\")}
        if len(descendants) < 2 or not descendants.issubset(selected):
            continue
        if not descendants.intersection(remaining):
            continue
        requests.append(f"{prefix}\\*")
        remaining.difference_update(descendants)
    requests.extend(sorted(remaining, key=lambda value: (value.lower(), value)))
    return requests


def _build_deal_report_payload(api_handle, req: DealReportRequest) -> dict[str, Any]:
    groups, _, group_warnings = _resolve_groups_for_request(
        api_handle, req.selected_groups, req.group_filter_rules
    )
    if not groups:
        return {
            "selected_groups": [],
            "kpis": compute_kpis(pd.DataFrame()),
            "deals": [],
            "client_report": [],
            "groups": [],
            "warnings": group_warnings,
        }

    deals_df, client_report, group_summary, kpis, warnings = build_deal_report(
        api_handle, groups, req.from_date, req.to_date, req.excluded_logins
    )

    return {
        "selected_groups": groups,
        "kpis": kpis,
        "deals": _dataframe_to_records(deals_df),
        "client_report": _dataframe_to_records(client_report),
        "groups": _dataframe_to_records(group_summary),
        "warnings": _dedupe_warnings(group_warnings, warnings),
    }


@api.get("/api/health")
def health():
    return {"status": "ok", "service": "mt5-reporting", "version": api.version}


@api.get("/api/ready")
def readiness():
    import mt5_connection
    try:
        with _conn() as con:
            con.execute("SELECT 1").fetchone()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database is unavailable: {exc}") from exc
    manager = mt5_connection.get_manager()
    if not manager or not manager.is_connected:
        raise HTTPException(status_code=503, detail="MT5 Manager is not connected")
    with manager.cache_lock:
        cache_status = str(manager.cache.get("status", "disconnected"))
        last_update = float(manager.cache.get("last_update", 0.0))
        cache_errors = bool(manager.cache.get("errors"))
    if getattr(manager, "native_mode", False) and (
        cache_status != "connected" or cache_errors or not math.isfinite(last_update)
        or last_update <= 0 or time_module.time() - last_update > 10
        or last_update > time_module.time() + 1
    ):
        raise HTTPException(status_code=503, detail="Native MT5 feed is synchronizing or account data is stale")
    focused_mode = not mt5_connection.FULL_SNAPSHOT_ENABLED
    if focused_mode and not manager.group_cache_view():
        raise HTTPException(status_code=503, detail="MT5 Manager group catalog is initializing")
    if not focused_mode and (cache_status != "connected" or last_update <= 0):
        raise HTTPException(status_code=503, detail="MT5 Manager cache has not published a complete generation")
    return {
        "status": "ready",
        "manager": "connected",
        "cache": "focused" if focused_mode else cache_status,
        "source_updated_at": last_update or None,
        "source": "mt5-cpp-native" if getattr(manager, "native_mode", False) else "mt5-manager",
        "market_watch": manager.market_cache_status(),
    }


class ManagerConnectRequest(BaseModel):
    server: str
    login: int
    password: str

def _get_manager_from_request(request: Request):
    del request
    try:
        return get_configured_manager()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Configured MT5 manager unavailable: {exc}") from exc


@api.post("/api/manager/connect")
def manager_connect(req: ManagerConnectRequest):
    del req
    try:
        manager = get_configured_manager()
        return {"status": "connected", "server": manager.server, "login": manager.manager_login}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@api.post("/api/manager/disconnect")
def manager_disconnect():
    manager = get_configured_manager()
    return {
        "status": "connected",
        "server": manager.server,
        "login": manager.manager_login,
        "detail": "Persistent manager mode cannot be disconnected through the API.",
    }


@api.get("/api/manager/status")
def manager_status():
    import mt5_connection
    mgr = mt5_connection.get_manager()
    if mgr and mgr.is_connected:
        last_update = mgr.update_marker()
        if getattr(mgr, "native_mode", False):
            with mgr.cache_lock:
                ready = mgr.cache.get("status") == "connected" and not mgr.cache.get("errors") and last_update > 0 and time_module.time() - last_update <= 10
            if not ready:
                source_connected = bool(getattr(getattr(mgr, "native_bridge", None), "available", False))
                return {"status": "warming", "server": mgr.server, "login": mgr.manager_login,
                        "last_update": last_update, "data_age_seconds": None,
                        "source_connected": source_connected,
                        "error": ("MT5 stream connected; waiting for current account observations" if source_connected else
                                  "MT5 synchronizing; waiting for current account data")}
        return {
            "status": "connected",
            "server": mgr.server,
            "login": mgr.manager_login,
            "last_update": last_update,
            "data_age_seconds": round(max(0.0, time_module.time() - last_update), 2) if last_update else None,
            "error": None,
        }
    try:
        server, login, _ = configured_manager_credentials()
    except Exception:
        server, login = "", 0
    return {
        "status": "disconnected",
        "server": server,
        "login": login,
        "last_update": 0.0,
        "data_age_seconds": None,
        "error": _MANAGER_CONNECTION_ERROR or "MT5 Manager connection is initializing.",
    }


@api.get("/api/managers")
def list_managers():
    """Expose safe connection metadata only; passwords never leave the server."""
    import mt5_connection
    configs = configured_manager_configs()
    managers = []
    for manager_id, config in configs.items():
        manager = mt5_connection.get_manager(manager_id)
        managers.append({
            "id": manager_id,
            "name": config["name"],
            "server": config["server"],
            "login": config["login"],
            "status": "connected" if manager and manager.is_connected else "disconnected",
            "error": _MANAGER_CONNECTION_ERRORS.get(manager_id),
            "last_update": manager.snapshot().last_update if manager and manager.is_connected else 0,
        })
    return {"managers": managers}


@api.get("/api/realtime/status")
def realtime_source_status():
    if not os.getenv("RMS_NATIVE_URL"):
        return {"source": "mt5-manager-polling", "ready": False, "native_enabled": False}
    from urllib.request import Request as NativeRequest, urlopen
    try:
        request = NativeRequest(os.environ["RMS_NATIVE_URL"].rstrip("/") + "/internal/native/health",
            headers={"Authorization": "Bearer " + os.environ["RMS_NATIVE_TOKEN"]})
        with urlopen(request, timeout=2) as response:
            payload = json.load(response)
        import mt5_connection
        manager = mt5_connection.get_manager()
        projected = False
        if manager is not None and getattr(manager, "native_mode", False):
            with manager.cache_lock:
                projected = (manager.cache.get("status") == "connected" and not manager.cache.get("errors")
                             and manager.cache.get("native_epoch") == payload.get("epoch")
                             and time_module.time() - manager.cache.get("native_received_at", 0) < 10)
        return {**payload, "native_enabled": True, "gateway_ready": bool(payload.get("ready")),
                "projection_ready": projected, "ready": bool(payload.get("ready") and projected)}
    except (OSError, ValueError, KeyError):
        return {"source": "mt5-cpp-native", "ready": False, "native_enabled": True, "connected": False}


@api.get("/api/manager/dashboard")
def manager_dashboard(request: Request):
    try:
        mgr = _get_manager_from_request(request)
    except HTTPException as exc:
        return _empty_manager_dashboard(exc.detail)
    from mt5_connection import coerce_float, coerce_int

    snapshot = mgr.dashboard_cache_view()
    if getattr(mgr, "native_mode", False) and (snapshot["status"] != "connected" or snapshot["errors"]):
        return _empty_manager_dashboard("Native MT5 source is unavailable or resynchronizing")
    group_currencies = snapshot.get("group_currencies", {})
    users = [
        {
            "login": coerce_int(row.get("Login")),
            "name": str(row.get("Name", "")),
            "group": str(row.get("Group", "")),
            "currency": str(group_currencies.get(str(row.get("Group", ""))) or row.get("Currency") or "UNKNOWN"),
            "country": str(row.get("Country", "")),
            "email": str(row.get("Email", "")),
            "enabled": bool(row.get("Enabled", False)),
            "leverage": coerce_int(row.get("Leverage")),
            "balance": coerce_float(row.get("Balance")),
            "credit": coerce_float(row.get("Credit")),
            "equity": coerce_float(row.get("Equity")),
            "margin": coerce_float(row.get("Margin")),
            "free_margin": coerce_float(row.get("Free Margin")),
            "margin_level": coerce_float(row.get("Margin Level %")),
            "floating_pnl": coerce_float(row.get("Live Floating PnL", row.get("Floating PnL"))),
            "swap": coerce_float(row.get("Swap")),
        }
        for row in snapshot["users"]
    ]

    totals = {
        "visible_accounts": len(users),
        "enabled_accounts": sum(1 for row in users if row["enabled"]),
        "open_positions": snapshot["positions_count"],
    }
    # Deposits in USD, USC and other currencies are different units. Keep all
    # account counts, but never expose their raw sum as a dollar portfolio.
    buckets = snapshot.get("native_money_totals")
    if buckets is not None and sum(bucket["accounts"] for bucket in buckets.values()) == len(users):
        from services.native_money import portfolio_summary
        money_totals = portfolio_summary(buckets, {group for group, _ in buckets}, group_currencies)["currency_totals"]
    else:
        money_totals = currency_money_totals((dict(row, Currency=group_currencies.get(str(row.get("Group", ""))) or row.get("Currency") or "UNKNOWN")
                                             for row in snapshot["users"]), native=True)
    totals["currency_totals"] = money_totals
    totals["currencies"] = sorted(money_totals)
    single = next(iter(money_totals.values())) if len(money_totals) == 1 and "UNKNOWN" not in money_totals else {}
    for output, field in (("total_balance", "balance"), ("total_equity", "equity"), ("total_margin", "margin"), ("total_free_margin", "margin_free")):
        totals[output] = single.get(field)
    totals["aggregate_margin_level"] = (round(totals["total_equity"] / totals["total_margin"] * 100, 2)
        if totals["total_equity"] is not None and totals["total_margin"] is not None and totals["total_margin"] > 0 else None)

    return {
        # In focused mode no account-wide generation is published by design.
        # The persistent SDK connection itself is the authoritative live state.
        "status": (
            "connected" if mgr.is_connected and snapshot["last_update"] > 0
            else "warming" if mgr.is_connected else snapshot["status"]
        ),
        "errors": list(snapshot["errors"] or []),
        "refresh_phase": snapshot.get("refresh_phase", "unknown"),
        "last_update": snapshot["last_update"],
        "data_age_seconds": round(max(0.0, time_module.time() - snapshot["last_update"]), 2) if snapshot["last_update"] else None,
        "server_time_msc": snapshot.get("server_time_msc") or None,
        "server_time_checked_at": snapshot.get("server_time_checked_at"),
        "server_time_source": "manager_time_pump" if snapshot.get("server_time_msc") else "unavailable",
        "server": mgr.server,
        "manager_login": mgr.manager_login,
        "metrics": snapshot["metrics"],
        "totals": totals,
        "users": users,
        "summary_rows": snapshot["summary_rows"],
        "exposure_rows": snapshot["exposure_rows"],
    }


@api.get("/api/manager/market-watch")
def manager_market_watch(
    request: Request,
    q: str = Query("", max_length=120),
    limit: int = Query(250, ge=1, le=1000),
):
    try:
        mgr = _get_manager_from_request(request)
    except HTTPException as exc:
        return {
            "status": "disconnected",
            "server_time_msc": None,
            "server_time_source": "unavailable",
            "summary": {
                "total_symbols": 0,
                "filtered_symbols": 0,
                "live_quotes": 0,
                "average_spread_points": "0.00",
                "max_spread_points": 0,
                "max_spread_symbol": None,
            },
            "rows": [],
            "top_margin_accounts": [],
            "warnings": [_offline_manager_warning(exc.detail)],
        }
    import mt5_connection
    engine = mt5_connection.get_market_engine()
    if not engine:
        return {
            "status": "disconnected",
            "server_time_msc": None,
            "server_time_source": "unavailable",
            "summary": {
                "total_symbols": 0,
                "filtered_symbols": 0,
                "live_quotes": 0,
                "average_spread_points": "0.00",
                "max_spread_points": 0,
                "max_spread_symbol": None,
            },
            "rows": [],
            "top_margin_accounts": [],
            "warnings": ["Market watch engine is not initialized."],
        }
    try:
        return engine.live_watch(search=q, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@api.get("/api/manager/accounts")
def manager_accounts(
    request: Request,
    q: str = Query("", max_length=120),
    enabled_only: bool = False,
):
    try:
        mgr = _get_manager_from_request(request)
    except HTTPException:
        return []
    snapshot = mgr.snapshot()
    users = snapshot.users
    token = q.strip().lower()
    
    out = []
    for u in users:
        login = str(u.get("Login", ""))
        name = str(u.get("Name", "")).lower()
        group = str(u.get("Group", "")).lower()
        
        if token and (token not in login and token not in name and token not in group):
            continue
        if enabled_only and not u.get("Enabled", False):
            continue
        out.append(u)
        
    return out


def _book_group_settings_record(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    return {
        "all_groups": "dynamic",
        "a_book": _json_column(data.get("a_book_json"), []) or [],
        "b_book": _json_column(data.get("b_book_json"), []) or [],
        "cent": _json_column(data.get("cent_json"), []) or [],
        "hybrid": _json_column(data.get("hybrid_json"), []) or [],
        "updated_by": data.get("updated_by", "system"),
        "updated_at": data.get("updated_at"),
    }


@api.get("/api/settings/books")
def get_book_group_settings():
    with _conn() as con:
        row = con.execute("SELECT * FROM book_group_settings WHERE id=1").fetchone()
    if not row:
        return _book_group_settings_record({})
    return _book_group_settings_record(row)


@api.put("/api/settings/books")
def update_book_group_settings(req: BookGroupSettings, request: Request):
    actor = _risk_admin_actor(request)
    payload = req.model_dump()
    with _conn() as con:
        con.execute(
            """UPDATE book_group_settings SET
               a_book_json=?, b_book_json=?, cent_json=?, hybrid_json=?,
               updated_by=?, updated_at=datetime('now') WHERE id=1""",
            (
                json.dumps(payload["a_book"]),
                json.dumps(payload["b_book"]),
                json.dumps(payload["cent"]),
                json.dumps(payload["hybrid"]),
                actor,
            ),
        )
        row = con.execute("SELECT * FROM book_group_settings WHERE id=1").fetchone()
    saved = _book_group_settings_record(row)
    write_audit(actor, "settings.books_updated", "book_group_settings", 1, payload)
    return saved


@api.post("/api/settings/reset-database")
def reset_application_database(req: DatabaseResetRequest, request: Request):
    """Clear generated RMS data while retaining schema and administrator logins."""
    actor = _risk_admin_actor(request)
    expected_password = os.getenv("RMS_RESET_PASSWORD")
    if not expected_password or not expected_password.strip():
        raise HTTPException(status_code=503, detail="Database reset is disabled; configure RMS_RESET_PASSWORD to enable it")
    if not hmac.compare_digest(req.password, expected_password):
        raise HTTPException(status_code=403, detail="Reset password is incorrect")
    if req.confirmation != "DELETE ALL DATA":
        raise HTTPException(
            status_code=400,
            detail='Type "DELETE ALL DATA" to confirm the database reset',
        )

    with _DATABASE_RESET_LOCK:
        with _conn() as con:
            tables = [
                str(row[0])
                for row in con.execute(
                    """SELECT name FROM sqlite_master
                       WHERE type='table' AND name NOT LIKE 'sqlite_%'
                         AND name <> 'app_users'"""
                ).fetchall()
            ]
            counts = {
                table: int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in tables
            }
            con.execute("PRAGMA defer_foreign_keys=ON")
            for table in tables:
                con.execute(f'DELETE FROM "{table}"')
            if tables:
                placeholders = ",".join("?" for _ in tables)
                con.execute(
                    f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                    tables,
                )

        init_db()
        from services.historical import HistoryStore
        HistoryStore(DB_PATH).migrate(
            os.getenv("HISTORY_BACKFILL_FROM") or (datetime.now(timezone.utc).date()-timedelta(days=30)).isoformat(),
            int(os.getenv("HISTORY_SYNC_INTERVAL_MINUTES", "60")),
            int(os.getenv("HISTORY_SNAPSHOT_SECONDS", "300")),
            int(os.getenv("HISTORY_RECONCILIATION_HOUR_UTC", "2")),
        )
        with _PERFORMANCE_CACHE_LOCK:
            _PERFORMANCE_CACHE.clear()
            _PERFORMANCE_REFRESHING.clear()
        with _ANTIFRAUD_CACHE_LOCK:
            _ANTIFRAUD_CACHE.clear()
            _ANTIFRAUD_REFRESHING.clear()
        with _STRESS_CACHE_LOCK:
            _STRESS_CACHE.clear()
            _STRESS_REFRESHING.clear()
        with _BOOK_HISTORY_LOCK:
            _BOOK_HISTORY_REFRESHING.clear()
        _TELEGRAM_SENT.clear()
        SPREAD_ALERTS.reset_cache()

    deleted_rows = sum(counts.values())
    write_audit(
        actor,
        "database.reset",
        "application_database",
        "all",
        {"deleted_rows": deleted_rows, "tables_cleared": len(counts)},
    )
    logger.warning(
        "Application database reset by %s: %s rows across %s tables",
        actor,
        deleted_rows,
        len(counts),
    )
    return {
        "ok": True,
        "deleted_rows": deleted_rows,
        "tables_cleared": len(counts),
        "preserved": ["app_users", "database schema"],
    }


@api.get("/api/settings/book-shifts")
def get_book_shift_events(
    response: Response, limit: int = Query(default=100, ge=1, le=1000)
):
    """Return durable, newest-first account group movement evidence."""
    response.headers["Cache-Control"] = "no-store, max-age=0"
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM book_shift_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        floating = float(item.get("floating_pnl_at_shift") or 0.0)
        item["from_book_correction"] = round(
            floating * _book_money_factor(str(item.get("from_book") or "")), 2
        )
        item["to_book_correction"] = round(
            -floating * _book_money_factor(str(item.get("to_book") or "")), 2
        )
        items.append(item)
    return {
        "items": items,
        "count": len(rows),
        "equity_source": "MT5 Manager account snapshot at detection generation",
        "correction_source": "MT5 floating P&L at detection; incoming is negated and outgoing is restored",
    }


class ClearBookShiftHistoryRequest(BaseModel):
    confirmation: str


@api.post("/api/settings/book-shifts/clear")
def clear_book_shift_history(req: ClearBookShiftHistoryRequest, request: Request):
    actor = _operations_admin_actor(request)
    if req.confirmation != "CLEAR BOOK SHIFTS":
        raise HTTPException(status_code=400, detail='Type "CLEAR BOOK SHIFTS" to confirm')
    with _DATABASE_RESET_LOCK:
        with _conn() as con:
            deleted = con.execute("DELETE FROM book_shift_events").rowcount
    write_audit(actor, "book_shifts.cleared", "book_shift_events", "all", {"deleted_rows": deleted})
    return {"ok": True, "deleted_rows": deleted}


def _book_history_days(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)]


def _book_history_request_groups(groups: list[str]) -> list[str]:
    """Compress complete SDK group branches without expanding the book scope."""
    import mt5_connection
    manager = mt5_connection.get_manager()
    catalog = manager.group_cache_view() if manager and callable(getattr(manager, "group_cache_view", None)) else []
    return _compress_complete_group_branches(groups, catalog) if catalog else list(groups)


def _backfill_book_pnl_history(book: str, date_from: date, date_to: date) -> int:
    """Build durable daily rows once from MT5 history, never on a UI render path."""
    groups = list(get_book_group_settings().get(book) or [])
    if not groups:
        return 0
    factor = _book_money_factor(book)
    history_from = date_from - timedelta(days=1)
    from_ts = int(datetime.combine(history_from, time.min, tzinfo=timezone.utc).timestamp())
    to_ts = int(datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc).timestamp()) - 1
    daily_records: list[Any] = []
    deals: list[Any] = []
    warnings: list[str] = []
    with yielding_history_manager_session() as api_handle:
        for group in _book_history_request_groups(groups):
            rows, error = request_daily_by_group(api_handle, group, from_ts, to_ts)
            if error:
                warnings.append(f"{group} daily: {error}")
            else:
                daily_records.extend(rows)
            rows, error = request_deals_by_group(api_handle, group, from_ts, to_ts)
            if error:
                warnings.append(f"{group} deals: {error}")
            else:
                deals.extend(rows)
    if warnings:
        raise ValueError("Book history is incomplete: " + "; ".join(warnings[:5]))

    latest_daily: dict[tuple[date, int], tuple[int, Any]] = {}
    for order, row in enumerate(daily_records):
        day = _row_date(_value(row, "Datetime", "datetime", "Time", "time"))
        login = _to_int(_value(row, "Login", "login"), 0) or 0
        if day is None or login <= 0:
            continue
        key = (day, login)
        stamp = _to_int(_value(row, "Datetime", "datetime", "Time", "time"), 0) or 0
        previous = latest_daily.get(key)
        if previous is None or stamp >= previous[0]:
            latest_daily[key] = (stamp, row)
    floating_by_day: dict[date, float] = defaultdict(float)
    balance_by_day: dict[date, float] = defaultdict(float)
    equity_by_day: dict[date, float] = defaultdict(float)
    accounts_by_day: dict[date, int] = defaultdict(int)
    incomplete_days: set[date] = set()
    for (day, _login), (_stamp, row) in latest_daily.items():
        equity = _pick_float(_value(row, "Equity", "equity"),
                             _value(row, "ProfitEquity", "profit_equity"), default=None)
        if (equity is None or _to_float(_value(row, "Balance", "balance"), None) is None or
                _to_float(_value(row, "Credit", "credit"), None) is None):
            incomplete_days.add(day)
            continue
        balance = _to_float(_value(row, "Balance", "balance"), 0.0)
        credit = _to_float(_value(row, "Credit", "credit"), 0.0)
        floating_by_day[day] += (equity - balance - credit) * factor
        balance_by_day[day] += balance * factor
        equity_by_day[day] += equity * factor
        accounts_by_day[day] += 1

    deposits_by_day: dict[date, float] = defaultdict(float)
    withdrawals_by_day: dict[date, float] = defaultdict(float)
    closed_profit_by_day: dict[date, float] = defaultdict(float)
    swaps_by_day: dict[date, float] = defaultdict(float)
    commissions_by_day: dict[date, float] = defaultdict(float)
    seen_deals: set[str] = set()
    for order, deal in enumerate(deals):
        deal_id = str(_value(deal, "Deal", "deal", "Ticket", "ticket", default=f"row-{order}"))
        if deal_id in seen_deals:
            continue
        seen_deals.add(deal_id)
        action = _to_int(_value(deal, "Action", "action"), None)
        timestamp = _deal_timestamp(deal)
        if timestamp <= 0:
            continue
        day = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
        amount = _to_float(_value(deal, "Profit", "profit"), 0.0) * factor
        if action == DEAL_BALANCE:
            if amount >= 0:
                deposits_by_day[day] += amount
            else:
                withdrawals_by_day[day] += abs(amount)
            continue
        volume = _deal_volume_units(deal)
        if not _is_trade_deal(deal, action, volume):
            continue
        entry = _to_int(_value(deal, "Entry", "entry"), None)
        entry_text = str(_value(deal, "Entry", "entry", "EntryType", "entry_type", default="") or "").lower()
        profit = amount
        is_close = entry in CLOSED_ENTRIES or entry_text in {
            "out", "out_by", "inout", "entry_out", "entry_out_by", "entry_inout", "close", "closed",
        } or (entry is None and not entry_text and profit != 0.0)
        commissions_by_day[day] += (
            _to_float(_value(deal, "Commission", "commission"), 0.0)
            + _to_float(_value(deal, "Fee", "fee"), 0.0)
        ) * factor
        if not is_close:
            continue
        closed_profit_by_day[day] += profit
        swaps_by_day[day] += _to_float(
            _value(deal, "Storage", "storage", "Swap", "swap"), 0.0
        ) * factor

    # An absent daily snapshot is unknown floating P&L, never zero. This also
    # prevents an empty configured group from producing a fabricated $0 result.
    for day in _book_history_days(history_from, date_to):
        if day not in accounts_by_day:
            incomplete_days.add(day)

    with _conn() as con:
        shifts = con.execute(
            """SELECT date(detected_at) AS day,
                      SUM(CASE WHEN to_book=? THEN -floating_pnl_at_shift
                               WHEN from_book=? THEN floating_pnl_at_shift ELSE 0 END) AS correction
               FROM book_shift_events WHERE date(detected_at) BETWEEN ? AND ? GROUP BY date(detected_at)""",
            (book, book, date_from.isoformat(), date_to.isoformat()),
        ).fetchall()
        corrections = {
            date.fromisoformat(row["day"]): float(row["correction"] or 0) * factor
            for row in shifts
        }
        previous_floating = None if history_from in incomplete_days else floating_by_day[history_from]
        stored = 0
        stored_days: set[date] = set()
        for day in _book_history_days(date_from, date_to):
            closing = None if day in incomplete_days else floating_by_day[day]
            if closing is None:
                previous_floating = None
                continue
            if previous_floating is None:
                previous_floating = closing
                continue
            closed_profit = closed_profit_by_day.get(day, 0.0)
            swaps = swaps_by_day.get(day, 0.0)
            commissions = commissions_by_day.get(day, 0.0)
            closed = closed_profit + swaps + commissions
            deposits = deposits_by_day.get(day, 0.0)
            withdrawals = withdrawals_by_day.get(day, 0.0)
            correction = corrections.get(day, 0.0)
            delta = closing - previous_floating
            client_total = closed + delta + correction
            broker_total = _book_broker_pnl(book, client_total, swaps, commissions)
            total = broker_total if broker_total is not None else client_total
            con.execute(
                """INSERT INTO book_pnl_daily
                   (book,report_day,deposits,withdrawals,net_in_out,balance,equity,
                    closed_profit,swaps,commissions,closed_swaps_comm,
                    opening_floating,closing_floating,
                    floating_delta,shift_corrections,ib_payout,total_book_pnl,account_count,source_status,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(book,report_day) DO UPDATE SET
                    deposits=excluded.deposits,withdrawals=excluded.withdrawals,
                    net_in_out=excluded.net_in_out,balance=excluded.balance,equity=excluded.equity,
                    closed_profit=excluded.closed_profit,swaps=excluded.swaps,
                    commissions=excluded.commissions,
                    closed_swaps_comm=excluded.closed_swaps_comm,
                    opening_floating=excluded.opening_floating,
                    closing_floating=excluded.closing_floating,
                    floating_delta=excluded.floating_delta,
                    shift_corrections=excluded.shift_corrections,
                    ib_payout=excluded.ib_payout,total_book_pnl=excluded.total_book_pnl,
                    account_count=excluded.account_count,source_status=excluded.source_status,
                    updated_at=datetime('now')""",
                (book, day.isoformat(), round(deposits, 2), round(withdrawals, 2),
                 round(deposits - withdrawals, 2), round(balance_by_day.get(day, 0.0), 2),
                 round(equity_by_day.get(day, 0.0), 2), round(closed_profit, 2),
                 round(swaps, 2), round(commissions, 2), round(closed, 2), round(previous_floating, 2),
                 round(closing, 2), round(delta, 2), round(correction, 2), 0.0,
                 round(total, 2), accounts_by_day.get(day, 0), "full"),
            )
            stored += 1
            stored_days.add(day)
            previous_floating = closing
        if book == "a_book" and (accounts_by_day or deals):
            # A-Book broker revenue from posted charges is independent of
            # equity snapshots. A day without a daily row can still have a
            # verified charge result when the deal request succeeded. An
            # entirely empty scope does not establish a zero-valued book.
            for day in _book_history_days(date_from, date_to):
                if day in stored_days:
                    continue
                swaps = swaps_by_day.get(day, 0.0)
                commissions = commissions_by_day.get(day, 0.0)
                closed_profit = closed_profit_by_day.get(day, 0.0)
                con.execute(
                    """INSERT INTO book_pnl_daily
                       (book,report_day,deposits,withdrawals,net_in_out,
                        closed_profit,swaps,commissions,closed_swaps_comm,
                        total_book_pnl,account_count,source_status,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                       ON CONFLICT(book,report_day) DO UPDATE SET
                        deposits=excluded.deposits,withdrawals=excluded.withdrawals,
                        net_in_out=excluded.net_in_out,closed_profit=excluded.closed_profit,
                        swaps=excluded.swaps,commissions=excluded.commissions,
                        closed_swaps_comm=excluded.closed_swaps_comm,
                        total_book_pnl=excluded.total_book_pnl,account_count=0,
                        source_status='charges_only',updated_at=datetime('now')""",
                    (book, day.isoformat(), round(deposits_by_day.get(day, 0.0), 2),
                     round(withdrawals_by_day.get(day, 0.0), 2),
                     round(deposits_by_day.get(day, 0.0) - withdrawals_by_day.get(day, 0.0), 2),
                     round(closed_profit, 2), round(swaps, 2), round(commissions, 2),
                     round(closed_profit + swaps + commissions, 2),
                     round(-swaps - commissions, 2), 0, "charges_only"),
                )
                stored += 1
    return stored


def _start_book_history_backfill(book: str, date_from: date, date_to: date) -> bool:
    key = (book, date_from.isoformat(), date_to.isoformat())
    with _BOOK_HISTORY_LOCK:
        if key in _BOOK_HISTORY_REFRESHING:
            return False
        _BOOK_HISTORY_REFRESHING.add(key)

    def run() -> None:
        try:
            _backfill_book_pnl_history(book, date_from, date_to)
        except Exception:
            logger.exception("Book P&L history backfill failed book=%s range=%s..%s", book, date_from, date_to)
        finally:
            with _BOOK_HISTORY_LOCK:
                _BOOK_HISTORY_REFRESHING.discard(key)

    threading.Thread(target=run, name=f"{book}-pnl-backfill", daemon=True).start()
    return True


def _book_dashboard_payload(book: str, date_from: date, date_to: date) -> dict[str, Any]:
    factor = _book_money_factor(book)
    configured_groups = bool(get_book_group_settings().get(book))
    with _conn() as con:
        rows = con.execute(
            """SELECT * FROM book_pnl_snapshots
               WHERE book=? AND date(captured_at) BETWEEN ? AND ? ORDER BY captured_at""",
            (book, date_from.isoformat(), date_to.isoformat()),
        ).fetchall()
        if configured_groups:
            rows = [row for row in rows if int(row["account_count"] or 0) > 0]
        shifts = con.execute(
            """SELECT date(detected_at) AS day,
                      SUM(CASE WHEN to_book=? THEN -floating_pnl_at_shift
                               WHEN from_book=? THEN floating_pnl_at_shift ELSE 0 END) AS correction
               FROM book_shift_events WHERE date(detected_at) BETWEEN ? AND ?
               GROUP BY date(detected_at)""",
            (book, book, date_from.isoformat(), date_to.isoformat()),
        ).fetchall()
        historical = [dict(row) for row in con.execute(
            """SELECT * FROM book_pnl_daily WHERE book=? AND report_day BETWEEN ? AND ?
               ORDER BY report_day""",
            (book, date_from.isoformat(), date_to.isoformat()),
        ).fetchall() if (not configured_groups or int(row["account_count"] or 0) > 0
                        or (book == "a_book" and row["source_status"] == "charges_only"))]
    correction_by_day = {
        row["day"]: round(float(row["correction"] or 0) * factor, 2) for row in shifts
    }
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[str(row["captured_at"])[:10]].append(dict(row))
    daily = []
    historical_by_day = {row["report_day"]: row for row in historical}
    for day, samples in sorted(by_day.items()):
        # A stored Manager history row is the authoritative final result for a
        # completed UTC day. Intraday snapshots may have been captured before
        # all of that day's posted charges arrived.
        if day < datetime.now(timezone.utc).date().isoformat() and day in historical_by_day:
            continue
        first, last = samples[0], samples[-1]
        historical_opening = historical_by_day.get(day)
        opening = (
            float(historical_opening["opening_floating"])
            if historical_opening is not None
            else float(first["floating"])
        )
        closing = float(last["floating"])
        closed = float(last["closed_swaps_comm"])
        closed_profit = float(last["closed_profit"])
        swaps = float(last["swaps"])
        commissions = float(last["commissions"])
        if closed and not any((closed_profit, swaps, commissions)):
            # Snapshots written before component-level storage remain readable.
            closed_profit = closed
        correction = correction_by_day.get(day, 0.0)
        ib_payout = float(last["ib_payout"])
        delta = closing - opening
        client_total = closed + delta + correction
        broker_total = _book_broker_pnl(book, client_total, swaps, commissions)
        total = broker_total if broker_total is not None else client_total
        daily.append({
            "day": day, "closed_swaps_comm": round(closed, 2),
            "deposits": round(float(last["deposits"]), 2),
            "withdrawals": round(float(last["withdrawals"]), 2),
            "net_in_out": round(float(last["deposits"]) - float(last["withdrawals"]), 2),
            "balance": round(float(last["balance"]), 2),
            "equity": round(float(last["equity"]), 2),
            "closed_profit": round(closed_profit, 2),
            "swaps": round(swaps, 2), "commissions": round(commissions, 2),
            "opening_floating": round(opening, 2), "closing_floating": round(closing, 2),
            "floating_delta": round(delta, 2), "shift_corrections": correction,
            "ib_payout": round(ib_payout, 2), "client_pnl": round(client_total, 2),
            "broker_pnl": broker_total, "total_bbook_pnl": round(total, 2),
            "total_book_pnl": round(total, 2),
            "account_count": int(last["account_count"]),
        })
    live_days = {row["day"] for row in daily}
    for row in historical:
        if row["report_day"] in live_days:
            continue
        if row["source_status"] == "charges_only":
            swaps = float(row["swaps"])
            commissions = float(row["commissions"])
            broker_total = round(-swaps - commissions, 2)
            daily.append({
                "day": row["report_day"], "source": "verified_deals_only",
                "deposits": row["deposits"], "withdrawals": row["withdrawals"],
                "net_in_out": row["net_in_out"], "balance": None, "equity": None,
                "closed_profit": row["closed_profit"], "swaps": swaps,
                "commissions": commissions, "closed_swaps_comm": row["closed_swaps_comm"],
                "opening_floating": None, "closing_floating": None,
                "floating_delta": None, "shift_corrections": None,
                "ib_payout": row["ib_payout"], "client_pnl": None,
                "broker_pnl": broker_total, "total_bbook_pnl": broker_total,
                "total_book_pnl": broker_total, "account_count": 0,
            })
            continue
        historical_closed_profit = row["closed_profit"]
        if row["closed_swaps_comm"] and not any(
            (row["closed_profit"], row["swaps"], row["commissions"])
        ):
            historical_closed_profit = row["closed_swaps_comm"]
        client_total = (float(historical_closed_profit) + float(row["swaps"]) +
                        float(row["commissions"]) + float(row["floating_delta"]) +
                        float(row["shift_corrections"]))
        broker_total = _book_broker_pnl(book, client_total, float(row["swaps"]), float(row["commissions"]))
        total = broker_total if broker_total is not None else client_total
        daily.append({
            "day": row["report_day"], "closed_swaps_comm": row["closed_swaps_comm"],
            "deposits": row["deposits"], "withdrawals": row["withdrawals"],
            "net_in_out": row["net_in_out"], "balance": row["balance"],
            "equity": row["equity"], "closed_profit": historical_closed_profit,
            "swaps": row["swaps"], "commissions": row["commissions"],
            "opening_floating": row["opening_floating"], "closing_floating": row["closing_floating"],
            "floating_delta": row["floating_delta"], "shift_corrections": row["shift_corrections"],
            "ib_payout": row["ib_payout"], "client_pnl": round(client_total, 2),
            "broker_pnl": broker_total, "total_bbook_pnl": round(total, 2),
            "total_book_pnl": round(total, 2),
            "account_count": row["account_count"], "source": "historical_cache",
        })
    daily.sort(key=lambda row: row["day"])
    intraday = [{
        "captured_at": row["captured_at"], "floating": float(row["floating"]),
        "closed_swaps_comm": float(row["closed_swaps_comm"]),
        "swaps": float(row["swaps"]), "commissions": float(row["commissions"]),
    } for row in rows]
    totals = {key: (round(sum(float(row[key]) for row in daily), 2)
                    if all(row[key] is not None for row in daily) else None) for key in (
        "deposits", "withdrawals", "net_in_out", "closed_profit", "swaps",
        "commissions", "closed_swaps_comm", "floating_delta", "shift_corrections",
        "ib_payout", "total_bbook_pnl"
    )}
    totals["client_pnl"] = (round(sum(float(row["client_pnl"]) for row in daily), 2)
                            if all(row["client_pnl"] is not None for row in daily) else None)
    totals["broker_pnl"] = (round(sum(float(row["broker_pnl"]) for row in daily), 2)
                            if book in {"a_book", "b_book"} else None)
    if book == "a_book":
        totals["broker_commission"] = (round(-totals["commissions"], 2) or 0.0
                                       if totals.get("commissions") is not None else None)
        totals["broker_swap"] = (round(-totals["swaps"], 2) or 0.0
                                 if totals.get("swaps") is not None else None)
    if daily:
        totals["balance"] = daily[-1]["balance"]
        totals["equity"] = daily[-1]["equity"]
        totals["closing_floating"] = daily[-1]["closing_floating"]
        totals["total_book_pnl"] = totals["total_bbook_pnl"]
    else:
        totals = {}
    labels = {"a_book": "A Book", "b_book": "B Book", "cent": "Cent Book", "hybrid": "Hybrid"}
    last_updated = rows[-1]["captured_at"] if rows else None
    next_sync_at = None
    if last_updated:
        try:
            next_sync_at = (
                datetime.fromisoformat(str(last_updated).replace("Z", "+00:00"))
                + timedelta(seconds=BBOOK_SNAPSHOT_INTERVAL_SECONDS)
            ).isoformat()
        except ValueError:
            next_sync_at = None
    return {
        "book": book, "book_label": labels[book],
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "daily": list(reversed(daily)), "intraday": intraday, "totals": totals,
        "last_updated": last_updated, "next_sync_at": next_sync_at,
        "source": "MT5 Summary/Segregated-compatible SQLite snapshots",
        "formula": ("B-Book broker P&L = -(client closed profit + posted swap + posted commission + floating delta + shift corrections)"
                    if book == "b_book" else
                    "A-Book broker P&L = -(posted client swap + posted client commission); LP costs unavailable"
                    if book == "a_book" else
                    "Client P&L = closed profit + posted swap + posted commission + floating delta + shift corrections; broker routing unavailable"),
        "performance_perspective": "broker" if book in {"a_book", "b_book"} else "client",
        "currency": "USD", "cent_divisor": CENT_ACCOUNT_DIVISOR if book == "cent" else 1,
        "refresh_seconds": BBOOK_SNAPSHOT_INTERVAL_SECONDS,
    }


def _book_performance_from_deals(api_handle: Any, book: str, groups: list[str],
                                 date_from: date, date_to: date) -> dict[str, Any]:
    """Deal-backed book activity. Never infer A-Book net P&L from client charges."""
    _, _, from_ts, to_ts = _date_range_utc(date_from, date_to)
    warning_rows: list[str] = []
    group_rows: list[dict[str, Any]] = []
    by_currency: dict[str, dict[str, Any]] = {}
    seen_tickets: dict[int, tuple[Any, ...]] = {}
    complete = True

    def new_bucket() -> dict[str, Any]:
        return {
                "trade_profit": [], "commission": [], "fee": [], "swap": [],
                "executions": 0, "closed_deals": 0, "volume_lots": [],
                "accounts": set(), "days": defaultdict(lambda: {
                    "trade_profit": [], "commission": [], "fee": [], "swap": [],
                    "executions": 0, "closed_deals": 0,
                }),
            }

    def add_amount(target: dict[str, Any], day: date, key: str, amount: float) -> None:
        target[key].append(amount)
        target["days"][day.isoformat()][key].append(amount)

    for group in groups:
        currency, currency_error = brokerage_pnl.request_group_currency(api_handle, group)
        factor = 1.0
        if book == "cent" and currency.upper() == "USC":
            currency = "USD"
            factor = 1.0 / CENT_ACCOUNT_DIVISOR
        elif book == "cent" and currency.upper() != "USC":
            currency_error = currency_error or f"{group}: cent currency {currency} cannot be normalized to USD."
        if currency_error:
            complete = False
            warning_rows.append(currency_error)
        if not currency or currency == "Unknown":
            currency = f"Unknown ({group})"
        deals, error = request_deals_by_group(api_handle, group, from_ts, to_ts)
        if error:
            complete = False
            warning_rows.append(f"{group}: deal history unavailable: {error}")
            group_rows.append({"group": group, "currency": currency, "status": "error", "executions": 0})
            continue
        target = by_currency.setdefault(currency, new_bucket())
        group_target = new_bucket()
        group_executions = 0
        group_bad = 0
        for deal in deals:
            ticket = _to_int(_value(deal, "Deal", "Ticket"), 0) or 0
            login = _to_int(_value(deal, "Login"), 0) or 0
            stamp = _deal_timestamp(deal)
            action = _to_int(_value(deal, "Action"), -1)
            if not ticket or not login or not stamp:
                complete = False
                group_bad += 1
                continue
            if stamp < from_ts or stamp > to_ts:
                continue
            fingerprint = (login, stamp, action, _value(deal, "Profit"),
                           _value(deal, "Commission"), _value(deal, "Fee"),
                           _value(deal, "Storage", "Swap"))
            if ticket in seen_tickets:
                if seen_tickets[ticket] != fingerprint:
                    complete = False
                    group_bad += 1
                    warning_rows.append(f"Deal {ticket} has conflicting history records; excluded duplicate.")
                continue
            seen_tickets[ticket] = fingerprint
            day = datetime.fromtimestamp(stamp, timezone.utc).date()
            is_trade = action in brokerage_pnl.CLOSED_TRADE_ACTIONS
            charge_kind = brokerage_pnl._charge_posting_kind(deal)
            if not is_trade and not charge_kind:
                continue
            profit = _to_float(_value(deal, "Profit"), None)
            if profit is None or charge_kind == "Unallocated charges":
                complete = False
                group_bad += 1
                continue
            target["accounts"].add(login)
            group_target["accounts"].add(login)
            if is_trade:
                commission = _to_float(_value(deal, "Commission", default=0), None)
                fee = _to_float(_value(deal, "Fee", default=0), None)
                swap = _to_float(_value(deal, "Storage", "Swap", default=0), None)
                if None in (commission, fee, swap):
                    complete = False
                    group_bad += 1
                    continue
                add_amount(target, day, "trade_profit", profit * factor)
                add_amount(group_target, day, "trade_profit", profit * factor)
                add_amount(target, day, "commission", commission * factor)
                add_amount(group_target, day, "commission", commission * factor)
                add_amount(target, day, "fee", fee * factor)
                add_amount(group_target, day, "fee", fee * factor)
                add_amount(target, day, "swap", swap * factor)
                add_amount(group_target, day, "swap", swap * factor)
                if action in {DEAL_BUY, DEAL_SELL}:
                    target["executions"] += 1
                    group_target["executions"] += 1
                    target["days"][day.isoformat()]["executions"] += 1
                    group_target["days"][day.isoformat()]["executions"] += 1
                    group_executions += 1
                    target["volume_lots"].append(_deal_volume_units(deal) / 10000.0)
                    group_target["volume_lots"].append(_deal_volume_units(deal) / 10000.0)
                    if _to_int(_value(deal, "Entry"), -1) in CLOSED_ENTRIES:
                        target["closed_deals"] += 1
                        group_target["closed_deals"] += 1
                        target["days"][day.isoformat()]["closed_deals"] += 1
                        group_target["days"][day.isoformat()]["closed_deals"] += 1
            elif charge_kind == "Commission":
                add_amount(target, day, "commission", profit * factor)
                add_amount(group_target, day, "commission", profit * factor)
            elif charge_kind == "Swap":
                add_amount(target, day, "swap", profit * factor)
                add_amount(group_target, day, "swap", profit * factor)
        if group_bad:
            warning_rows.append(f"{group}: {group_bad} deal records need review and were excluded or partially attributed.")
        group_sums = {key: round(math.fsum(group_target[key]), 2)
                      for key in ("trade_profit", "commission", "fee", "swap")}
        group_rows.append({"group": group, "currency": currency, **group_sums,
                           "client_deal_result": round(math.fsum(
                               math.fsum(group_target[key])
                               for key in ("trade_profit", "commission", "fee", "swap")), 2),
                           "status": "partial" if group_bad or currency_error else "complete",
                           "executions": group_executions,
                           "active_accounts": len(group_target["accounts"])})

    currencies = []
    for currency, values in sorted(by_currency.items()):
        raw_sums = {key: math.fsum(values[key])
                    for key in ("trade_profit", "commission", "fee", "swap")}
        sums = {key: round(value, 2) for key, value in raw_sums.items()}
        days = []
        cumulative_raw = 0.0
        for day in _book_history_days(date_from, date_to):
            entry = values["days"][day.isoformat()]
            day_sums = {key: round(math.fsum(entry[key]), 2)
                        for key in ("trade_profit", "commission", "fee", "swap")}
            day_net_raw = math.fsum(math.fsum(entry[key])
                                    for key in ("trade_profit", "commission", "fee", "swap"))
            net = round(day_net_raw, 2)
            cumulative_raw += day_net_raw
            days.append({"day": day.isoformat(), **day_sums,
                         "client_deal_result": net, "cumulative_result": round(cumulative_raw, 2),
                         "executions": entry["executions"], "closed_deals": entry["closed_deals"]})
        client_result = round(math.fsum(raw_sums.values()), 2)
        currencies.append({
            "currency": currency, **sums, "client_deal_result": client_result,
            "rounding_adjustment": round(client_result - math.fsum(sums.values()), 2),
            "posted_client_charges": round(math.fsum(
                raw_sums[key] for key in ("commission", "fee", "swap")), 2),
            "executions": values["executions"], "closed_deals": values["closed_deals"],
            "volume_lots": round(math.fsum(values["volume_lots"]), 2),
            "active_accounts": len(values["accounts"]), "daily": days,
            "broker_net_pnl": None,
        })
    return {
        "book": book, "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "MT5 Manager deal history", "configured_groups": len(groups),
        "groups_checked": sum(row["status"] != "error" for row in group_rows),
        "complete": complete and len(group_rows) == len(groups),
        "currencies": currencies, "groups": group_rows, "warnings": warning_rows[:100],
        "broker_net_status": (
            "LP execution prices, LP charges, markup and hedge allocations are required for A-Book net profit."
            if book == "a_book" else
            "Realized client deals do not establish total broker net profit without routing and external cost records."
        ),
    }


@api.post("/api/book-performance")
def get_book_performance(req: BookPerformanceRequest):
    if req.date_from > req.date_to:
        raise HTTPException(status_code=400, detail="Date From must not be after Date To.")
    if req.date_to > datetime.now(timezone.utc).date():
        raise HTTPException(status_code=400, detail="Future UTC dates are unavailable.")
    if (req.date_to - req.date_from).days > 90:
        raise HTTPException(status_code=400, detail="Choose at most 90 days per analysis.")
    groups = sorted(set(get_book_group_settings().get(req.book) or []))
    if not groups:
        return {"book": req.book, "date_from": req.date_from.isoformat(),
                "date_to": req.date_to.isoformat(), "configured_groups": 0,
                "groups_checked": 0, "complete": False, "currencies": [], "groups": [],
                "warnings": ["No groups are assigned to this book in Controls."],
                "source": "MT5 Manager deal history", "generated_at": datetime.now(timezone.utc).isoformat(),
                "broker_net_status": "No book group scope is configured."}
    with yielding_history_manager_session() as api_handle:
        return _book_performance_from_deals(api_handle, req.book, groups,
                                            req.date_from, req.date_to)


@api.get("/api/book-pnl")
def get_book_pnl(
    response: Response,
    book: str = Query(default="b_book", pattern="^(a_book|b_book|cent|hybrid)$"),
    date_from: date = Query(default_factory=lambda: date.today() - timedelta(days=6)),
    date_to: date = Query(default_factory=date.today),
):
    if date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must not be later than date_to")
    if (date_to - date_from).days > 366:
        raise HTTPException(status_code=400, detail="Date range cannot exceed 366 days")
    if date_to > date.today():
        raise HTTPException(status_code=400, detail="date_to cannot be in the future")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    payload = _book_dashboard_payload(book, date_from, date_to)
    configured_groups = list(get_book_group_settings().get(book) or [])
    with _conn() as con:
        latest_scope = con.execute(
            "SELECT account_count FROM book_pnl_snapshots WHERE book=? ORDER BY captured_at DESC LIMIT 1",
            (book,),
        ).fetchone()
    payload["latest_account_count"] = int(latest_scope["account_count"] or 0) if latest_scope else None
    import mt5_connection
    live_manager = mt5_connection.get_manager()
    payload["live"] = None
    if live_manager and live_manager.is_connected and configured_groups:
        live_view = live_manager.live_risk_cache_view(groups=configured_groups)
        stamp = float(live_view.get("last_update", 0))
        if stamp > 0 and time_module.time() - stamp <= 10 and not live_view.get("errors"):
            scoped_users = live_view["users"]
            currencies = {str(row.get("Currency") or "").upper() for row in scoped_users}
            if scoped_users and (currencies == {"USC"} if book == "cent" else currencies == {"USD"}):
                factor = _book_money_factor(book)
                payload["live"] = {
                    "balance": round(sum(_to_float(row.get("Balance"), 0) for row in scoped_users) * factor, 2),
                    "equity": round(sum(_to_float(row.get("Equity"), 0) for row in scoped_users) * factor, 2),
                    "floating": round(sum(_to_float(row.get("Profit"), 0) + _to_float(row.get("Swap"), 0) for row in live_view["positions"]) * factor, 2),
                    "account_count": len(scoped_users), "source_updated_at": stamp,
                }

    expected = {day.isoformat() for day in _book_history_days(date_from, date_to)}
    covered = {row["day"] for row in payload["daily"]}
    missing = sorted(expected - covered)
    key = (book, date_from.isoformat(), date_to.isoformat())
    if missing and configured_groups:
        _start_book_history_backfill(book, date_from, date_to)
    with _BOOK_HISTORY_LOCK:
        refreshing = key in _BOOK_HISTORY_REFRESHING
    payload["coverage"] = {
        "expected_days": len(expected), "available_days": len(covered),
        "missing_days": len(missing), "complete": not missing,
    }
    if missing and payload["totals"]:
        payload["available_days_subtotal"] = {
            key: payload["totals"].get(key)
            for key in ("client_pnl", "broker_pnl", "total_book_pnl")
        }
        for key in ("client_pnl", "broker_pnl", "total_book_pnl", "total_bbook_pnl"):
            payload["totals"][key] = None
    payload["configured_groups"] = len(configured_groups)
    payload["backfill"] = {
        "refreshing": refreshing,
        "message": (
            "No groups are assigned to this book"
            if not configured_groups
            else "Loading and storing historical daily data"
            if refreshing
            else "Historical coverage is complete"
        ),
    }
    if book == "a_book":
        # Legacy clients may still call this endpoint. Client charges cannot
        # establish actual A-Book broker P&L without LP fills and costs.
        payload["posted_client_charge_offset"] = payload["totals"].get("total_book_pnl")
        if payload.get("available_days_subtotal"):
            subtotal = payload["available_days_subtotal"]
            subtotal["posted_client_charge_offset"] = subtotal.get("total_book_pnl")
            subtotal["broker_pnl"] = None
            subtotal["total_book_pnl"] = None
        for row in payload["daily"]:
            row["posted_client_charge_offset"] = row.get("broker_pnl")
            row["broker_pnl"] = None
            row["total_book_pnl"] = None
            row["total_bbook_pnl"] = None
        for key in ("broker_pnl", "total_book_pnl", "total_bbook_pnl"):
            if payload["totals"]:
                payload["totals"][key] = None
        payload["formula"] = "A-Book broker net P&L requires LP execution and cost records."
        payload["performance_perspective"] = "client_posted_charges_only"
    return payload


@api.get("/api/bbook-pnl")
def get_bbook_pnl(
    response: Response,
    date_from: date = Query(default_factory=lambda: date.today() - timedelta(days=6)),
    date_to: date = Query(default_factory=date.today),
):
    """Backward-compatible alias for clients that loaded the first release."""
    return get_book_pnl(response, "b_book", date_from, date_to)



@api.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    rate_limit_key = _enforce_login_rate_limit(request, req.username)
    user = verify_user(req.username, req.password)
    if not user:
        _record_login_result(rate_limit_key, False)
        write_audit(req.username or "unknown", "auth.login_failed", "user", req.username)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    _record_login_result(rate_limit_key, True)
    write_audit(user.get("username", req.username), "auth.login_succeeded", "user", user.get("id"))
    token = _create_session_token(user)
    response = JSONResponse({
        "user": user,
        "expires_in": SESSION_TTL_SECONDS,
    })
    _set_session_cookie(response, token)
    return response


@api.get("/api/auth/session")
def auth_session(request: Request):
    return {"user": request.state.user, "expires_in": SESSION_TTL_SECONDS}


@api.post("/api/auth/change-password")
def change_password(req: PasswordChangeRequest, request: Request):
    user_id = int(request.state.user["id"])
    with _conn() as con:
        row = con.execute(
            "SELECT id, username, password_hash, is_admin FROM app_users WHERE id=?",
            (user_id,),
        ).fetchone()
        if not row or not _verify_pw(req.current_password, str(row["password_hash"])):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        con.execute(
            "UPDATE app_users SET password_hash=?, must_change_pw=0 WHERE id=?",
            (_hash_pw(req.new_password), user_id),
        )
    user = {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "is_admin": int(row["is_admin"]),
        "must_change_pw": 0,
    }
    write_audit(user["username"], "auth.password_changed", "user", user_id)
    token = _create_session_token(user)
    response = JSONResponse({"user": user, "expires_in": SESSION_TTL_SECONDS})
    _set_session_cookie(response, token)
    return response


@api.post("/api/auth/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@api.post("/api/mt5/connect")
def connect_mt5(req: MT5Credentials):
    del req
    manager = get_configured_manager()
    return {"ok": True, "server": manager.server, "login": manager.manager_login}


@api.post("/api/mt5/groups")
def fetch_groups(req: MT5Credentials):
    """Return groups only from the configured persistent Manager connection."""
    import mt5_connection

    mgr = mt5_connection.get_manager()
    cached_groups: list[str] = []
    try:
        configured_server, configured_login, _ = configured_manager_credentials()
    except Exception:
        configured_server, configured_login = req.server, req.login
    if mgr and mgr.is_connected and mgr.server == configured_server and mgr.manager_login == configured_login:
        snapshot = mgr.live_risk_cache_view()
        cached_groups = sorted(set(snapshot["groups"]) | {
            str(row.get("Group", "")).strip()
            for row in snapshot["users"]
            if str(row.get("Group", "")).strip()
        })
        payload = {
            "groups": cached_groups,
            "count": len(cached_groups),
            "source": "manager_cache",
        }
        configured_default = os.getenv("MT5_DEFAULT_LIVE_GROUP", "").strip()
        if configured_default in cached_groups:
            payload["default_group"] = configured_default
        if snapshot["last_update"] <= 0:
            payload["source"] = "manager_cache_initializing"
            payload["warning"] = "MT5 group cache is initializing; the catalog will refresh automatically."
        return payload

    if cached_groups:
        return {
            "groups": cached_groups,
            "count": len(cached_groups),
            "source": "manager_cache_fallback",
            "warning": _offline_manager_warning(_MANAGER_CONNECTION_ERROR),
        }
    raise HTTPException(
        status_code=503,
        detail="MT5 Manager is reconnecting. The group catalog will be available from its persistent cache shortly.",
    )


@api.get("/api/calendar")
def get_terminal_calendar():
    return read_calendar_api()


@api.get("/api/calendar/bridge.mq5")
def download_calendar_bridge():
    return FileResponse(Path(__file__).with_name("mt5") / "RMS_EconomicCalendar.mq5", filename="RMS_EconomicCalendar.mq5")


@api.get("/api/calendar/bridge.ex5")
def download_compiled_calendar_bridge():
    return FileResponse(Path(__file__).with_name("mt5") / "RMS_EconomicCalendar.ex5", filename="RMS_EconomicCalendar.ex5")


@api.post("/api/dealer-command-center")
def dealer_command_center(req: GroupScopedRequest):
    """One compact, scope-exact view from the published Manager generation."""
    empty = {"status": "offline", "source_updated_at": None, "generation": None, "selected_groups": [], "currency": None, "kpis": None, "symbol_exposure": [], "top_risk_accounts": [], "risk_events": [], "what_changed": [], "warnings": []}
    if not req.selected_groups:
        return {**empty, "warnings": ["Select one or more groups."]}
    try:
        manager = get_configured_manager()
    except Exception as exc:
        return {**empty, "warnings": [str(exc)]}
    catalog = manager.live_risk_cache_view(groups=[])
    if not catalog.get("last_update"):
        return {**empty, "status": "warming", "warnings": ["Verified MT5 generation is loading."]}
    groups, _, warnings = _resolve_groups_from_available(catalog["groups"], req.selected_groups, req.group_filter_rules)
    if not groups:
        return {**empty, "warnings": _dedupe_warnings(warnings, ["No groups matched the selection."])}
    snapshot = manager.live_risk_cache_view(groups=groups)
    excluded = _normalize_login_set(req.excluded_logins)
    users = [row for row in snapshot["users"] if _to_int(row.get("Login"), 0) not in excluded]
    allowed_logins = {_to_int(row.get("Login"), 0) for row in users}
    positions = [row for row in snapshot["positions"] if _to_int(row.get("Login"), 0) in allowed_logins]
    currencies = {str(row.get("Currency") or "").strip().upper() for row in users}
    currency = next(iter(currencies)) if len(currencies) == 1 and "" not in currencies else None
    age_seconds = max(0.0, time_module.time() - float(snapshot["last_update"]))
    status = "stale" if age_seconds > 60 else "connected"
    risk = build_live_risk_snapshot_from_cache(users, positions, groups, [])
    symbol_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {"symbol": "", "buy_lots": 0.0, "sell_lots": 0.0, "open_positions": 0, "floating_pnl": 0.0, "accounts": set(), "gross_notional": 0.0, "notional_complete": True})
    for position in positions:
        symbol = str(position.get("Symbol") or "").strip()
        if not symbol:
            continue
        row = symbol_rows[symbol]
        row["symbol"] = symbol
        lots = max(_to_float(position.get("Volume"), 0.0), 0.0)
        action = _to_int(position.get("Action"), -1)
        if action == 0:
            row["buy_lots"] += lots
        elif action == 1:
            row["sell_lots"] += lots
        row["open_positions"] += 1
        row["floating_pnl"] += _to_float(position.get("Profit"), 0.0) + _to_float(position.get("Swap"), 0.0)
        row["accounts"].add(_to_int(position.get("Login"), 0))
        contract_size = _to_float(position.get("ContractSize"), 0.0)
        price = _to_float(position.get("PriceCurrent"), 0.0)
        conversion = _to_float(position.get("RateProfit"), 0.0)
        if not currency or contract_size <= 0 or price <= 0 or conversion <= 0:
            row["notional_complete"] = False
        else:
            row["gross_notional"] += abs(lots * contract_size * price * conversion)
    total_notional = sum(row["gross_notional"] for row in symbol_rows.values()) if all(row["notional_complete"] for row in symbol_rows.values()) else None
    exposure = [{"symbol": row["symbol"], "buy_lots": round(row["buy_lots"], 8), "sell_lots": round(row["sell_lots"], 8), "net_lots": round(row["buy_lots"] - row["sell_lots"], 8), "open_positions": row["open_positions"], "floating_pnl": round(row["floating_pnl"], 2) if currency else None, "accounts": len(row["accounts"]), "gross_notional": round(row["gross_notional"], 2) if row["notional_complete"] else None, "concentration_pct": round(row["gross_notional"] / total_notional * 100, 2) if total_notional and row["notional_complete"] else None, "risk_status": "measured" if row["notional_complete"] else "data_required"} for row in symbol_rows.values()]
    exposure.sort(key=lambda row: (-row["open_positions"], row["symbol"]))
    with _conn() as con:
        open_cases = con.execute("SELECT COUNT(*) FROM investigation_cases WHERE status NOT IN ('resolved','closed')").fetchone()[0]
        audit_rows = con.execute("SELECT id,actor,action,entity_type,entity_id,details_json,created_at FROM audit_log ORDER BY id DESC LIMIT 20").fetchall()
        persisted_risk_rows = con.execute("SELECT event_id,detector,login,symbol,severity,source,source_generation,first_seen_at,evidence_json FROM risk_events ORDER BY first_seen_at DESC LIMIT 20").fetchall()
        recent_risk_rows = con.execute("SELECT * FROM risk_events ORDER BY last_seen_at DESC LIMIT 200").fetchall()
    changed = [{"event_id": f"audit-{row['id']}", "timestamp": row["created_at"], "event_type": row["action"], "login": _to_int(row["entity_id"], None) if row["entity_type"] in {"account", "client"} else None, "symbol": None, "severity": "info", "source": "audit_log", "generation": None, "description": f"{row['actor']}: {row['action']} ({row['entity_type']} {row['entity_id']})".strip()} for row in audit_rows]
    changed.extend({"event_id": row["event_id"], "timestamp": row["first_seen_at"], "event_type": row["detector"], "login": row["login"], "symbol": row["symbol"], "severity": row["severity"], "source": row["source"], "generation": row["source_generation"], "description": str(_json_column(row["evidence_json"], {}).get("evidence") or row["detector"])} for row in persisted_risk_rows if row["login"] in allowed_logins)
    changed.sort(key=lambda row: str(row["timestamp"]), reverse=True)
    changed = changed[:20]
    risk_events = [_risk_event_record(row) for row in recent_risk_rows if row["login"] in allowed_logins][:30]
    top_accounts = [row for row in risk["accounts"] if row["severity"] != "normal"][:30]
    if top_accounts:
        with _conn() as con:
            for account in top_accounts:
                account["risk_events"] = con.execute("SELECT COUNT(*) FROM risk_events WHERE login=? AND status NOT IN ('RESOLVED','CLOSED')", (account["login"],)).fetchone()[0]
                account["evidence_confidence"] = "PARTIAL" if any(event.get("data_quality") == "PARTIAL" for event in risk_events if event.get("login") == account["login"]) else "VERIFIED" if account["risk_events"] else "Unavailable"
                account["last_update"] = snapshot["last_update"]
    financial = {"total_equity": round(sum(_to_float(row.get("Equity"), 0.0) for row in users), 2), "total_balance": round(sum(_to_float(row.get("Balance"), 0.0) for row in users), 2), "total_credit": round(sum(_to_float(row.get("Credit"), 0.0) for row in users), 2), "floating_pnl": round(sum(_to_float(row.get("Live Floating PnL", row.get("Floating PnL")), 0.0) for row in users), 2), "margin_used": round(sum(_to_float(row.get("Margin"), 0.0) for row in users), 2), "free_margin": round(sum(_to_float(row.get("Free Margin"), 0.0) for row in users), 2)} if currency else {key: None for key in ("total_equity", "total_balance", "total_credit", "floating_pnl", "margin_used", "free_margin")}
    return {"status": status, "source_updated_at": snapshot["last_update"], "generation": snapshot["last_update"], "data_age_seconds": round(age_seconds, 2), "selected_groups": groups, "excluded_logins": len(excluded), "currency": currency, "kpis": {"total_accounts": len(users), "active_accounts": len({_to_int(row.get("Login"), 0) for row in positions}), "net_lots": round(math.fsum(max(_to_float(row.get("Volume"), 0.0), 0.0) * (1 if _to_int(row.get("Action"), -1) == 0 else -1 if _to_int(row.get("Action"), -1) == 1 else 0) for row in positions), 8), "gross_lots": round(math.fsum(max(_to_float(row.get("Volume"), 0.0), 0.0) for row in positions), 8), "open_positions": len(positions), "critical_accounts": risk["summary"]["critical_accounts"], "high_risk_accounts": sum(row["severity"] == "high" for row in risk["accounts"]), "open_cases": open_cases, **financial}, "symbol_exposure": exposure[:200], "top_risk_accounts": top_accounts, "risk_events": risk_events, "what_changed": changed, "warnings": _dedupe_warnings(warnings, risk.get("warnings", []), ["Financial totals unavailable: selected groups have unknown or mixed deposit currencies."] if users and not currency else [], ["Some symbol notional values require MT5 contract size, current price, and currency conversion."] if any(row["gross_notional"] is None for row in exposure) else [])}


@api.post("/api/risk/exposure")
def get_exposure(req: ExposureRequest, request: Request):
    import sys
    return read_exposure(sys.modules[__name__], req, request)


@api.post("/api/risk/live")
def get_live_risk(req: LiveRiskRequest):
    import mt5_connection
    def apply_scope(payload):
        return scope_live_payload(payload, req.account_login, req.symbol, req.risk_category)

    try:
        mgr = get_configured_manager()
    except Exception as exc:
        return _empty_live_risk_snapshot(exc)
    if mgr and mgr.is_connected:
        if getattr(mgr, "native_mode", False):
            with mgr.cache_lock:
                native_ready = mgr.cache.get("status") == "connected" and not mgr.cache.get("errors") and time_module.time() - float(mgr.cache.get("last_update") or 0) <= 10
            if not native_ready:
                return _empty_live_risk_snapshot("Native MT5 source is unavailable or resynchronizing")
        catalog_snapshot = mgr.live_risk_cache_view(groups=[])
        available_groups = catalog_snapshot["groups"]
        if mt5_connection.FULL_SNAPSHOT_ENABLED and catalog_snapshot["last_update"] <= 0:
            payload = _empty_live_risk_snapshot(
                "The initial manager-2076 cache is loading. Group switching will become fast when it publishes."
            )
            payload["status"] = "warming"
            return payload
        groups, _, group_warnings = _resolve_groups_from_available(
            available_groups, req.selected_groups, req.group_filter_rules
        )
        if not req.selected_groups:
            return _empty_live_risk_snapshot(
                "Select one or more groups to start live monitoring."
            )
        if not groups:
            return _empty_live_risk_snapshot(
                group_warnings[0] if group_warnings else "No groups matched the current selection."
            )
        request_groups = _compress_complete_group_branches(groups, available_groups)
        snapshot = (mgr.live_risk_cache_view(groups=groups, active_only=True,
                    excluded_logins=req.excluded_logins, include_login=req.account_login)
                    if getattr(mgr, "native_mode", False) else mgr.live_risk_cache_view(groups=groups))
        # A published full generation includes empty groups. Membership cannot
        # prove completeness: a valid group can have zero users or positions.
        complete_generation = (mt5_connection.FULL_SNAPSHOT_ENABLED
                               and snapshot["last_update"] > 0
                               and not snapshot["errors"]
                               and time_module.time() - snapshot["last_update"] <= 30)
        if complete_generation:
            payload = build_live_risk_snapshot_from_cache(
                snapshot["users"], snapshot["positions"], groups, req.excluded_logins,
                row_limit=None, include_login=req.account_login, stable_order=True,
            )
            if snapshot.get("portfolio_summary") is not None:
                payload["summary"].update(snapshot["portfolio_summary"])
            payload = apply_scope(payload)
            payload["warnings"] = _dedupe_warnings(
                group_warnings, payload.get("warnings", []), snapshot["errors"] or []
            )
            payload["cache"] = {"source": "manager", "last_update": snapshot["last_update"]}
            payload["source_updated_at"] = snapshot["last_update"]
            payload["data_age_seconds"] = round(max(0.0, time_module.time() - snapshot["last_update"]), 2)
            payload["status"] = "connected"
            payload["source"] = {
                "server": mgr.server,
                "manager_login": mgr.manager_login,
                "selected_groups": groups,
            }
            notify_live_risk_events(payload.get("accounts", []), payload.get("positions", []))
            return _apply_live_risk_notices(payload)
        # Large group branches can contain more than 100k accounts. Reuse the
        # completed, scope-exact result briefly instead of monopolizing the SDK
        # with an identical minute-long scan on every browser poll.
        cache_key = (
            mgr.server,
            mgr.manager_login,
            tuple(groups),
            tuple(sorted(req.excluded_logins)),
            req.account_login, req.symbol, req.risk_category,
        )
        with _LIVE_RISK_CACHE_LOCK:
            cached = _LIVE_RISK_CACHE.get(cache_key)
            compute_lock = _LIVE_RISK_KEY_LOCKS.setdefault(cache_key, threading.Lock())
        cache_ttl = _live_risk_scope_cache_ttl(groups, cached[1] if cached else None)
        if cached and time_module.monotonic() - cached[0] < cache_ttl:
            payload = copy.deepcopy(cached[1])
            payload["source_updated_at"] = payload.get("generated_at")
            payload["data_age_seconds"] = round(time_module.monotonic() - cached[0], 2)
            payload["cache"] = {
                "source": "focused_scope_cache",
                "age_seconds": round(time_module.monotonic() - cached[0], 1),
                "ttl_seconds": cache_ttl,
            }
            payload["source"] = {
                "server": mgr.server,
                "manager_login": mgr.manager_login,
                "selected_groups": groups,
            }
            return _apply_live_risk_notices(payload)
        with compute_lock:
            with _LIVE_RISK_CACHE_LOCK:
                cached = _LIVE_RISK_CACHE.get(cache_key)
            cache_ttl = _live_risk_scope_cache_ttl(groups, cached[1] if cached else None)
            if cached and time_module.monotonic() - cached[0] < cache_ttl:
                payload = copy.deepcopy(cached[1])
                payload["source_updated_at"] = payload.get("generated_at")
                payload["data_age_seconds"] = round(time_module.monotonic() - cached[0], 2)
                payload["cache"] = {
                    "source": "focused_scope_cache",
                    "age_seconds": round(time_module.monotonic() - cached[0], 1),
                    "ttl_seconds": cache_ttl,
                }
                payload["source"] = {
                    "server": mgr.server,
                    "manager_login": mgr.manager_login,
                    "selected_groups": groups,
                }
                return _apply_live_risk_notices(payload)
            # Use the one configured persistent Manager. A focused request is
            # allowed before the optional account-wide cache has completed.
            with mgr.api_lock:
                if mgr.api is None:
                    return _empty_live_risk_snapshot("MT5 Manager is reconnecting.")
                live_started = time_module.perf_counter()
                payload = build_live_risk_snapshot(
                    mgr.api,
                    groups,
                    req.excluded_logins,
                    request_groups=request_groups,
                    row_limit=None, include_login=req.account_login, stable_order=True,
                )
                payload = apply_scope(payload)
            logger.info(
                "Focused live risk loaded groups=%s requests=%s scoped_accounts=%s positions=%s elapsed_ms=%.1f",
                len(groups),
                request_groups,
                payload.get("summary", {}).get("scoped_accounts", 0),
                payload.get("summary", {}).get("open_positions", 0),
                (time_module.perf_counter() - live_started) * 1000.0,
            )
            cache_ttl = _live_risk_scope_cache_ttl(groups, payload)
            with _LIVE_RISK_CACHE_LOCK:
                if len(_LIVE_RISK_CACHE) >= 16:
                    oldest = min(_LIVE_RISK_CACHE, key=lambda key: _LIVE_RISK_CACHE[key][0])
                    _LIVE_RISK_CACHE.pop(oldest, None)
                    _LIVE_RISK_KEY_LOCKS.pop(oldest, None)
                _LIVE_RISK_CACHE[cache_key] = (
                    time_module.monotonic(), copy.deepcopy(payload)
                )
        payload["warnings"] = _dedupe_warnings(group_warnings, payload.get("warnings", []))
        payload["source_updated_at"] = payload.get("generated_at")
        payload["data_age_seconds"] = 0.0
        payload["cache"] = {
            "source": "focused_manager_request",
            "ttl_seconds": cache_ttl,
        }
        payload["source"] = {
            "server": mgr.server,
            "manager_login": mgr.manager_login,
            "selected_groups": groups,
        }
        notify_live_risk_events(payload.get("accounts", []), payload.get("positions", []))
        return _apply_live_risk_notices(payload)

    try:
        with fast_manager_session(req.server, req.login, req.password) as api_handle:
            groups, _, group_warnings = _resolve_groups_for_request(
                api_handle, req.selected_groups, req.group_filter_rules
            )
            if not groups:
                return {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "selected_groups": [],
                    "summary": {},
                    "accounts": [],
                    "symbols": [],
                    "positions": [],
                    "network": {"map_points": [], "providers": [], "countries": [], "shared_clusters": []},
                    "warnings": group_warnings or ["No groups matched the current selection."],
                }
            payload = build_live_risk_snapshot(api_handle, groups, req.excluded_logins,
                                               row_limit=None, include_login=req.account_login, stable_order=True)
            payload = apply_scope(payload)
    except HTTPException as exc:
        return _empty_live_risk_snapshot(exc.detail)
    payload["warnings"] = _dedupe_warnings(group_warnings, payload.get("warnings", []))
    payload["source_updated_at"] = payload.get("generated_at")
    payload["data_age_seconds"] = 0.0
    notify_live_risk_events(payload.get("accounts", []), payload.get("positions", []))
    return _apply_live_risk_notices(payload)


def _apply_live_risk_notices(payload: dict[str, Any]) -> dict[str, Any]:
    """Hide noticed activity for 48h while assigning stable UI action keys."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    with _conn() as con:
        noticed = {
            row["alert_key"] for row in con.execute(
                "SELECT alert_key FROM alert_acknowledgements WHERE acknowledged_at >= ?",
                (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
            ).fetchall()
        }
    hidden = 0
    for account in payload.get("accounts", []):
        if account.get("severity") == "normal":
            continue
        key = _alert_key(
            "live-risk", account.get("login"), account.get("severity"),
            "|".join(sorted(str(signal) for signal in account.get("signals", []))),
        )
        account["alert_key"] = key
        if key in noticed:
            account["noticed"] = True
            hidden += 1
    payload["noticed_hidden"] = hidden
    return payload


@api.post("/api/risk/alerts/notice")
def notice_live_risk_alert(req: AlertAcknowledgeRequest, request: Request):
    actor = str(request.state.user.get("username", "system"))
    with _conn() as con:
        con.execute(
            """INSERT INTO alert_acknowledgements(alert_key,actor,acknowledged_at)
               VALUES (?,?,datetime('now')) ON CONFLICT(alert_key) DO UPDATE SET
               actor=excluded.actor, acknowledged_at=datetime('now')""",
            (req.alert_key, actor),
        )
    write_audit(actor, "risk_alert.noticed", "live_risk_alert", req.alert_key)
    return {"ok": True, "hidden_hours": 48}


@api.post("/api/risk/performance")
def get_risk_performance(req: PerformanceRequest):
    import mt5_connection

    manager = mt5_connection.get_manager()
    focused = not mt5_connection.FULL_SNAPSHOT_ENABLED
    if not manager or not manager.is_connected:
        return {
            "status": "warming", "overall_window_days": req.lookback_days,
            "periods": {}, "data_quality": {},
            "warnings": ["MT5 Manager is connecting."],
            "cache": {"hit": False, "refreshing": True},
        }
    if focused:
        available_groups = manager.group_cache_view()
        cached_users = None
    else:
        snapshot = manager.snapshot()
        available_groups = snapshot.groups
        cached_users = snapshot.users
        if snapshot.last_update <= 0 or not cached_users:
            return {
                "status": "warming", "overall_window_days": req.lookback_days,
                "periods": {}, "data_quality": {},
                "warnings": ["MT5 account cache is initializing."],
                "cache": {"hit": False, "refreshing": True},
            }
    if not available_groups:
        return {
            "status": "warming", "overall_window_days": req.lookback_days,
            "periods": {}, "data_quality": {},
            "warnings": ["MT5 group catalog is initializing."],
            "cache": {"hit": False, "refreshing": True},
        }
    cache_key = _performance_cache_key(req)
    cached = _get_cached_performance(cache_key)
    if cached is not None:
        return cached
    with _PERFORMANCE_CACHE_LOCK:
        should_start = cache_key not in _PERFORMANCE_REFRESHING
        if should_start:
            _PERFORMANCE_REFRESHING.add(cache_key)
    if should_start:
        def compute_performance() -> None:
            try:
                with _PERFORMANCE_COMPUTE_LOCK:
                    if _get_cached_performance(cache_key) is not None:
                        return
                    with yielding_history_manager_session() as api_handle:
                        groups, _, group_warnings = (
                            _resolve_groups_from_available(
                                available_groups, req.selected_groups, req.group_filter_rules
                            )
                            if available_groups
                            else _resolve_groups_for_request(
                                api_handle, req.selected_groups, req.group_filter_rules
                            )
                        )
                        if not groups:
                            return
                        request_groups = _compress_complete_group_branches(
                            groups, available_groups
                        ) if focused else None
                        horizons = list(dict.fromkeys(
                            horizon for horizon in (1, req.lookback_days)
                            if horizon <= req.lookback_days
                        ))
                        for horizon in horizons:
                            scan_started = time_module.perf_counter()
                            payload = build_performance_snapshot(
                                api_handle, groups, req.excluded_logins,
                                horizon, cached_users,
                                request_groups=request_groups,
                            )
                            payload["overall_window_days"] = req.lookback_days
                            payload["warnings"] = _dedupe_warnings(
                                group_warnings, payload.get("warnings", [])
                            )
                            payload["data_quality"]["history_days_loaded"] = horizon
                            payload["data_quality"]["history_complete"] = horizon == req.lookback_days
                            payload["cache"] = {
                                "hit": False, "ttl_seconds": PERFORMANCE_CACHE_SECONDS,
                                "progressive": horizon != req.lookback_days,
                            }
                            _store_cached_performance(cache_key, payload)
                            logger.info(
                                "Realized P&L scan published groups=%s days=%s deals=%s elapsed_ms=%.1f",
                                len(groups), horizon,
                                payload.get("data_quality", {}).get("deals_returned", 0),
                                (time_module.perf_counter() - scan_started) * 1000.0,
                            )
            except Exception:
                logger.exception("Background P&L leaderboard scan failed")
            finally:
                with _PERFORMANCE_CACHE_LOCK:
                    _PERFORMANCE_REFRESHING.discard(cache_key)

        threading.Thread(target=compute_performance, daemon=True).start()
    return {
        "status": "warming",
        "overall_window_days": req.lookback_days,
        "periods": {},
        "data_quality": {},
        "warnings": [],
        "cache": {"hit": False, "refreshing": True},
    }


def _google_sheet_cell(value: Any) -> str | int | float | bool:
    """Use RAW values and neutralize spreadsheet formulas from MT5/user data."""
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def _google_sheet_rows(headers: list[str], rows: list[list[Any]]) -> list[list[Any]]:
    return [headers, *[[_google_sheet_cell(cell) for cell in row] for row in rows]]


def _google_unique_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Keep one current row per stable MT5 identity, preferring higher risk."""
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "").strip()
        if not value:
            continue
        previous = unique.get(value)
        if previous is None or _to_float(row.get("risk_score"), 0) >= _to_float(previous.get("risk_score"), 0):
            unique[value] = row
    return list(unique.values())


def _record_google_toxic_observations(accounts: list[dict[str, Any]], snapshot_at: str) -> list[dict[str, Any]]:
    """Count toxic observations once per distinct live snapshot, not per re-sync."""
    toxic = [
        row for row in accounts
        if str(row.get("severity") or "").lower() in {"high", "critical"}
        or _to_float(row.get("risk_score"), 0) >= 50
    ]
    with _conn() as con:
        for row in toxic:
            login = _to_int(row.get("login"), 0)
            if login <= 0:
                continue
            con.execute(
                """INSERT INTO google_sheets_toxic_history
                   (login,client_name,account_group,last_risk_score,last_severity,toxic_reason,observations,last_seen,last_snapshot_at)
                   VALUES (?,?,?,?,?,?,1,datetime('now'),?)
                   ON CONFLICT(login) DO UPDATE SET
                     client_name=excluded.client_name, account_group=excluded.account_group,
                     last_risk_score=excluded.last_risk_score, last_severity=excluded.last_severity,
                     toxic_reason=excluded.toxic_reason,
                     observations=CASE WHEN google_sheets_toxic_history.last_snapshot_at=excluded.last_snapshot_at
                         THEN google_sheets_toxic_history.observations ELSE google_sheets_toxic_history.observations+1 END,
                     last_seen=datetime('now'), last_snapshot_at=excluded.last_snapshot_at""",
                (
                    login,
                    str(row.get("name") or ""),
                    str(row.get("group") or ""),
                    _to_float(row.get("risk_score"), 0),
                    str(row.get("severity") or "normal"),
                    " | ".join(dict.fromkeys(str(signal).strip() for signal in (row.get("signals") or []) if str(signal).strip())),
                    snapshot_at,
                ),
            )
        history = [dict(row) for row in con.execute(
            "SELECT * FROM google_sheets_toxic_history WHERE observations > 0 ORDER BY observations DESC,last_risk_score DESC,login"
        ).fetchall()]
    return history


def _google_daily_leaders(api_handle, groups: list[str]) -> dict[str, Any]:
    """Return today's realized rankings without allowing history issues to block a sheet sync."""
    if not groups:
        return {"winners": [], "losers": [], "net_pnl": 0.0, "trades": 0, "warning": "No groups selected"}
    try:
        # An exact group history request is authoritative for the selected
        # sheet scope. It avoids partial rankings when a Manager bulk-login
        # request truncates a busy account's deal stream.
        performance = build_performance_snapshot(
            api_handle, groups, lookback_days=1, request_groups=groups
        )
        daily = performance.get("periods", {}).get("daily", {})
        quality = performance.get("data_quality", {})
        warnings = list(performance.get("warnings") or [])
        return {
            "winners": list(daily.get("winners") or []),
            "losers": list(daily.get("losers") or []),
            "net_pnl": _to_float(daily.get("net_pnl"), 0.0),
            "trades": _to_int(quality.get("closing_deals"), 0),
            "warning": " | ".join(warnings),
            "verification": "MT5 Manager deal history" if not warnings else "MT5 Manager deal history with retrieval warnings",
        }
    except Exception as exc:
        logger.warning("Google Sheets daily leaderboard unavailable: %s", exc)
        return {"winners": [], "losers": [], "net_pnl": 0.0, "trades": 0, "warning": "Daily realized P&L could not be loaded", "verification": "Unavailable"}


def _google_vps_review_status(row: dict[str, Any]) -> tuple[str, str]:
    """Keep public network enrichment clearly separate from MT5-confirmed facts."""
    confidence = _to_int(row.get("vps_confidence"), 0)
    if row.get("vps_status") == "detected" and confidence >= 75:
        return "Hosting/VPS candidate", "External intelligence — dealer confirmation required"
    if row.get("vps_status") == "detected":
        return "Possible hosting network", "External intelligence — low confidence"
    return "Not classified as hosting", "External intelligence — not proof of device type"


def _sync_google_sheet(snapshot: dict[str, Any], daily_leaders: dict[str, Any] | None = None) -> dict[str, Any]:
    if os.getenv("GOOGLE_SHEETS_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=400, detail="Google Sheets sync is disabled. Set GOOGLE_SHEETS_ENABLED=true in .env.")
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
    credential_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if not spreadsheet_id or not credential_file:
        raise HTTPException(status_code=400, detail="Google Sheets is not configured. Set GOOGLE_SHEETS_SPREADSHEET_ID and GOOGLE_SERVICE_ACCOUNT_FILE in .env.")
    if not Path(credential_file).is_file():
        raise HTTPException(status_code=400, detail="The Google service-account JSON file was not found on this server.")
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Google Sheets packages are not installed. Run pip install -r requirements.txt.") from exc

    accounts = _google_unique_rows(list(snapshot.get("accounts") or []), "login")
    positions = _google_unique_rows(list(snapshot.get("positions") or []), "ticket")
    symbols = _google_unique_rows(list(snapshot.get("symbols") or []), "symbol")
    vps_accounts = [row for row in accounts if row.get("vps_status") == "detected"]
    profitable_accounts = sorted((row for row in accounts if _to_float(row.get("floating_pnl"), 0.0) > 0), key=lambda row: _to_float(row.get("floating_pnl"), 0.0), reverse=True)
    flagged_accounts = [row for row in accounts if row.get("severity") != "normal"]
    generated_at = str(snapshot.get("generated_at") or datetime.now(timezone.utc).isoformat())
    selected_groups = ", ".join(str(group) for group in (snapshot.get("selected_groups") or []))
    summary = snapshot.get("summary") or {}
    toxic_history = _record_google_toxic_observations(accounts, generated_at)
    toxic_rows = [row for row in toxic_history if _to_int(row.get("observations"), 0) >= 3]
    daily_leaders = daily_leaders or {}
    daily_winners = list(daily_leaders.get("winners") or [])
    daily_losers = list(daily_leaders.get("losers") or [])
    big_lot_positions = list(snapshot.get("large_positions") or [])
    source = snapshot.get("source") or {}
    mt5_source = "MT5 Manager live snapshot"
    daily_source = str(daily_leaders.get("verification") or "MT5 Manager deal history")
    tabs = {
        "RMS Summary": _google_sheet_rows(["Metric", "Value"], [["Last sync (UTC)", generated_at], ["Selected groups", selected_groups], ["Scoped accounts", summary.get("scoped_accounts", 0)], ["Open positions", len(positions)], ["Big-lot positions", len(big_lot_positions)], ["Hosting/VPS candidates (external)", len(vps_accounts)], ["Live profitable traders", len(profitable_accounts)], ["Today's realized P&L (UTC)", daily_leaders.get("net_pnl", 0)], ["Today's closed trades", daily_leaders.get("trades", 0)], ["Flagged risk accounts", len(flagged_accounts)], ["Frequently toxic traders", len(toxic_rows)], ["Duplicate rows removed", len(snapshot.get("accounts") or []) - len(accounts)], ["Daily-ranking status", daily_leaders.get("warning") or "Ready"]]),
        "RMS Accounts": _google_sheet_rows(["Login", "Name", "Group", "Balance", "Equity", "Floating P&L", "Margin level", "Open positions", "Gross lots", "Risk score", "Severity", "Hosting/VPS classification", "IP", "Provider", "Signals", "MT5 data source", "Synced at (UTC)"], [[row.get("login"), row.get("name"), row.get("group"), row.get("balance"), row.get("equity"), row.get("floating_pnl"), row.get("margin_level"), row.get("positions"), row.get("gross_lots"), row.get("risk_score"), row.get("severity"), _google_vps_review_status(row)[0], row.get("last_ip"), row.get("provider"), " | ".join(row.get("signals") or []), mt5_source, generated_at] for row in accounts]),
        "RMS VPS Users": _google_sheet_rows(["Login", "Name", "Group", "MT5 LastIP", "Provider", "Location", "Hosting/VPS classification", "Confidence", "Evidence", "Verification", "Risk severity", "Signals", "Synced at (UTC)"], [[row.get("login"), row.get("name"), row.get("group"), row.get("last_ip"), row.get("provider"), row.get("location"), _google_vps_review_status(row)[0], row.get("vps_confidence"), " | ".join(row.get("vps_evidence") or []), _google_vps_review_status(row)[1], row.get("severity"), " | ".join(row.get("signals") or []), generated_at] for row in vps_accounts]),
        "RMS High Profit": _google_sheet_rows(["Login", "Name", "Group", "Balance", "Equity", "Floating P&L", "Profit multiple", "Open positions", "Risk severity", "Synced at (UTC)"], [[row.get("login"), row.get("name"), row.get("group"), row.get("balance"), row.get("equity"), row.get("floating_pnl"), row.get("profit_multiple"), row.get("positions"), row.get("severity"), generated_at] for row in profitable_accounts]),
        "RMS Risk Watchlist": _google_sheet_rows(["Login", "Name", "Group", "Risk score", "Severity", "Floating P&L", "Margin level", "Gross lots", "VPS status", "Signals", "Synced at (UTC)"], [[row.get("login"), row.get("name"), row.get("group"), row.get("risk_score"), row.get("severity"), row.get("floating_pnl"), row.get("margin_level"), row.get("gross_lots"), row.get("vps_status"), " | ".join(row.get("signals") or []), generated_at] for row in flagged_accounts]),
        "RMS Toxic Traders": _google_sheet_rows(["Login", "Name", "Group", "Toxic observations", "Last risk score", "Last severity", "Toxic reason", "First seen", "Last seen", "Priority"], [[row.get("login"), row.get("client_name"), row.get("account_group"), row.get("observations"), row.get("last_risk_score"), row.get("last_severity"), row.get("toxic_reason") or "Risk threshold exceeded", row.get("first_seen"), row.get("last_seen"), "FREQUENT TOXIC" if _to_int(row.get("observations"), 0) >= 3 else "Watch"] for row in toxic_history]),
        "RMS Daily Winners": _google_sheet_rows(["Rank", "Login", "Name", "Group", "Realized P&L (UTC today)", "Closed trades", "Verification", "As of (UTC)"], [[index, row.get("login"), row.get("name"), row.get("group"), row.get("pnl"), row.get("trades"), daily_source, generated_at] for index, row in enumerate(daily_winners, start=1)]),
        "RMS Daily Losers": _google_sheet_rows(["Rank", "Login", "Name", "Group", "Realized P&L (UTC today)", "Closed trades", "Verification", "As of (UTC)"], [[index, row.get("login"), row.get("name"), row.get("group"), row.get("pnl"), row.get("trades"), daily_source, generated_at] for index, row in enumerate(daily_losers, start=1)]),
        "RMS Big Lot Alerts": _google_sheet_rows(["Ticket", "Login", "Name", "Group", "Symbol", "Side", "Lots", "Floating P&L", "Opened at (UTC)", "Alert reason", "Synced at (UTC)"], [[row.get("ticket"), row.get("login"), row.get("name"), row.get("group"), row.get("symbol"), row.get("action"), row.get("volume"), row.get("profit"), row.get("opened_at"), f"Position at or above {summary.get('high_lot_threshold', HIGH_LOT_ALERT_THRESHOLD):g} lots", generated_at] for row in big_lot_positions]),
        "RMS Open Positions": _google_sheet_rows(["Ticket", "Login", "Name", "Group", "Symbol", "Side", "Lots", "Open price", "Current price", "Floating P&L", "Opened at (UTC)", "Age seconds", "Synced at (UTC)"], [[row.get("ticket"), row.get("login"), row.get("name"), row.get("group"), row.get("symbol"), row.get("action"), row.get("volume"), row.get("price_open"), row.get("price_current"), row.get("profit"), row.get("opened_at"), row.get("age_seconds"), generated_at] for row in positions]),
        "RMS Symbol Exposure": _google_sheet_rows(["Symbol", "Buy lots", "Sell lots", "Net lots", "Gross lots", "Floating P&L", "Open positions", "Synced at (UTC)"], [[row.get("symbol"), row.get("buy_lots"), row.get("sell_lots"), row.get("net_lots"), row.get("gross_lots"), row.get("floating_pnl"), row.get("positions"), generated_at] for row in symbols]),
        "RMS Data Quality": _google_sheet_rows(["Dataset", "Source", "Verification status", "Important note", "As of (UTC)"], [["Accounts / balances / equity / positions", mt5_source, "Direct MT5 data", "Exact at snapshot time; live values can change after sync.", generated_at], ["Daily winners / losers", daily_source, "Direct MT5 closed-deal data" if not daily_leaders.get("warning") else "Review retrieval warning", daily_leaders.get("warning") or "Closed deals, commission, swap and fees are included.", generated_at], ["Hosting / VPS candidates", "MT5 LastIP + ip-api.com public network intelligence", "External classification — review required", "An IP's provider/network is not proof that a trader used a VPS.", generated_at], ["Toxic-trader watchlist", "RMS rules applied to MT5 live snapshot", "Rule-based risk signal", "A risk alert requires dealer review; it is not a fraud verdict.", generated_at], ["Big-lot alerts", mt5_source, "Direct MT5 open position data", f"Threshold: {summary.get('high_lot_threshold', HIGH_LOT_ALERT_THRESHOLD):g} lots.", generated_at], ["Export scope", str(source.get("server") or "MT5 Manager"), "Configured selected groups", selected_groups or "No group scope", generated_at]]),
    }
    tab_colors = {
        "RMS Summary": {"red": 0.10, "green": 0.20, "blue": 0.35},
        "RMS Accounts": {"red": 0.18, "green": 0.36, "blue": 0.62},
        "RMS VPS Users": {"red": 0.38, "green": 0.25, "blue": 0.62},
        "RMS High Profit": {"red": 0.05, "green": 0.48, "blue": 0.35},
        "RMS Risk Watchlist": {"red": 0.78, "green": 0.42, "blue": 0.08},
        "RMS Toxic Traders": {"red": 0.72, "green": 0.12, "blue": 0.16},
        "RMS Daily Winners": {"red": 0.05, "green": 0.48, "blue": 0.35},
        "RMS Daily Losers": {"red": 0.72, "green": 0.12, "blue": 0.16},
        "RMS Big Lot Alerts": {"red": 0.78, "green": 0.42, "blue": 0.08},
        "RMS Open Positions": {"red": 0.12, "green": 0.48, "blue": 0.64},
        "RMS Symbol Exposure": {"red": 0.34, "green": 0.40, "blue": 0.47},
        "RMS Data Quality": {"red": 0.20, "green": 0.36, "blue": 0.50},
    }
    try:
        credentials = Credentials.from_service_account_file(credential_file, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)").execute()
        existing = {sheet["properties"]["title"] for sheet in metadata.get("sheets", [])}
        missing = [title for title in tabs if title not in existing]
        if missing:
            service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [{"addSheet": {"properties": {"title": title}}} for title in missing]}).execute()
            metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)").execute()
        service.spreadsheets().values().batchClear(spreadsheetId=spreadsheet_id, body={"ranges": [f"'{title}'!A:Z" for title in tabs]}).execute()
        service.spreadsheets().values().batchUpdate(spreadsheetId=spreadsheet_id, body={"valueInputOption": "RAW", "data": [{"range": f"'{title}'!A1", "values": rows} for title, rows in tabs.items()]}).execute()
        sheet_ids = {sheet["properties"]["title"]: sheet["properties"]["sheetId"] for sheet in metadata.get("sheets", [])}
        format_requests = []
        for title, rows in tabs.items():
            sheet_id = sheet_ids[title]
            format_requests.extend([
                {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "tabColor": tab_colors[title], "gridProperties": {"frozenRowCount": 1}}, "fields": "tabColor,gridProperties.frozenRowCount"}},
                {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1}, "properties": {"pixelSize": 110}, "fields": "pixelSize"}},
                {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": len(rows[0])}, "properties": {"pixelSize": 150}, "fields": "pixelSize"}},
                {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(rows[0])}, "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.10, "green": 0.20, "blue": 0.35}, "horizontalAlignment": "LEFT", "verticalAlignment": "MIDDLE", "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True, "fontSize": 10}}}, "fields": "userEnteredFormat"}},
                {"setBasicFilter": {"filter": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": len(rows), "startColumnIndex": 0, "endColumnIndex": len(rows[0])}}}},
            ])
        toxic_sheet_id = sheet_ids["RMS Toxic Traders"]
        for index, row in enumerate(toxic_history, start=1):
            observations = _to_int(row.get("observations"), 0)
            color = {"red": 0.95, "green": 0.75, "blue": 0.75} if observations >= 3 else {"red": 1.0, "green": 0.93, "blue": 0.70}
            is_frequent = observations >= 3
            format_requests.append({"repeatCell": {"range": {"sheetId": toxic_sheet_id, "startRowIndex": index, "endRowIndex": index + 1, "startColumnIndex": 0, "endColumnIndex": len(tabs["RMS Toxic Traders"][0])}, "cell": {"userEnteredFormat": {"backgroundColor": color, "textFormat": {"foregroundColor": {"red": 0.45, "green": 0.03, "blue": 0.05} if is_frequent else {"red": 0.38, "green": 0.22, "blue": 0.02}, "bold": is_frequent}}}, "fields": "userEnteredFormat"}})
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": format_requests}).execute()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Google Sheets sync failed")
        raise HTTPException(status_code=502, detail="Google Sheets rejected the sync. Confirm that the service account has Editor access to the spreadsheet.") from exc
    return {"ok": True, "spreadsheet_id": spreadsheet_id, "synced_at": generated_at, "tabs": {"accounts": len(accounts), "vps_users": len(vps_accounts), "high_profit": len(profitable_accounts), "risk_watchlist": len(flagged_accounts), "toxic_traders": len(toxic_history), "frequently_toxic": len(toxic_rows), "daily_winners": len(daily_winners), "daily_losers": len(daily_losers), "big_lot_alerts": len(big_lot_positions), "open_positions": len(positions), "symbol_exposure": len(symbols)}}


def _google_sheets_auto_sync_scope(manager) -> list[str]:
    configured = [item.strip() for item in os.getenv("GOOGLE_SHEETS_AUTO_SYNC_GROUPS", "").split(",") if item.strip()]
    requested = configured or [os.getenv("MT5_DEFAULT_LIVE_GROUP", "").strip()]
    requested = [group for group in requested if group]
    if requested:
        # The configured scope is deliberate. Do not replace it with a stale
        # browser cache branch merely because the catalog is still warming.
        return list(dict.fromkeys(requested))
    # If no explicit default is configured, reuse the last scope selected in
    # the live dashboard rather than risking an account-wide Manager scan.
    with _LIVE_RISK_CACHE_LOCK:
        cached = max(_LIVE_RISK_CACHE.values(), key=lambda item: item[0], default=None)
    return list(cached[1].get("selected_groups") or []) if cached else []


def _run_google_sheets_auto_sync() -> bool:
    enabled = os.getenv("GOOGLE_SHEETS_AUTO_SYNC_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled or os.getenv("GOOGLE_SHEETS_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    import mt5_connection

    manager = mt5_connection.get_manager()
    if not manager or not manager.is_connected:
        return False
    groups = _google_sheets_auto_sync_scope(manager)
    if not groups:
        logger.info("Google Sheets auto-sync is waiting for a configured live group.")
        return False
    catalog = manager.live_risk_cache_view(groups=[]).get("groups") or []
    request_groups = _compress_complete_group_branches(groups, catalog)
    with manager.api_lock:
        if manager.api is None:
            return False
        payload = build_live_risk_snapshot(manager.api, groups, request_groups=request_groups)
        daily_leaders = _google_daily_leaders(manager.api, groups)
    if not payload.get("summary", {}).get("scoped_accounts"):
        logger.warning(
            "Google Sheets auto-sync skipped: selected MT5 scope returned no accounts; preserving the previous export."
        )
        return False
    payload["source"] = {"server": manager.server, "manager_login": manager.manager_login, "selected_groups": groups}
    with _GOOGLE_SHEETS_SYNC_LOCK:
        result = _sync_google_sheet(payload, daily_leaders)
    logger.info("Google Sheets auto-sync complete: accounts=%s positions=%s toxic=%s", result["tabs"]["accounts"], result["tabs"]["open_positions"], result["tabs"]["frequently_toxic"])
    return True


def _google_sheets_auto_sync_supervisor() -> None:
    # Let the Manager connection establish first; automatic exports never block
    # the API request path and are intentionally bounded to a chosen scope.
    if _RUNTIME_MAINTENANCE_STOP.wait(20):
        return
    while not _RUNTIME_MAINTENANCE_STOP.is_set():
        try:
            _run_google_sheets_auto_sync()
        except Exception:
            logger.exception("Google Sheets automatic sync failed")
        wait_seconds = max(int(os.getenv("GOOGLE_SHEETS_AUTO_SYNC_SECONDS", "300")), 60)
        if _RUNTIME_MAINTENANCE_STOP.wait(wait_seconds):
            return


@api.get("/api/integrations/google-sheets/status")
def google_sheets_status(request: Request):
    _risk_admin_actor(request)
    enabled = os.getenv("GOOGLE_SHEETS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    credential_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    return {"enabled": enabled, "configured": bool(os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip() and credential_file and Path(credential_file).is_file()), "spreadsheet_id": os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()}


@api.post("/api/integrations/google-sheets/sync")
def sync_google_sheets(req: GoogleSheetsSyncRequest, request: Request):
    actor = _risk_admin_actor(request)
    if not req.live_snapshot.get("generated_at"):
        raise HTTPException(status_code=400, detail="Load a live-risk snapshot before syncing Google Sheets.")
    daily_leaders: dict[str, Any] | None = None
    try:
        manager = get_configured_manager()
        groups = list(req.live_snapshot.get("selected_groups") or [])
        if manager and manager.is_connected and groups:
            with manager.api_lock:
                if manager.api is not None:
                    daily_leaders = _google_daily_leaders(manager.api, groups)
    except Exception as exc:
        logger.warning("Manual Google Sheets daily leaderboard unavailable: %s", exc)
    with _GOOGLE_SHEETS_SYNC_LOCK:
        result = _sync_google_sheet(req.live_snapshot, daily_leaders)
    write_audit(actor, "integrations.google_sheets_synced", "google_sheets", result["spreadsheet_id"], result["tabs"])
    return result


class NewsPushSettings(BaseModel):
    enabled: bool = False
    minimum_profit: float = Field(default=100, gt=0, allow_inf_nan=False)
    currency: str = Field(default="USD", pattern="^[A-Z]{3,6}$")
    message: str = Field(min_length=1, max_length=1024)


@api.get("/api/notifications/news-push")
def news_push_status(request: Request):
    _risk_admin_actor(request)
    return NEWS_PUSH.status()


@api.put("/api/notifications/news-push")
def update_news_push(req: NewsPushSettings, request: Request):
    actor = _risk_admin_actor(request)
    try:
        result = NEWS_PUSH.save(req.model_dump(), actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(actor, "settings.news_push_updated", "notifications", "news-push",
                {"enabled": req.enabled, "minimum_profit": req.minimum_profit, "currency": req.currency})
    return result


class NewsPushTestRequest(BaseModel):
    login: int = Field(gt=0, le=9223372036854775807)
    message: str = Field(default="RMS push notification test.", min_length=1, max_length=1024)
    request_id: UUID

    @field_validator("login", mode="before")
    @classmethod
    def validate_login(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("Enter an MT5 login using digits only")
        if isinstance(value, str) and not value.isdecimal():
            raise ValueError("Enter an MT5 login using digits only")
        return value


@api.post("/api/notifications/news-push/test")
def send_news_push_test(req: NewsPushTestRequest, request: Request):
    actor = _risk_admin_actor(request)
    try:
        result = NEWS_PUSH.send_test(req.login, req.message, actor, req.request_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(actor, "notification.news_push_test", "account", req.login,
                {"request_id": str(req.request_id), "status": result["status"]})
    return result


@api.get("/api/notifications/telegram/status")
def telegram_status():
    return {
        "configured": bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip()),
        "bot_token": bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip()),
        "chat_id": bool(os.getenv("TELEGRAM_CHAT_ID", "").strip()),
        "settings": {
            "enabled": TELEGRAM_ALERTS_ENABLED,
            "risk_alerts": TELEGRAM_RISK_ALERTS_ENABLED,
            "antifraud_alerts": TELEGRAM_ANTIFRAUD_ALERTS_ENABLED,
            "profit_alert_pct": PROFIT_ALERT_PCT_THRESHOLD,
            "double_profit_alert_pct": DOUBLE_PROFIT_PCT_THRESHOLD,
            "high_lot_threshold": HIGH_LOT_ALERT_THRESHOLD,
            "account_lot_threshold": ACCOUNT_LOT_ALERT_THRESHOLD,
            "channel_routes": {
                route: os.getenv(env_name, "").strip()
                for route, env_name in TELEGRAM_ROUTE_ENV.items()
            },
        },
    }


@api.put("/api/notifications/telegram/settings")
def update_telegram_settings(req: TelegramAlertSettings, request: Request):
    global TELEGRAM_ALERTS_ENABLED, TELEGRAM_RISK_ALERTS_ENABLED
    global TELEGRAM_ANTIFRAUD_ALERTS_ENABLED, PROFIT_ALERT_PCT_THRESHOLD
    global DOUBLE_PROFIT_PCT_THRESHOLD, HIGH_LOT_ALERT_THRESHOLD, ACCOUNT_LOT_ALERT_THRESHOLD

    _risk_admin_actor(request)
    if req.double_profit_alert_pct < req.profit_alert_pct:
        raise HTTPException(status_code=400, detail="Double-profit threshold must be at least the profit threshold.")
    if req.account_lot_threshold < req.high_lot_threshold:
        raise HTTPException(status_code=400, detail="Account lot threshold must be at least the position lot threshold.")

    TELEGRAM_ALERTS_ENABLED = req.enabled
    TELEGRAM_RISK_ALERTS_ENABLED = req.risk_alerts
    TELEGRAM_ANTIFRAUD_ALERTS_ENABLED = req.antifraud_alerts
    PROFIT_ALERT_PCT_THRESHOLD = req.profit_alert_pct
    DOUBLE_PROFIT_PCT_THRESHOLD = req.double_profit_alert_pct
    HIGH_LOT_ALERT_THRESHOLD = req.high_lot_threshold
    ACCOUNT_LOT_ALERT_THRESHOLD = req.account_lot_threshold

    env_path = Path(__file__).with_name(".env")
    values = {
        "TELEGRAM_ALERTS_ENABLED": str(req.enabled).lower(),
        "TELEGRAM_RISK_ALERTS_ENABLED": str(req.risk_alerts).lower(),
        "TELEGRAM_ANTIFRAUD_ALERTS_ENABLED": str(req.antifraud_alerts).lower(),
        "PROFIT_ALERT_PCT_THRESHOLD": str(req.profit_alert_pct),
        "DOUBLE_PROFIT_PCT_THRESHOLD": str(req.double_profit_alert_pct),
        "HIGH_LOT_ALERT_THRESHOLD": str(req.high_lot_threshold),
        "ACCOUNT_LOT_ALERT_THRESHOLD": str(req.account_lot_threshold),
    }
    values.update({
        env_name: req.channel_routes.get(route, "")
        for route, env_name in TELEGRAM_ROUTE_ENV.items()
    })
    for key, value in values.items():
        set_key(str(env_path), key, value)
        os.environ[key] = value
    return telegram_status()


@api.get("/api/notifications/spread/symbols")
def spread_symbol_catalog(q: str = Query("", max_length=120), limit: int = Query(20, ge=1, le=100)):
    manager = _risk_manager_provider()
    if not manager or not manager.is_connected:
        raise HTTPException(status_code=503, detail="MT5 symbol catalog is reconnecting. Try again shortly.")
    with manager.cache_lock:
        catalog = manager.cache.get("symbol_cache", {})
    if not catalog:
        raise HTTPException(status_code=503, detail="MT5 symbol catalog is synchronizing. Try again shortly.")
    from services.symbol_catalog import search_symbols
    return search_symbols(catalog, q, limit)


@api.get("/api/notifications/spread/settings")
def spread_alert_status():
    return SPREAD_ALERTS.status()


@api.put("/api/notifications/spread/settings")
def update_spread_alert_settings(req: SpreadAlertSettings, request: Request):
    actor = _risk_admin_actor(request)
    if req.enabled and req.default_threshold_points is None and not req.symbol_thresholds:
        raise HTTPException(status_code=400, detail="Set a default spread threshold or at least one symbol threshold.")
    SPREAD_ALERTS.save_settings(req, actor)
    write_audit(actor, "settings.spread_alerts_updated", "spread_alert_settings", 1, req.model_dump())
    return SPREAD_ALERTS.status()


@api.post("/api/notifications/telegram/test")
def telegram_test(req: TelegramTestRequest, request: Request):
    _risk_admin_actor(request)
    ok, detail = send_telegram_notification(req.message, req.route)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True}


@api.post("/api/risk/markout")
def client_markout(req: MarkoutRequest):
    key = (_configured_manager_source_key(), req.model_dump_json())
    return MARKOUT_REPORTS.read(key, lambda: _calculate_client_markout(req))


def _calculate_client_markout(req: MarkoutRequest):
    """On-demand quote-backed markout for one scoped client; no inferred prices."""
    if req.from_date > req.to_date or (req.to_date - req.from_date).days > 6:
        raise HTTPException(status_code=400, detail="Select at most seven UTC days, including both dates.")
    if req.to_date > datetime.now(timezone.utc).date():
        raise HTTPException(status_code=400, detail="Future dates are unavailable.")
    _, _, from_ts, to_ts = _date_range_utc(req.from_date, req.to_date)
    with yielding_history_manager_session() as api_handle:
        groups, _, warnings = _resolve_groups_for_request(api_handle, req.selected_groups, req.group_filter_rules)
        if not groups:
            raise HTTPException(status_code=400, detail="Select at least one matching group.")
        users, group_map, scope_warnings = _collect_user_scope(api_handle, groups, _normalize_login_set(req.excluded_logins))
        warnings.extend(scope_warnings)
        if req.client_login not in users:
            raise HTTPException(status_code=404, detail="Client login was not found in the selected groups.")
        deals, deal_error = request_deals_by_login(api_handle, req.client_login, from_ts, to_ts)
        if deal_error:
            deals = []
            seen = set()
            for group in groups:
                group_deals, error = request_deals_by_group(api_handle, group, from_ts, to_ts)
                if error:
                    raise HTTPException(status_code=503, detail=f"Deal history unavailable for {group}: {error}")
                for deal in group_deals:
                    if _login_from_record(deal) != req.client_login:
                        continue
                    ticket = _to_int(_value(deal, "Deal", "deal", "Ticket", "ticket"), 0) or 0
                    if ticket and ticket in seen:
                        continue
                    if ticket:
                        seen.add(ticket)
                    deals.append(deal)
        try:
            executions = markout.normalize_executions(deals, req.client_login, from_ts * 1000, to_ts * 1000 + 999)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if len(executions) > 300 or len({row["symbol"] for row in executions}) > 20:
            raise HTTPException(status_code=400, detail="This range has too many executions or symbols for a quote-backed scan. Narrow the dates.")
        diagnostics = {"returned_deals": len(deals), "executed_fills": len(executions),
                       "latest_execution_date": None, "recent_lookup_status": "not_needed"}
        if not executions:
            recent_from = req.to_date - timedelta(days=6)
            diagnostics["recent_lookup_from"] = recent_from.isoformat()
            diagnostics["recent_lookup_to"] = req.to_date.isoformat()
            _, _, recent_start, recent_end = _date_range_utc(recent_from, req.to_date)
            recent, recent_error = request_deals_by_login(api_handle, req.client_login, recent_start, recent_end)
            diagnostics["recent_lookup_status"] = "unavailable" if recent_error else "complete"
            if not recent_error:
                try:
                    recent_executions = markout.normalize_executions(recent, req.client_login, recent_start * 1000, recent_end * 1000 + 999)
                    if recent_executions:
                        diagnostics["latest_execution_date"] = recent_executions[-1]["time_utc"][:10]
                except ValueError:
                    diagnostics["recent_lookup_status"] = "unavailable"
        by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in executions:
            by_symbol[row["symbol"]].append(row)
        ticks_by_symbol: dict[str, list[Any]] = {}
        for symbol, rows in by_symbol.items():
            # Merge nearby execution windows to reduce Manager calls without
            # requesting a full day of high-frequency ticks per symbol.
            windows: list[list[int]] = []
            for row in rows:
                start = max(0, row["time_ms"] // 1000 - 3)
                end = row["time_ms"] // 1000 + 903
                if windows and start <= windows[-1][1] + 5:
                    windows[-1][1] = max(windows[-1][1], end)
                else:
                    windows.append([start, end])
            ticks_by_symbol[symbol] = []
            for start, end in windows:
                ticks, error = request_tick_history(api_handle, symbol, start, end)
                if error:
                    warnings.append(f"{symbol}: tick history unavailable for {start}–{end}: {error}")
                    continue
                ticks_by_symbol[symbol].extend(ticks)
        currency = ""
        modes = {}
        try:
            group_spec = api_handle.GroupRequest(group_map[req.client_login])
            currency = str(_value(group_spec, "Currency", default="") or "").upper()
        except (AttributeError, RuntimeError, TypeError):
            pass
        for symbol in by_symbol:
            try:
                spec = api_handle.SymbolGet(symbol)
                modes[symbol] = _to_int(_value(spec, "CalcMode", default=None), -1)
            except (AttributeError, RuntimeError, TypeError):
                pass
    result = markout.calculate_markouts(executions, ticks_by_symbol)
    from services.markout_dashboard import dashboard as markout_dashboard
    result["dashboard"] = markout_dashboard(result, currency, modes)
    return {**result, "diagnostics": diagnostics,
            "history_status": "complete" if executions else "no_trade_fills" if deals else "no_deals",
            "client_login": req.client_login, "group": group_map[req.client_login],
            "from_date": req.from_date.isoformat(), "to_date": req.to_date.isoformat(),
            "source": "MT5 Manager deal execution and tick history", "warnings": list(dict.fromkeys(warnings)),
            "generated_at": datetime.now(timezone.utc).isoformat()}


@api.post("/api/risk/analyse")
def analyse_client_patterns(req: ClientPatternRequest):
    if req.from_date > req.to_date:
        raise HTTPException(status_code=400, detail="from_date must be on or before to_date.")
    _, _, from_ts, to_ts = _date_range_utc(req.from_date, req.to_date)
    import mt5_connection
    manager = mt5_connection.get_manager()
    cached_client = _cached_client_read(req.client_login)
    live_cache = {"groups": cached_client["groups"], "users": [cached_client["user"]]} if cached_client else None
    with yielding_history_manager_session() as api_handle:
        groups, _, group_warnings = (
            _resolve_groups_from_available(
                live_cache["groups"], req.selected_groups, req.group_filter_rules
            )
            if live_cache and live_cache.get("groups")
            else _resolve_groups_for_request(api_handle, req.selected_groups, req.group_filter_rules)
        )
        if not groups:
            raise HTTPException(status_code=400, detail="Select at least one matching group.")

        excluded = _normalize_login_set(req.excluded_logins)
        cached_user = next(
            (
                row for row in (live_cache or {}).get("users", [])
                if _to_int(row.get("Login"), 0) == req.client_login
                and str(row.get("Group", "")) in groups
                and req.client_login not in excluded
            ),
            None,
        )
        if cached_user is not None:
            users_by_login = {req.client_login: {"name": cached_user.get("Name", ""),
                "last_ip": _ip_text(cached_user.get("LastIP", "")), "currency": cached_user.get("Currency", "")}}
            group_map = {req.client_login: str(cached_user.get("Group", ""))}
            warnings = []
        else:
            users_by_login, group_map, warnings = _collect_user_scope(api_handle, groups, excluded)
        user = users_by_login.get(req.client_login)
        if user is None:
            raise HTTPException(status_code=404, detail="Client login was not found in the selected groups.")

        deals, direct_error = request_deals_by_login(api_handle, req.client_login, from_ts, to_ts)
        if direct_error:
            deals = []
            seen_tickets: set[int] = set()
            for group in groups:
                group_deals, err = request_deals_by_group(api_handle, group, from_ts, to_ts)
                if err:
                    warnings.append(f"{group}: {err}")
                    continue
                for deal in group_deals:
                    if _login_from_record(deal) != req.client_login:
                        continue
                    ticket = _to_int(_value(deal, "Deal", "deal", "Ticket", "ticket"), 0) or 0
                    if ticket and ticket in seen_tickets:
                        continue
                    if ticket:
                        seen_tickets.add(ticket)
                    deals.append(deal)

        rows = build_client_trade_lifecycles(deals, req.client_login)
        analysis = analyse_client_lifecycles(rows, req.scalping_seconds)
        journal_identifiers = {
            value for deal in deals
            for value in (
                _to_int(_value(deal, "Deal", "deal"), 0) or 0,
                _to_int(_value(deal, "Order", "order", "OrderID", "order_id"), 0) or 0,
                _to_int(_value(deal, "PositionID", "position_id", "Position", "position"), 0) or 0,
            ) if value
        }
        journal_rows, journal_error = request_trade_journal(
            api_handle, req.client_login, from_ts, to_ts,
            req.server, req.login, req.password,
            journal_identifiers,
        )
        local_journal_rows, local_log_files = scan_local_trade_journals(
            req.client_login, from_ts, to_ts, journal_identifiers
        )
        combined_journal_rows = [*journal_rows, *local_journal_rows]
        execution_evidence = map_trade_execution_evidence(deals, combined_journal_rows, req.client_login)
        execution_by_position = {
            position_id: evidence["ips"] for position_id, evidence in execution_evidence.items()
        }
        execution_ips = {
            ip for position_ips in execution_by_position.values() for ip in position_ips
        }
        client_ip = user.get("last_ip", "")
        network_ips = set(execution_ips)
        if client_ip:
            network_ips.add(client_ip)
        execution_network = enrich_ip_addresses_nonblocking(network_ips)
        evidence_stats = apply_trade_execution_evidence(
            analysis["trades"], execution_evidence, execution_network, fallback_ip=client_ip
        )
        covered_trades = evidence_stats["covered_trades"]
        inferred_trades = evidence_stats["inferred_trades"]
        attributed_trades = evidence_stats["attributed_trades"]
        ip_change_count = evidence_stats["ip_change_count"]
        multi_ip_trades = evidence_stats["multi_ip_trades"]
        configured_log_dirs = [
            value.strip() for value in os.getenv("MT5_SERVER_LOG_DIRS", "").split(os.pathsep)
            if value.strip()
        ]
        coverage_pct = round(covered_trades / max(len(analysis["trades"]), 1) * 100.0, 1)
        attribution_pct = round(attributed_trades / max(len(analysis["trades"]), 1) * 100.0, 1)
        if covered_trades:
            diagnostic_status = "healthy" if coverage_pct >= 80 else "partial"
        elif journal_error:
            diagnostic_status = "permission_or_source_missing"
        else:
            diagnostic_status = "no_ticket_evidence"
        analysis["execution_network"] = {
            "journal_records": len(combined_journal_rows),
            "remote_journal_records": len(journal_rows),
            "local_journal_records": len(local_journal_rows),
            "local_log_files_scanned": len(local_log_files),
            "mapped_positions": len(execution_by_position),
            "covered_trades": covered_trades,
            "inferred_trades": inferred_trades,
            "attributed_trades": attributed_trades,
            "total_trades": len(analysis["trades"]),
            "coverage_pct": coverage_pct,
            "attribution_pct": attribution_pct,
            "unique_ips": len(execution_ips),
            "vps_ips": sum(intel.get("vps_status") == "detected" for intel in execution_network.values()),
            "vps_checked_ips": sum(intel.get("status") == "available" for intel in execution_network.values()),
            "vps_total_ips": len(network_ips),
            "ip_change_count": ip_change_count,
            "multi_ip_trades": multi_ip_trades,
            "source": "MT5 server journal + configured local MT5 logs",
            "diagnostic_status": diagnostic_status,
            "remote_journal_available": bool(journal_rows),
            "configured_log_directories": len(configured_log_dirs),
            "actions": [
                "Grant the reporting manager LoggerServerRequest/server-journal read permission, preferably via Admin API.",
                "Set MT5_SERVER_LOG_DIRS to copied MT5 server connection/request journal directories.",
                "Confirm journal lines contain a client public IP and a deal, order, or position ID from the selected range.",
                "Do not use terminal access-server IPs as client trade IPs; they are broker infrastructure addresses.",
            ] if not covered_trades else [],
        }
        if journal_error and not execution_by_position:
            warnings.append(f"Trade IP: {journal_error}")
        if not execution_by_position:
            fallback_note = " Account last-login IP is shown as an inferred fallback." if client_ip else " No account last-login IP is available for fallback."
            warnings.append(f"Trade IP: no ticket-linked public IP evidence was found in {len(journal_rows)} remote records or {len(local_log_files)} local log files.{fallback_note}")
        account, account_error = (cached_client["account"], None) if cached_client else request_account(api_handle, req.client_login)
        if account_error:
            warnings.append(account_error)

        client_network = execution_network.get(client_ip, {})

    analysis.update({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected_groups": groups,
        "range": {"from": req.from_date.isoformat(), "to": req.to_date.isoformat()},
        "client": {
            "login": req.client_login,
            "name": user.get("name", ""),
            "group": group_map.get(req.client_login, ""),
            "last_ip": client_ip,
            "vps_status": client_network.get("vps_status", "unknown"),
            "vps_confidence": client_network.get("vps_confidence", 0),
            "vps_evidence": client_network.get("vps_evidence", ["Provider lookup unavailable"]),
            "location": client_network.get("location", ""),
            "country": client_network.get("country", ""),
            "city": client_network.get("city", ""),
            "latitude": client_network.get("latitude"),
            "longitude": client_network.get("longitude"),
            "provider": client_network.get("provider", ""),
            "asn": client_network.get("asn", ""),
            "balance": round(_to_float(_value(account, "Balance", "balance"), 0.0), 2),
            "equity": round(_to_float(_value(account, "Equity", "equity"), 0.0), 2),
            "margin_level": round(_to_float(_value(account, "MarginLevel", "margin_level"), 0.0), 2),
        },
        "warnings": _dedupe_warnings(group_warnings, warnings),
    })
    return analysis


def _cached_client_read(login):
    """Copy one account and its positions atomically, never the entire book."""
    import mt5_connection
    manager = mt5_connection.get_manager()
    if not manager or not manager.is_connected or not mt5_connection.FULL_SNAPSHOT_ENABLED or not hasattr(manager, "cache_lock"):
        return None
    with manager.cache_lock:
        generation = manager.cache.get("last_update", 0)
        if (not generation or time_module.time() - generation > 10
                or manager.cache.get("errors") or manager.cache.get("status") != "connected"):
            return None
        user = manager.cache.get("users_by_login", {}).get(int(login))
        if not user:
            return None
        user = dict(user)
        positions = [dict(row, VolumeExt=int(round(float(row.get("Volume", 0)) * 100000000)))
                     for row in manager.cache.get("positions_by_login", {}).get(int(login), [])]
        account = dict(user, MarginFree=user.get("Free Margin"), MarginLevel=user.get("Margin Level %"))
        return {"user": user, "positions": positions, "account": account,
                "groups": list(manager.cache.get("groups", [])), "generation": generation}


@api.post("/api/risk/client-live")
def get_client_live(req: ClientLiveRequest):
    if req.client_login in _normalize_login_set(req.excluded_logins):
        raise HTTPException(status_code=404, detail="Client login is excluded from the selected scope.")
    cached = _cached_client_read(req.client_login)
    if cached:
        groups, _, _ = _resolve_groups_from_available(cached["groups"], req.selected_groups, req.group_filter_rules)
        if (str(cached["user"].get("Group", "")) not in groups
                or req.client_login in _normalize_login_set(req.excluded_logins)):
            raise HTTPException(status_code=404, detail="Client login is outside the selected groups.")
        payload = build_client_live_snapshot(None, req.client_login, cached["user"],
                    cached_positions=cached["positions"], cached_account=cached["account"])
        payload["source_updated_at"] = cached["generation"]
        payload["cache"] = {"source": "manager", "generation": cached["generation"]}
        return payload
    with market_manager_session(req.server, req.login, req.password) as api_handle:
        user_method = getattr(api_handle, "UserRequest", None) or getattr(api_handle, "UserGet", None)
        if user_method is None:
            raise HTTPException(status_code=500, detail="UserRequest is unavailable in this Manager API wrapper.")
        try:
            response = user_method(req.client_login)
            user, err = _response_to_data(response, "UserRequest")
        except Exception as exc:
            user, err = None, str(exc)
        if err or user is None or isinstance(user, bool):
            raise HTTPException(status_code=404, detail=err or "Client login was not found.")
        group = str(_value(user, "Group", "group", default="") or "")
        selected = [str(value).strip() for value in req.selected_groups if str(value).strip()]
        if selected and not any(_group_pattern_matches(group, pattern) for pattern in selected):
            raise HTTPException(status_code=404, detail="Client login is outside the selected groups.")
        return build_client_live_snapshot(api_handle, req.client_login, user)


@api.post("/api/risk/antifraud")
def get_antifraud_snapshot(req: AntiFraudRequest):
    import mt5_connection
    if not req.selected_groups:
        return _empty_antifraud_snapshot(
            "Select one or more groups from the configured manager to start the scan."
        )
    cache_key = _antifraud_cache_key(req)
    cache_ttl = min(ANTIFRAUD_CACHE_SECONDS, 5.0) if os.getenv("RMS_NATIVE_URL") else ANTIFRAUD_CACHE_SECONDS
    now = time_module.monotonic()
    force_refresh = bool(
        req.refresh_token
        or getattr(_ANTIFRAUD_REFRESH_CONTEXT, "enabled", False)
    )
    with _ANTIFRAUD_CACHE_LOCK:
        cached = _ANTIFRAUD_CACHE.get(cache_key)
        if cached and not force_refresh and now - cached[0] < cache_ttl:
            payload = copy.deepcopy(cached[1])
            payload["cache"] = {"hit": True, "ttl_seconds": cache_ttl}
            return payload
    if not force_refresh and not cached:
        with _conn() as con:
            stored = con.execute(
                "SELECT payload_json,generated_at FROM antifraud_snapshots WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
        if stored:
            payload = _json_column(stored["payload_json"], {})
            stored_at = str(payload.get("generated_at") or stored["generated_at"] or "")
            try:
                generated_time = datetime.fromisoformat(stored_at.replace("Z", "+00:00"))
                if generated_time.tzinfo is None:
                    generated_time = generated_time.replace(tzinfo=timezone.utc)
                age_seconds = max(0, int((datetime.now(timezone.utc) - generated_time).total_seconds()))
            except ValueError:
                age_seconds = None
            payload["cache"] = {
                "hit": True, "stale": True, "persistent": True,
                "stored_at": stored_at, "age_seconds": age_seconds,
                "refreshing": True,
            }
            with _ANTIFRAUD_CACHE_LOCK:
                _ANTIFRAUD_CACHE[cache_key] = (0.0, copy.deepcopy(payload))
                should_start = cache_key not in _ANTIFRAUD_REFRESHING
                if should_start:
                    _ANTIFRAUD_REFRESHING.add(cache_key)
            if should_start:
                def refresh_persisted_snapshot() -> None:
                    _ANTIFRAUD_REFRESH_CONTEXT.enabled = True
                    try:
                        get_antifraud_snapshot(req)
                    except Exception:
                        logger.exception("Background persisted anti-fraud refresh failed")
                    finally:
                        _ANTIFRAUD_REFRESH_CONTEXT.enabled = False
                        with _ANTIFRAUD_CACHE_LOCK:
                            _ANTIFRAUD_REFRESHING.discard(cache_key)

                threading.Thread(target=refresh_persisted_snapshot, daemon=True).start()
            return payload
    if cached and not force_refresh:
        payload = copy.deepcopy(cached[1])
        payload["cache"] = {
            "hit": True, "stale": True, "refreshing": True,
            "age_seconds": round(now - cached[0]) if cached[0] else None,
            "ttl_seconds": cache_ttl,
        }
        if cache_key not in _ANTIFRAUD_REFRESHING:
            _ANTIFRAUD_REFRESHING.add(cache_key)

            def refresh_snapshot() -> None:
                _ANTIFRAUD_REFRESH_CONTEXT.enabled = True
                try:
                    get_antifraud_snapshot(req)
                except Exception:
                    logger.exception("Background anti-fraud refresh failed")
                finally:
                    _ANTIFRAUD_REFRESH_CONTEXT.enabled = False
                    with _ANTIFRAUD_CACHE_LOCK:
                        _ANTIFRAUD_REFRESHING.discard(cache_key)

            threading.Thread(target=refresh_snapshot, daemon=True).start()
        return payload

    if not force_refresh:
        with _ANTIFRAUD_CACHE_LOCK:
            should_start = cache_key not in _ANTIFRAUD_REFRESHING
            if should_start:
                _ANTIFRAUD_REFRESHING.add(cache_key)
        if should_start:
            def warm_antifraud_snapshot() -> None:
                _ANTIFRAUD_REFRESH_CONTEXT.enabled = True
                try:
                    get_antifraud_snapshot(req)
                except Exception:
                    logger.exception("Background anti-fraud warmup failed")
                finally:
                    _ANTIFRAUD_REFRESH_CONTEXT.enabled = False
                    with _ANTIFRAUD_CACHE_LOCK:
                        _ANTIFRAUD_REFRESHING.discard(cache_key)

            threading.Thread(target=warm_antifraud_snapshot, daemon=True).start()
        return {
            **_empty_antifraud_snapshot(
                "Building the first verified evidence snapshot in the background."
            ),
            "status": "warming",
            "cache": {"hit": False, "refreshing": True},
        }

    with _ANTIFRAUD_CACHE_LOCK:
        compute_lock = _ANTIFRAUD_KEY_LOCKS.setdefault(cache_key, threading.Lock())
    with compute_lock:
        with _ANTIFRAUD_CACHE_LOCK:
            cached = _ANTIFRAUD_CACHE.get(cache_key)
            if cached and not force_refresh and time_module.monotonic() - cached[0] < cache_ttl:
                payload = copy.deepcopy(cached[1])
                payload["cache"] = {"hit": True, "ttl_seconds": cache_ttl}
                return payload
        group_warnings: list[str] = []
        try:
            manager = get_configured_manager()
            if not manager or not manager.is_connected:
                return {
                    **_empty_antifraud_snapshot(
                        "Configured MT5 manager is reconnecting; retrying automatically."
                    ),
                    "status": "warming",
                }
            # Focused mode intentionally does not build an account-wide cache
            # for manager 2076. Only use the cheap group catalog here; the
            # selected users/deals/positions are loaded directly below.
            live_cache = manager.live_risk_cache_view(groups=[])
            available_groups = live_cache.get("groups", [])
            if not available_groups:
                return {
                    **_empty_antifraud_snapshot(
                        "MT5 group catalog is initializing; retrying the selected scan automatically."
                    ),
                    "status": "warming",
                }
            with market_manager_session(req.server, req.login, req.password) as api_handle:
                groups, _, group_warnings = _resolve_groups_from_available(
                    available_groups, req.selected_groups, req.group_filter_rules
                )
                if not groups:
                    return _empty_antifraud_snapshot("No groups matched the current selection.")
                request_groups = _compress_complete_group_branches(groups, available_groups)
                scoped_cache = manager.live_risk_cache_view(groups=groups) if (mt5_connection.FULL_SNAPSHOT_ENABLED
                    and live_cache.get("last_update", 0) > 0 and not live_cache.get("errors")
                    and time_module.time() - live_cache["last_update"] <= 10) else None
                cache_ready = bool(scoped_cache and scoped_cache.get("last_update", 0) > 0
                    and time_module.time() - scoped_cache["last_update"] <= 10 and not scoped_cache.get("errors"))
                logger.info(
                    "Anti-fraud focused scan starting manager=%s groups=%s requests=%s",
                    manager.manager_login, len(groups), request_groups,
                )
                payload = build_antifraud_snapshot(
                    api_handle, groups, req.excluded_logins,
                    lookback_hours=req.lookback_hours,
                    gap_seconds=req.gap_seconds,
                    profit_threshold=req.profit_threshold,
                    check_total_realized_pnl=req.check_total_realized_pnl,
                    minimum_total_realized_pnl=req.minimum_total_realized_pnl,
                    maximum_duration_minutes=req.maximum_duration_minutes,
                    scalp_seconds=req.scalp_seconds,
                    scalp_sequence_min=req.scalp_sequence_min,
                    sharp_profit_threshold=req.sharp_profit_threshold,
                    concentration_min_trades=req.concentration_min_trades,
                    deposit_alert_threshold=req.deposit_alert_threshold,
                    large_order_lots=req.large_order_lots,
                    large_account_lots=req.large_account_lots,
                    large_order_exposure_ratio=req.large_order_exposure_ratio,
                    large_account_exposure_ratio=req.large_account_exposure_ratio,
                    mini_equity_threshold=req.mini_equity_threshold,
                    stopout_margin_level=req.stopout_margin_level,
                    news_window_minutes=req.news_window_minutes,
                    news_impacts=req.news_impacts,
                    calendar_events=req.calendar_events,
                    excluded_symbols=req.excluded_symbols,
                    cached_users=scoped_cache["users"] if cache_ready else None,
                    cached_positions=[dict(row, VolumeExt=int(round(float(row.get("Volume", 0)) * 100000000))) for row in scoped_cache["positions"]] if cache_ready else None,
                    request_groups=request_groups,
                )
                configured_server, configured_login, _ = configured_manager_credentials()
                payload["scan_started_at"] = payload.get("generated_at")
                payload["generated_at"] = datetime.now(timezone.utc).isoformat()
                payload["source_updated_at"] = scoped_cache["last_update"] if cache_ready else None
                payload["live_source"] = "mt5-cpp-native" if getattr(manager, "native_mode", False) and cache_ready else "mt5-manager"
                payload["source"] = {
                    "server": configured_server,
                    "manager_login": configured_login,
                    "selected_groups": list(req.selected_groups),
                    "resolved_groups": list(groups),
                }
                logger.info(
                    "Anti-fraud scan groups=%s deals=%s positions=%s events=%s warnings=%s",
                    len(groups), payload.get("summary", {}).get("deals_scanned", 0),
                    payload.get("summary", {}).get("positions_scanned", 0),
                    payload.get("summary", {}).get("events", 0), len(payload.get("warnings", [])),
                )
        except HTTPException as exc:
            return _empty_antifraud_snapshot(exc.detail)
        # A completed scan with zero deals is still authoritative. Cache it so
        # quiet groups do not trigger a fresh background scan every five seconds.
        _record_news_incidents(payload.get("events", []), payload.get("client_summaries", []))
        NEWS_PUSH.queue(payload.get("events", []))
        cacheable = bool(payload.get("selected_groups"))
        with _ANTIFRAUD_CACHE_LOCK:
            if len(_ANTIFRAUD_CACHE) >= 32:
                oldest = min(_ANTIFRAUD_CACHE, key=lambda key: _ANTIFRAUD_CACHE[key][0])
                _ANTIFRAUD_CACHE.pop(oldest, None)
            if cacheable:
                _ANTIFRAUD_CACHE[cache_key] = (time_module.monotonic(), copy.deepcopy(payload))
        if cacheable:
            with _conn() as con:
                con.execute(
                    """INSERT INTO antifraud_snapshots(cache_key,payload_json,generated_at)
                       VALUES (?,?,datetime('now')) ON CONFLICT(cache_key) DO UPDATE SET
                       payload_json=excluded.payload_json,generated_at=datetime('now')""",
                    (cache_key, json.dumps(payload)),
                )
        payload["cache"] = {"hit": False, "ttl_seconds": cache_ttl}
    payload["warnings"] = _dedupe_warnings(group_warnings, payload.get("warnings", []))
    new_events = persist_antifraud_events(payload.get("events", []), getattr(manager, "update_marker", lambda: None)())
    _remember_antifraud_monitor_scope(req, cache_key)
    alerted_logins = {_to_int(event.get("login"), 0) for event in new_events}
    notify_new_antifraud_events(new_events, [row for row in payload.get("client_summaries", []) if _to_int(row.get("login"), 0) in alerted_logins])
    return payload


def _scan_next_monitored_antifraud_scope() -> bool:
    """Refresh one persisted scope without requiring an open browser tab."""
    interval = max(int(os.getenv("ANTIFRAUD_BACKGROUND_SCAN_SECONDS", "60")), 60)
    with _conn() as con:
        row = con.execute("SELECT cache_key,request_json,last_scan_at FROM antifraud_monitor_scopes ORDER BY COALESCE(last_scan_at,'') ASC LIMIT 1").fetchone()
    if row is None:
        return False
    last_scan = str(row["last_scan_at"] or "")
    if last_scan:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_scan).replace(tzinfo=timezone.utc)).total_seconds()
            if age < interval:
                return False
        except ValueError:
            pass
    try:
        stored = _json_column(row["request_json"], {})
        request = AntiFraudRequest.model_validate({**stored, "refresh_token": int(time_module.time())})
        payload = get_antifraud_snapshot(request)
        error = "; ".join(payload.get("warnings", [])[:2]) if payload.get("status") in {"offline", "warming"} else ""
    except Exception as exc:
        logger.exception("Background Anti-Fraud scope scan failed")
        error = str(exc)
    with _conn() as con:
        con.execute("UPDATE antifraud_monitor_scopes SET last_scan_at=datetime('now'),last_error=? WHERE cache_key=?", (error[:1000], row["cache_key"]))
    return True


def _configured_manager_source_key() -> str:
    # Persist deduplication per configured Manager without storing credentials.
    return hashlib.sha256((os.getenv("MT5_MANAGER_SERVER", "") + "|" + os.getenv("MT5_MANAGER_LOGIN", "")).encode()).hexdigest()


def _spread_alert_supervisor() -> None:
    while not _RUNTIME_MAINTENANCE_STOP.wait(.25):
        try:
            settings = SPREAD_ALERTS.read_settings()
            if not settings.enabled or not SPREAD_ALERTS.delivery_enabled():
                continue
            import mt5_connection
            manager = mt5_connection.get_manager()
            if not manager or not manager.is_connected:
                continue
            with manager.cache_lock:
                cache = manager.cache
                symbols, ticks = cache.get("symbol_cache", {}), cache.get("tick_cache", {})
                last_update = cache.get("last_update", 0)
                server_time = cache.get("server_time_msc")
                checked_at = cache.get("server_time_checked_at")
            if not last_update or time_module.time() - float(last_update) > 5:
                continue
            reference = mt5_connection.quote_reference_time(server_time, checked_at)
            SPREAD_ALERTS.scan(fresh_spreads(symbols, ticks, reference), _configured_manager_source_key(), time_module.time())
        except Exception:
            logger.exception("Spread monitor scan failed")
            _RUNTIME_MAINTENANCE_STOP.wait(5)


def _spread_delivery_supervisor() -> None:
    while not _RUNTIME_MAINTENANCE_STOP.wait(.25):
        try:
            SPREAD_ALERTS.deliver_one(_configured_manager_source_key(), time_module.time())
        except Exception:
            logger.exception("Spread alert delivery failed")
            _RUNTIME_MAINTENANCE_STOP.wait(5)


def _news_push_supervisor() -> None:
    if _RUNTIME_MAINTENANCE_STOP.wait(5):
        return
    try:
        NEWS_PUSH.recover()
    except Exception:
        logger.exception("Unable to recover the news-push send log")
    while not _RUNTIME_MAINTENANCE_STOP.is_set():
        try:
            NEWS_PUSH.process_one()
        except Exception:
            logger.exception("News-push worker will retry pending work")
        if _RUNTIME_MAINTENANCE_STOP.wait(1):
            return


def _antifraud_background_supervisor() -> None:
    if _RUNTIME_MAINTENANCE_STOP.wait(30):
        return
    while not _RUNTIME_MAINTENANCE_STOP.is_set():
        if os.getenv("ANTIFRAUD_BACKGROUND_SCAN_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}:
            try:
                _scan_next_monitored_antifraud_scope()
            except Exception:
                logger.exception("Anti-Fraud background supervisor failed")
        if _RUNTIME_MAINTENANCE_STOP.wait(30):
            return


@api.post("/api/risk/stress")
def get_stress_snapshot(req: StressTestRequest):
    cache_key = hashlib.sha256(
        json.dumps(req.model_dump(exclude={"password"}), sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    now = time_module.monotonic()
    with _STRESS_CACHE_LOCK:
        cached = _STRESS_CACHE.get(cache_key)
        if cached and now - cached[0] < 15.0:
            payload = copy.deepcopy(cached[1])
            payload["cache"] = {"hit": True, "ttl_seconds": 15}
            return payload
    with _STRESS_CACHE_LOCK:
        should_start = cache_key not in _STRESS_REFRESHING
        if should_start:
            _STRESS_REFRESHING.add(cache_key)
    if should_start:
        def compute_stress_snapshot() -> None:
            try:
                with market_manager_session(req.server, req.login, req.password) as api_handle:
                    import mt5_connection
                    manager = mt5_connection.get_manager()
                    available = manager.group_cache_view() if manager and manager.is_connected else []
                    groups, _, group_warnings = (
                        _resolve_groups_from_available(
                            available, req.selected_groups, req.group_filter_rules
                        )
                        if available
                        else _resolve_groups_for_request(
                            api_handle, req.selected_groups, req.group_filter_rules
                        )
                    )
                    if not groups:
                        return
                    payload = build_stress_snapshot(
                        api_handle, groups, req.excluded_logins,
                        shock_percent=req.shock_percent,
                        margin_level_floor=req.margin_level_floor,
                        concentration_limit=req.concentration_limit,
                        symbol_gross_limit=req.symbol_gross_limit,
                    )
                payload["warnings"] = _dedupe_warnings(
                    group_warnings, payload.get("warnings", [])
                )
                payload["cache"] = {"hit": False, "ttl_seconds": 15}
                with _STRESS_CACHE_LOCK:
                    if len(_STRESS_CACHE) >= 32:
                        oldest = min(_STRESS_CACHE, key=lambda key: _STRESS_CACHE[key][0])
                        _STRESS_CACHE.pop(oldest, None)
                    _STRESS_CACHE[cache_key] = (
                        time_module.monotonic(), copy.deepcopy(payload)
                    )
            except Exception:
                logger.exception("Background risk stress calculation failed")
            finally:
                with _STRESS_CACHE_LOCK:
                    _STRESS_REFRESHING.discard(cache_key)

        threading.Thread(target=compute_stress_snapshot, daemon=True).start()
    return {
        "status": "warming", "summary": {}, "symbols": [], "accounts": [],
        "warnings": [], "cache": {"hit": False, "refreshing": True},
    }


@api.post("/api/reports/deal")
def get_deal_report(req: DealReportRequest):
    with yielding_history_manager_session() as api_handle:
        return _build_deal_report_payload(api_handle, req)


@api.post("/api/reports/deal/export")
def export_deal_report(req: DealExportRequest):
    if req.cached_report is not None:
        payload = dict(req.cached_report)
    else:
        with yielding_history_manager_session() as api_handle:
            payload = _build_deal_report_payload(api_handle, req)

    payload["selected_groups"] = [
        str(group).strip()
        for group in (payload.get("selected_groups") or req.selected_groups or [])
        if str(group).strip()
    ]
    payload["warnings"] = list(payload.get("warnings") or [])
    payload["report_range"] = (req.from_date, req.to_date)

    buf = build_deal_export_workbook(payload)

    filename = f"PnL_{req.from_date.isoformat()}_to_{req.to_date.isoformat()}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api.post("/api/reports/brokerage")
def get_brokerage_report(req: BrokerageReportRequest):
    if req.opening_equity_date > req.closing_equity_date:
        raise HTTPException(status_code=400, detail="opening_equity_date must be on or before closing_equity_date.")
    summary_from = req.date_from or (req.opening_equity_date + timedelta(days=1))
    summary_to = req.date_to or req.closing_equity_date

    with yielding_history_manager_session() as api_handle:
        groups, _, group_warnings = _resolve_groups_for_request(
            api_handle, req.selected_groups, req.group_filter_rules
        )
        payload = build_brokerage_export_payload(
            api_handle,
            groups,
            req.opening_equity_date,
            req.closing_equity_date,
            summary_from,
            summary_to,
            clip_negative_equity=False,
            excluded_logins=req.excluded_logins,
        )

    summary_with_total = append_total_row(payload["trade_summary"])
    snapshot_opening = payload["opening_snapshot"]
    snapshot_closing = payload["closing_snapshot"]
    brokerage_totals = payload["brokerage_totals"]

    return {
        "selected_groups": groups,
        "opening_equity_date": req.opening_equity_date.isoformat(),
        "closing_equity_date": req.closing_equity_date.isoformat(),
        "date_from": summary_from.isoformat(),
        "date_to": summary_to.isoformat(),
        "opening_report": _dataframe_to_records(snapshot_opening),
        "closing_report": _dataframe_to_records(snapshot_closing),
        "summary": _dataframe_to_records(payload["trade_summary"]),
        "summary_with_total": _dataframe_to_records(summary_with_total),
        "accounts_display": _dataframe_to_records(payload["accounts_display"]),
        "group_summary": _dataframe_to_records(payload["group_summary"]),
        "books_summary": _dataframe_to_records(payload["books_summary"]),
        "daily_summary": _dataframe_to_records(payload["daily_summary"]),
        "lp_summary": _dataframe_to_records(payload["lp_summary"]),
        "brokerage_totals": payload["brokerage_totals"],
        "data_quality": payload["data_quality"],
        "summary_range": [summary_from.isoformat(), summary_to.isoformat()],
        "opening_equity_total": round(float(brokerage_totals.get("opening_equity", 0.0)), 2),
        "closing_equity_total": round(float(brokerage_totals.get("closing_equity", 0.0)), 2),
        "equity_difference": round(float(brokerage_totals.get("difference", 0.0)), 2),
        "total_lp_pnl": round(float(brokerage_totals.get("total_lp_pnl", 0.0)), 2),
        "client_a_book_pnl": round(float(brokerage_totals.get("client_a_book_pnl", 0.0)), 2),
        "net_brokerage": round(float(brokerage_totals.get("net_brokerage", 0.0)), 2),
        "profile_name": req.profile_name or "",
        "warnings": _dedupe_warnings(group_warnings, payload.get("warnings", [])),
    }


_AUDITED_BROKERAGE_JOBS: dict[str, dict[str, Any]] = {}
_AUDITED_BROKERAGE_LOCK = threading.RLock()
_AUDITED_BROKERAGE_TTL = 15 * 60
_AUDITED_BROKERAGE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audited-brokerage")
_AUDITED_BROKERAGE_MAX_PENDING = 4


def _audited_brokerage_job_id(req: AuditedBrokerageRequest, actor_id: int) -> str:
    rule_file = brokerage_pnl.TRANSACTION_RULES_STORE
    fingerprint = {
        **req.model_dump(mode="json", exclude={"password", "server", "login", "force_refresh"}),
        "actor_id": actor_id,
        "rules_mtime": rule_file.stat().st_mtime_ns if rule_file.exists() else None,
    }
    return hmac.new(_SESSION_SECRET, json.dumps(fingerprint, sort_keys=True).encode(), hashlib.sha256).hexdigest()[:32]


def _audited_brokerage_result(job: dict[str, Any]) -> dict[str, Any]:
    if job["status"] != "ready":
        return {"status": job["status"], "job_id": job["job_id"], "error": job.get("error")}
    report = job["report"]
    accounts = report["accounts"]
    all_clients = brokerage_pnl.aggregate_summary(accounts, report["coverage"], report["aligned"])
    complete = brokerage_pnl.aggregate_summary(accounts, report["coverage"], report["aligned"], complete_only=True)
    audit = report["audit"]
    blocking_accounts = []
    for account in accounts.itertuples(index=False, name=None):
        row = dict(zip(accounts.columns, account))
        missing = [key for key in brokerage_pnl.SOURCE_COLUMNS
                   if brokerage_pnl._to_float(row.get(key)) is None]
        if not missing and "Currency changed" not in str(row.get("Data Quality", "")):
            continue
        blocking_accounts.append({
            "Login": int(row["Login"]), "Name": str(row.get("Name") or ""),
            "Group": str(row.get("Group") or ""), "Currency": str(row.get("Currency") or "Unknown"),
            "Missing fields": missing, "Reason": str(row.get("Data Quality") or ""),
            "Opening Source": str(row.get("Opening Source") or ""),
            "Closing Source": str(row.get("Closing Source") or ""),
        })
    leaderboards: dict[str, dict[str, list[dict[str, Any]]]] = {}
    if not accounts.empty:
        ranked = accounts.copy()
        ranked["_rank_pnl"] = pd.to_numeric(ranked["Client PnL"], errors="coerce")
        ranked["_currency_scope"] = ranked["Currency"].where(
            ranked["Currency"] != "Unknown", "Unknown (" + ranked["Group"].astype(str) + ")"
        )
        for currency, scoped in ranked.groupby("_currency_scope", sort=True):
            winners = scoped.loc[scoped["_rank_pnl"] > 0].sort_values(
                ["_rank_pnl", "Login"], ascending=[False, True]
            ).head(10)
            losers = scoped.loc[scoped["_rank_pnl"] < 0].sort_values(
                ["_rank_pnl", "Login"], ascending=[True, True]
            ).head(10)
            leaderboards[str(currency)] = {
                "winners": _dataframe_to_records(winners.drop(columns=["_rank_pnl", "_currency_scope"])),
                "losers": _dataframe_to_records(losers.drop(columns=["_rank_pnl", "_currency_scope"])),
            }
    return {
        "status": "ready", "job_id": job["job_id"], "generated_at": report["generated"],
        "book_mode": report["book_mode"], "formula": brokerage_pnl.FORMULA,
        "opening_equity_date": report["opening"].isoformat(),
        "closing_equity_date": report["closing"].isoformat(),
        "date_from": report["flow_start"].isoformat(), "date_to": report["flow_end"].isoformat(),
        "selected_groups": report["groups"], "selected_logins": report["logins"],
        "coverage": report["coverage"], "aligned": report["aligned"],
        "excluded_new_accounts": report["excluded_new_accounts"],
        "accounts_total": len(accounts), "audit_total": len(audit),
        "accounts": _dataframe_to_records(accounts.head(5000)),
        "leaderboards": leaderboards,
        "all_clients": _dataframe_to_records(all_clients),
        "complete_accounts": _dataframe_to_records(complete),
        "blocking_accounts": blocking_accounts,
        "transaction_audit": _dataframe_to_records(audit.head(2000)),
        "unresolved_transactions": _dataframe_to_records(
            audit.loc[audit["Review"].eq("Unresolved")].head(100)
        ) if not audit.empty else [],
        "diagnostics": _dataframe_to_records(report["diagnostics"]),
        "warnings": report["warnings"][:300],
    }


def _compute_audited_brokerage(job_id: str, generation: int, req: AuditedBrokerageRequest) -> None:
    started = time_module.perf_counter()
    try:
        with yielding_history_manager_session() as api_handle:
            groups, _, group_warnings = _resolve_groups_for_request(
                api_handle, req.selected_groups, req.group_filter_rules
            )
            if not groups:
                raise ValueError("Select at least one valid MT5 group.")
            selected_logins = sorted(set(req.selected_logins) - set(req.excluded_logins)) or None
            if req.selected_logins and selected_logins is None:
                raise ValueError("Every selected login was excluded.")
            resolved_users = (brokerage_pnl.resolve_login_scope(
                api_handle, selected_logins, allowed_groups=set(groups)
            ) if selected_logins is not None else None)
            report = brokerage_pnl.build_report(
                api_handle, groups, req.opening_equity_date, req.closing_equity_date,
                req.date_from or req.opening_equity_date + timedelta(days=1),
                req.date_to or req.closing_equity_date,
                allow_live=req.allow_live, selected_logins=selected_logins,
                resolved_users=resolved_users, excluded_logins=req.excluded_logins,
            )
            report["accounts"] = brokerage_pnl.apply_book_mode(report["accounts"], req.book_mode)
            report["book_mode"] = req.book_mode
            report["warnings"] = _dedupe_warnings(group_warnings, report["warnings"])
        with _AUDITED_BROKERAGE_LOCK:
            job = _AUDITED_BROKERAGE_JOBS.get(job_id)
            if job and job["generation"] == generation:
                job.update(status="ready", report=report, completed_at=time_module.monotonic())
        logger.info("Audited brokerage ready groups=%s accounts=%s elapsed_ms=%.1f", len(groups), len(report["accounts"]), (time_module.perf_counter() - started) * 1000)
    except Exception as exc:
        logger.exception("Audited brokerage report failed")
        with _AUDITED_BROKERAGE_LOCK:
            job = _AUDITED_BROKERAGE_JOBS.get(job_id)
            if job and job["generation"] == generation:
                job.update(status="error", error=str(exc)[:1000], completed_at=time_module.monotonic())


@api.post("/api/reports/brokerage/audited")
def start_audited_brokerage(req: AuditedBrokerageRequest, request: Request):
    actor = getattr(request.state, "user", None)
    if not actor:
        raise HTTPException(status_code=401, detail="Authentication is required")
    today = datetime.now(timezone.utc).date()
    start = req.date_from or req.opening_equity_date + timedelta(days=1)
    end = req.date_to or req.closing_equity_date
    if (req.opening_equity_date > req.closing_equity_date or req.closing_equity_date > today
            or end > today or (start > end and (req.date_from is not None or req.date_to is not None
                                                or req.opening_equity_date != req.closing_equity_date))):
        raise HTTPException(status_code=400, detail="Choose ordered UTC dates with no future date.")
    if not req.selected_groups and not req.group_filter_rules.strip():
        raise HTTPException(status_code=400, detail="Select at least one MT5 group.")
    job_id = _audited_brokerage_job_id(req, int(actor["id"]))
    with _AUDITED_BROKERAGE_LOCK:
        now = time_module.monotonic()
        for key, old in list(_AUDITED_BROKERAGE_JOBS.items()):
            if old["status"] != "warming" and now - old.get("completed_at", now) > _AUDITED_BROKERAGE_TTL:
                del _AUDITED_BROKERAGE_JOBS[key]
        existing = _AUDITED_BROKERAGE_JOBS.get(job_id)
        if existing and (existing["status"] == "warming" or (existing["status"] == "ready" and not req.force_refresh)):
            return _audited_brokerage_result(existing)
        if sum(item["status"] == "warming" for item in _AUDITED_BROKERAGE_JOBS.values()) >= _AUDITED_BROKERAGE_MAX_PENDING:
            raise HTTPException(status_code=429, detail="Audited report queue is full; retry after a report completes.")
        generation = (existing or {}).get("generation", 0) + 1
        _AUDITED_BROKERAGE_JOBS[job_id] = {"job_id": job_id, "actor_id": int(actor["id"]), "status": "warming", "started_at": now, "generation": generation}
        _AUDITED_BROKERAGE_EXECUTOR.submit(_compute_audited_brokerage, job_id, generation, req)
    return {"status": "warming", "job_id": job_id}


@api.get("/api/reports/brokerage/audited/{job_id}")
def get_audited_brokerage(job_id: str, request: Request):
    actor = getattr(request.state, "user", None)
    with _AUDITED_BROKERAGE_LOCK:
        job = _AUDITED_BROKERAGE_JOBS.get(job_id)
        if not actor or not job or job["actor_id"] != int(actor["id"]):
            raise HTTPException(status_code=404, detail="Report not found")
        return _audited_brokerage_result(job)


@api.post("/api/reports/brokerage/audited/{job_id}/review")
def review_audited_transaction(job_id: str, req: AuditedTransactionReviewRequest, request: Request):
    actor = _risk_admin_actor(request)
    user = request.state.user
    if not math.isfinite(req.expected_profit):
        raise HTTPException(status_code=400, detail="The reviewed amount must be finite.")
    with _AUDITED_BROKERAGE_LOCK:
        job = _AUDITED_BROKERAGE_JOBS.get(job_id)
        if not job or job["status"] != "ready" or job["actor_id"] != int(user["id"]):
            raise HTTPException(status_code=404, detail="Audited report not found.")
        audit = job["report"]["audit"]
        matching = audit.loc[
            audit["Login"].eq(req.login) & audit["Deal"].eq(req.deal)
            & audit["Action"].eq(req.action) & audit["Review"].eq("Unresolved")
        ] if not audit.empty else audit
        raw_amount = brokerage_pnl._to_float(matching.iloc[0]["Raw Amount"]) if len(matching) == 1 else None
        if raw_amount is None or not math.isclose(raw_amount, req.expected_profit, abs_tol=1e-9):
            raise HTTPException(status_code=400, detail="The ticket, action, or amount does not match one unresolved transaction in this report.")
        if len(req.reason.strip()) < 10:
            raise HTTPException(status_code=400, detail="Provide a factual review reason of at least 10 characters.")
        record = matching.iloc[0]
        rule = {
            "login": req.login, "deal": req.deal, "action": req.action,
            "expected_profit": req.expected_profit, "category": req.category,
            "reason": req.reason.strip(),
        }
        try:
            brokerage_pnl.classify_balance_transaction({
                "Login": req.login, "Deal": req.deal, "Action": req.action,
                "Profit": req.expected_profit, "Group": record["Group"],
            }, [rule])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        rules = brokerage_pnl.load_transaction_rules()
        if any(int(item.get("deal", 0) or 0) == req.deal for item in rules):
            raise HTTPException(status_code=409, detail="This deal already has a reviewed rule.")
        store = brokerage_pnl.TRANSACTION_RULES_STORE
        store.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=store.parent,
                                             prefix=".transaction-rules-", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                json.dump([*rules, rule], file, indent=2)
            temporary.replace(store)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    write_audit(actor, "brokerage.transaction_reviewed", "mt5_deal", req.deal, rule)
    return {"ok": True, "deal": req.deal, "category": req.category}


@api.get("/api/reports/brokerage/audited/{job_id}/export")
def export_audited_brokerage(job_id: str, request: Request):
    actor = getattr(request.state, "user", None)
    with _AUDITED_BROKERAGE_LOCK:
        job = _AUDITED_BROKERAGE_JOBS.get(job_id)
        if not actor or not job or job["actor_id"] != int(actor["id"]) or job["status"] != "ready":
            raise HTTPException(status_code=404, detail="Completed report not found")
        report = job["report"]
    summary = brokerage_pnl.aggregate_summary(report["accounts"], report["coverage"], report["aligned"])
    workbook = brokerage_pnl.excel_export(report, summary)
    filename = f"AuditedBrokerage_{report['opening'].isoformat()}_to_{report['closing'].isoformat()}.xlsx"
    return Response(content=workbook, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@api.post("/api/reports/diff")
def get_diff_report(req: BrokerageReportRequest):
    if req.opening_equity_date > req.closing_equity_date:
        raise HTTPException(status_code=400, detail="opening_equity_date must be on or before closing_equity_date.")
    summary_from = req.date_from or (req.opening_equity_date + timedelta(days=1))
    summary_to = req.date_to or req.closing_equity_date

    with yielding_history_manager_session() as api_handle:
        groups, _, group_warnings = _resolve_groups_for_request(
            api_handle, req.selected_groups, req.group_filter_rules
        )
        payload = build_brokerage_export_payload(
            api_handle,
            groups,
            req.opening_equity_date,
            req.closing_equity_date,
            summary_from,
            summary_to,
            clip_negative_equity=False,
            excluded_logins=req.excluded_logins,
        )

    summary_with_total = append_total_row(payload["trade_summary"])
    snapshot_opening = payload["opening_snapshot"]
    snapshot_closing = payload["closing_snapshot"]
    brokerage_totals = payload["brokerage_totals"]

    return {
        "selected_groups": groups,
        "opening_equity_date": req.opening_equity_date.isoformat(),
        "closing_equity_date": req.closing_equity_date.isoformat(),
        "date_from": summary_from.isoformat(),
        "date_to": summary_to.isoformat(),
        "opening_report": _dataframe_to_records(snapshot_opening),
        "closing_report": _dataframe_to_records(snapshot_closing),
        "summary": _dataframe_to_records(payload["trade_summary"]),
        "summary_with_total": _dataframe_to_records(summary_with_total),
        "accounts_display": _dataframe_to_records(payload["accounts_display"]),
        "group_summary": _dataframe_to_records(payload["group_summary"]),
        "books_summary": _dataframe_to_records(payload["books_summary"]),
        "daily_summary": _dataframe_to_records(payload["daily_summary"]),
        "lp_summary": _dataframe_to_records(payload["lp_summary"]),
        "brokerage_totals": payload["brokerage_totals"],
        "data_quality": payload["data_quality"],
        "summary_range": [summary_from.isoformat(), summary_to.isoformat()],
        "opening_equity_total": round(float(brokerage_totals.get("opening_equity", 0.0)), 2),
        "closing_equity_total": round(float(brokerage_totals.get("closing_equity", 0.0)), 2),
        "equity_difference": round(float(brokerage_totals.get("difference", 0.0)), 2),
        "total_lp_pnl": round(float(brokerage_totals.get("total_lp_pnl", 0.0)), 2),
        "client_a_book_pnl": round(float(brokerage_totals.get("client_a_book_pnl", 0.0)), 2),
        "net_brokerage": round(float(brokerage_totals.get("net_brokerage", 0.0)), 2),
        "profile_name": req.profile_name or "",
        "warnings": _dedupe_warnings(group_warnings, payload.get("warnings", [])),
    }


@api.post("/analytics")
@api.post("/api/analytics")
def get_analytics(req: AnalyticsRequest):
    try:
        opening_equity_date, closing_equity_date, summary_from, summary_to = _resolve_analytics_date_window(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    requested_groups = req.groups or req.selected_groups

    with yielding_history_manager_session() as api_handle:
        groups, _, group_warnings = _resolve_groups_for_request(
            api_handle, requested_groups, req.group_filter_rules
        )
        if not groups:
            return {
                "equity": {"opening": 0.0, "closing": 0.0, "brokerPnL": 0.0},
                "summary": [],
                "warnings": group_warnings or ["No groups matched the current selection."],
            }

        payload = build_brokerage_export_payload(
            api_handle,
            groups,
            opening_equity_date,
            closing_equity_date,
            summary_from,
            summary_to,
            excluded_logins=req.excluded_logins,
        )

    brokerage_totals = payload["brokerage_totals"]
    return {
        "equity": {
            "opening": round(float(brokerage_totals.get("opening_equity", 0.0)), 2),
            "closing": round(float(brokerage_totals.get("closing_equity", 0.0)), 2),
            "brokerPnL": round(float(brokerage_totals.get("net_brokerage", 0.0)), 2),
        },
        "summary": _dataframe_to_records(payload["trade_summary"]),
        "warnings": _dedupe_warnings(group_warnings, payload.get("warnings", [])),
    }


@api.post("/api/reports/brokerage/export")
def export_brokerage_report(req: BrokerageExportRequest):
    if req.opening_equity_date > req.closing_equity_date:
        raise HTTPException(status_code=400, detail="opening_equity_date must be on or before closing_equity_date.")
    summary_from = req.date_from or (req.opening_equity_date + timedelta(days=1))
    summary_to = req.date_to or req.closing_equity_date
    if _looks_like_brokerage_export_payload(req.cached_report):
        payload = dict(req.cached_report)
        payload.setdefault("summary_range", [summary_from.isoformat(), summary_to.isoformat()])
        payload.setdefault("warnings", [])
        payload.setdefault("lp_summary", [])
        payload.setdefault("profile_name", req.profile_name or "")
    else:
        with yielding_history_manager_session() as api_handle:
            groups, _, group_warnings = _resolve_groups_for_request(
                api_handle, req.selected_groups, req.group_filter_rules
            )
            payload = build_brokerage_export_payload(
                api_handle,
                groups,
                req.opening_equity_date,
                req.closing_equity_date,
                summary_from,
                summary_to,
                clip_negative_equity=False,
                excluded_logins=req.excluded_logins,
            )
            payload["selected_groups"] = groups
            payload["profile_name"] = req.profile_name or ""

        if group_warnings:
            payload["warnings"] = _dedupe_warnings(group_warnings, payload.get("warnings", []))

    payload["selected_groups"] = [
        str(group).strip()
        for group in (payload.get("selected_groups") or req.selected_groups or [])
        if str(group).strip()
    ]
    payload["clip_negative_equity"] = False

    buf = build_brokerage_export_workbook(payload)

    filename = f"Brokerage_{req.opening_equity_date.isoformat()}_to_{req.closing_equity_date.isoformat()}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _alert_key(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@api.get("/api/risk-automation/overview")
def risk_automation_overview(
    limit: int = Query(200, ge=10, le=1000),
    include_clients: bool = False,
    include_audit: bool = False,
    client_limit: int = Query(500, ge=50, le=5000),
):
    payload = RISK_AUTOMATION.overview(limit)
    import mt5_connection
    manager = mt5_connection.get_manager()
    if manager and manager.is_connected:
        payload["groups"] = manager.group_cache_view()
        payload["manager"] = {"status": "connected"}
        if include_clients:
            snapshot = manager.snapshot()
            clients = RISK_AUTOMATION.client_metrics(snapshot)
            payload["client_total"] = len(clients)
            payload["clients"] = clients[:client_limit]
            payload["groups"] = sorted(
                set(payload["groups"]) | {row["group"] for row in clients if row["group"]},
                key=lambda value: (value.lower(), value),
            )
            payload["manager"]["source_updated_at"] = snapshot.last_update
        else:
            payload["clients"] = []
    else:
        payload.update(clients=[], groups=[], manager={"status": "offline"})
    payload["audit"] = []
    if include_audit:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM audit_log WHERE action LIKE 'risk_%' ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        for row in rows:
            item = dict(row)
            item["details"] = _json_column(item.pop("details_json", "{}"), {})
            payload["audit"].append(item)
    payload["permissions"] = {"view": True, "manage": "administrator", "approve": "administrator", "manual_override": "administrator"}
    return payload


@api.post("/api/risk-automation/rules")
def create_risk_rule(req: RiskRuleRequest, request: Request):
    actor = _risk_admin_actor(request)
    return RISK_AUTOMATION.save_rule(req.model_dump(), actor)


@api.put("/api/risk-automation/rules/{rule_id}")
def update_risk_rule(rule_id: int, req: RiskRuleRequest, request: Request):
    actor = _risk_admin_actor(request)
    RISK_AUTOMATION.run_lock.acquire()
    try:
        try:
            return RISK_AUTOMATION.save_rule(req.model_dump(), actor, rule_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        RISK_AUTOMATION.run_lock.release()


@api.delete("/api/risk-automation/rules/{rule_id}")
def delete_risk_rule(rule_id: int, request: Request):
    actor = _risk_admin_actor(request)
    try:
        RISK_AUTOMATION.delete_rule(rule_id, actor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@api.post("/api/risk-automation/rules/{rule_id}/simulate")
def simulate_risk_rule(rule_id: int, request: Request):
    _risk_admin_actor(request)
    rule = RISK_AUTOMATION.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Risk automation rule was not found")
    try:
        return RISK_AUTOMATION.simulate(rule)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/api/risk-automation/run")
def run_risk_automation(request: Request, rule_id: int | None = Query(None, ge=1)):
    actor = _risk_admin_actor(request)
    try:
        return RISK_AUTOMATION.run(rule_id=rule_id, actor=actor)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/api/risk-automation/approvals/{approval_id}")
def decide_risk_approval(approval_id: int, req: RiskApprovalDecision, request: Request):
    actor = _risk_admin_actor(request)
    try:
        return RISK_AUTOMATION.decide_approval(approval_id, req.decision, actor, req.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/api/risk-automation/manual-override")
def manual_risk_override(req: RiskManualOverride, request: Request):
    actor = _risk_admin_actor(request)
    try:
        return RISK_AUTOMATION.manual_override(
            req.client_login, req.client_name, req.current_group,
            [action.model_dump() for action in req.actions], actor, req.reason,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _operations_should_notify(key: str, retention_seconds: int = 300) -> bool:
    now = time_module.time()
    token = f"operations:{key}"
    with _TELEGRAM_LOCK:
        previous = _TELEGRAM_SENT.get(token, 0.0)
        if now - previous < retention_seconds:
            return False
        _TELEGRAM_SENT[token] = now
    return True


def _operations_live_payload(req: OperationsRequest) -> dict[str, Any]:
    import mt5_connection

    mgr = mt5_connection.get_manager()
    if not mgr or not mgr.is_connected:
        return {"status": "offline", "generated_at": datetime.now(timezone.utc).isoformat(), "alerts": [], "related_accounts": [], "coordinated_positions": [], "limits": [], "summary": {}, "warnings": []}
    snapshot = mgr.snapshot()
    now_epoch = time_module.time()
    data_age_seconds = max(0.0, now_epoch - snapshot.last_update) if snapshot.last_update else None
    stale_after = max(float(os.getenv("LIVE_CACHE_STALE_SECONDS", "10")), 2.0)
    cache_stale = data_age_seconds is None or data_age_seconds > stale_after or snapshot.status == "error"
    available_groups = sorted(set(snapshot.groups) | {str(row.get("Group", "")).strip() for row in snapshot.users if str(row.get("Group", "")).strip()})
    groups, _, warnings = _resolve_groups_from_available(available_groups, req.selected_groups, req.group_filter_rules)
    warnings.extend(snapshot.errors)
    if cache_stale:
        warnings.append("MT5 live cache is stale; values may not reflect the latest server state.")
    if not groups and not req.selected_groups and not req.group_filter_rules:
        groups = available_groups
    if callable(getattr(mgr, "live_risk_cache_view", None)):
        risk_view = mgr.live_risk_cache_view(groups=groups)
        risk_users, risk_positions = risk_view["users"], risk_view["positions"]
    else:
        risk_users, risk_positions = snapshot.users, snapshot.positions
    risk = build_live_risk_snapshot_from_cache(risk_users, risk_positions, groups, req.excluded_logins, row_limit=None)
    accounts = risk.get("accounts", [])
    positions = risk.get("positions", [])
    symbols = risk.get("symbols", [])
    alerts: list[dict[str, Any]] = []

    def add_alert(kind: str, severity: str, title: str, detail: str, *, login: int = 0, group: str = "", symbol: str = "", value: Any = None, source: str = "MT5 manager cache") -> None:
        key = _alert_key(kind, login, group, symbol, title, value)
        alerts.append({"key": key, "kind": kind, "severity": severity, "title": title, "detail": detail, "login": login, "group": group, "symbol": symbol, "value": value, "source": source, "observed_at": risk.get("generated_at")})

    for account in accounts:
        severity = str(account.get("severity", "normal"))
        login = _to_int(account.get("login"), 0) or 0
        if severity != "normal":
            add_alert("account_risk", "critical" if severity == "critical" else "high" if severity in {"high", "elevated"} else "warning", f"Account risk #{login}", " · ".join(account.get("signals") or ["Live risk threshold reached"]), login=login, group=account.get("group", ""), value=account.get("risk_score"))
        if account.get("vps_status") == "detected":
            add_alert("vps", "high", f"VPS detected #{login}", " · ".join(account.get("vps_evidence") or ["Hosting network evidence"]), login=login, group=account.get("group", ""), source="MT5 LastIP + public network intelligence")
        balance = abs(_to_float(account.get("balance"), 0.0))
        floating = _to_float(account.get("floating_pnl"), 0.0)
        if balance > 0 and floating > 0 and floating / balance * 100 >= PROFIT_ALERT_PCT_THRESHOLD:
            add_alert("unusual_profit", "high", f"Unusual floating profit #{login}", f"Floating profit is {round(floating / balance * 100, 1)}% of balance", login=login, group=account.get("group", ""), value=round(floating, 2))

    for position in positions:
        lots = _to_float(position.get("volume"), 0.0)
        if lots >= HIGH_LOT_ALERT_THRESHOLD:
            add_alert("large_exposure", "high", f"Large position #{position.get('ticket') or position.get('position_id')}", f"{lots:g} lots {position.get('action', '')} {position.get('symbol', '')}", login=_to_int(position.get("login"), 0) or 0, symbol=position.get("symbol", ""), value=lots)

    with _conn() as con:
        acknowledgements = {row["alert_key"]: dict(row) for row in con.execute("SELECT * FROM alert_acknowledgements").fetchall()}
        limits = [dict(row) for row in con.execute("SELECT * FROM exposure_limits ORDER BY enabled DESC, id DESC").fetchall()]

    account_by_login = {str(row.get("login")): row for row in accounts}
    symbol_by_name = {str(row.get("symbol")): row for row in symbols}
    group_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in accounts:
        group_rows[str(row.get("group", ""))].append(row)

    evaluated_limits = []
    for limit in limits:
        value, source = evaluate_limit_value(limit, accounts, positions)
        threshold = _to_float(limit.get("threshold"), 0.0)
        inverse = limit.get("metric") == "margin_level_below"
        breached = bool(not cache_stale and limit.get("enabled") and value is not None and (value <= threshold if inverse else value >= threshold))
        warning_line = threshold / max(_to_float(limit.get("warning_pct"), 80.0) / 100.0, 0.01) if inverse else threshold * _to_float(limit.get("warning_pct"), 80.0) / 100.0
        warning = bool(not cache_stale and limit.get("enabled") and value is not None and not breached and (value <= warning_line if inverse else value >= warning_line))
        evaluated = limit | {"current_value": value, "source": source, "breached": breached, "warning": warning,
                             "evaluation_state": "disabled" if not limit.get("enabled") else "stale" if cache_stale else "missing_source" if value is None else "breached" if breached else "warning" if warning else "within_limit"}
        evaluated_limits.append(evaluated)
        if breached:
            add_alert("exposure_limit", limit.get("escalation_level", "warning"), f"{limit['scope_type'].title()} limit breached", f"{limit['scope_value']} {limit['metric']} is {round(value or 0, 2)}; limit {threshold}", value=value, source=source)

    users_by_ip: dict[str, list[dict[str, Any]]] = defaultdict(list)
    users_by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    allowed_groups = set(groups)
    for user in snapshot.users:
        if str(user.get("Group", "")) not in allowed_groups: continue
        login = _to_int(user.get("Login"), 0) or 0
        item = {"login": login, "name": str(user.get("Name", "")), "group": str(user.get("Group", ""))}
        ip = _ip_text(user.get("LastIP", ""))
        if ip: users_by_ip[ip].append(item)
        agent = str(user.get("Agent", user.get("AgentAccount", "")) or "").strip()
        if agent and agent != "0": users_by_agent[agent].append(item)
    related = []
    for ip, members in users_by_ip.items():
        if len(members) > 1: related.append({"type": "shared_login_ip", "value": ip, "accounts": members, "count": len(members), "source": "MT5 User LastIP", "confidence": "verified_shared_attribute"})
    for agent, members in users_by_agent.items():
        if len(members) > 1: related.append({"type": "shared_agent", "value": agent, "accounts": members, "count": len(members), "source": "MT5 User Agent", "confidence": "verified_shared_attribute"})

    coordinated = []
    ordered = sorted(positions, key=lambda row: str(row.get("opened_at", "")))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:index + 80]:
            if left.get("login") == right.get("login") or left.get("symbol") != right.get("symbol") or left.get("action") != right.get("action"): continue
            try:
                gap = abs((datetime.fromisoformat(str(right.get("opened_at")).replace("Z", "+00:00")) - datetime.fromisoformat(str(left.get("opened_at")).replace("Z", "+00:00"))).total_seconds())
            except Exception: continue
            if gap <= 5:
                coordinated.append({"symbol": left.get("symbol"), "side": left.get("action"), "time_gap_seconds": round(gap, 3), "accounts": [left.get("login"), right.get("login")], "positions": [left.get("ticket") or left.get("position_id"), right.get("ticket") or right.get("position_id")], "source": "MT5 open positions", "classification": "timing_similarity_not_proof"})
                if len(coordinated) >= 100: break
        if len(coordinated) >= 100: break

    for alert in alerts:
        ack = acknowledgements.get(alert["key"])
        alert["acknowledged"] = bool(ack)
        alert["acknowledged_by"] = ack.get("actor") if ack else ""
        if alert["kind"] == "exposure_limit" and not ack and _operations_should_notify(alert["key"]):
            threading.Thread(target=dispatch_operations_alert, args=(alert,), daemon=True).start()
    alerts.sort(key=lambda item: (_severity_rank(item["severity"]), item.get("title", "")), reverse=True)
    return {"status": "stale" if cache_stale else "connected", "generated_at": risk.get("generated_at"), "source_updated_at": snapshot.last_update, "data_age_seconds": round(data_age_seconds, 3) if data_age_seconds is not None else None, "alerts": alerts, "related_accounts": related, "coordinated_positions": coordinated, "limits": evaluated_limits, "summary": {"alerts": len(alerts), "critical": sum(_severity_rank(row["severity"]) >= 3 for row in alerts), "open_cases": _db_scalar("SELECT COUNT(*) FROM investigation_cases WHERE status NOT IN ('resolved','closed')"), "related_clusters": len(related), "coordinated_signals": len(coordinated), "limit_breaches": sum(row["breached"] for row in evaluated_limits)}, "warnings": _dedupe_warnings(warnings)}


def _db_scalar(sql: str, params: tuple[Any, ...] = ()) -> Any:
    with _conn() as con:
        row = con.execute(sql, params).fetchone()
    return row[0] if row else 0


@api.post("/api/operations/live")
def operations_live(req: OperationsRequest):
    return _operations_live_payload(req)


@api.get("/api/operations/cases")
def list_cases():
    with _conn() as con:
        rows = con.execute("SELECT * FROM investigation_cases ORDER BY CASE priority WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC, updated_at DESC").fetchall()
    return {"cases": [_case_record(row) for row in rows]}


def _risk_event_record(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["evidence"] = _json_column(data.pop("evidence_json", "{}"), {})
    data["thresholds"] = _json_column(data.pop("thresholds_json", "{}"), {})
    data["actual_values"] = _json_column(data.pop("actual_values_json", "{}"), {})
    return data


@api.get("/api/operations/risk-events")
def list_risk_events(request: Request, login: int | None = Query(None, ge=1), status: str | None = Query(None), limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    _operations_admin_actor(request)
    filters = []
    params: list[Any] = []
    if login is not None:
        filters.append("login=?")
        params.append(login)
    if status:
        if status not in {"NEW", "ACKNOWLEDGED", "UNDER_REVIEW", "ESCALATED", "RESOLVED", "CLOSED"}:
            raise HTTPException(status_code=400, detail="Unknown risk event status")
        filters.append("status=?")
        params.append(status)
    where = " WHERE " + " AND ".join(filters) if filters else ""
    with _conn() as con:
        total = con.execute("SELECT COUNT(*) FROM risk_events" + where, tuple(params)).fetchone()[0]
        rows = con.execute("SELECT * FROM risk_events" + where + " ORDER BY last_seen_at DESC,event_id LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
    return {"total": total, "events": [_risk_event_record(row) for row in rows]}


@api.get("/api/notifications/in-app")
def in_app_notifications(request: Request, limit: int = Query(default=50, ge=1, le=100)):
    """Read recent persisted Anti-Fraud alerts without polling MT5."""
    _operations_admin_actor(request)
    with _conn() as con:
        rows = con.execute(
            """SELECT event_id,detector,login,account_group,severity,detected_at,
                      first_seen_at,status,evidence_json,data_quality
               FROM risk_events WHERE event_type='antifraud'
               ORDER BY first_seen_at DESC,event_id DESC LIMIT ?""", (limit,),
        ).fetchall()
    return {"events": [{
        "event_id": row["event_id"], "detector": row["detector"],
        "login": row["login"], "group": row["account_group"],
        "severity": row["severity"], "occurred_at": row["detected_at"],
        "first_seen_at": row["first_seen_at"], "status": row["status"],
        "data_quality": row["data_quality"],
        "evidence": _json_column(row["evidence_json"], {}).get("evidence", ""),
    } for row in rows]}


@api.get("/api/operations/risk-events/{event_id}")
def risk_event_detail(event_id: str, request: Request):
    _operations_admin_actor(request)
    with _conn() as con:
        row = con.execute("SELECT * FROM risk_events WHERE event_id=?", (event_id,)).fetchone()
        actions = con.execute("SELECT id,actor,action,note,created_at FROM risk_event_actions WHERE event_id=? ORDER BY id", (event_id,)).fetchall()
    if row is None:
        raise HTTPException(status_code=404, detail="Risk event not found")
    return {"event": _risk_event_record(row), "actions": [dict(item) for item in actions]}


@api.post("/api/operations/risk-events/{event_id}/status")
def update_risk_event_status(event_id: str, req: RiskEventActionRequest, request: Request):
    actor = _operations_admin_actor(request)
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        existing = con.execute("SELECT * FROM risk_events WHERE event_id=?", (event_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Risk event not found")
        if req.case_id is not None:
            case = con.execute("SELECT client_login FROM investigation_cases WHERE id=?", (req.case_id,)).fetchone()
            if case is None or int(case["client_login"]) != int(existing["login"] or 0):
                raise HTTPException(status_code=400, detail="Case must exist for the same client")
        assigned = req.assigned_to.strip() if req.assigned_to is not None else existing["assigned_to"]
        con.execute("""UPDATE risk_events SET status=?,assigned_to=?,case_id=?,acknowledged_by=CASE WHEN ?='ACKNOWLEDGED' THEN ? ELSE acknowledged_by END,acknowledged_at=CASE WHEN ?='ACKNOWLEDGED' THEN ? ELSE acknowledged_at END,resolved_by=CASE WHEN ? IN ('RESOLVED','CLOSED') THEN ? ELSE resolved_by END,resolved_at=CASE WHEN ? IN ('RESOLVED','CLOSED') THEN ? ELSE resolved_at END WHERE event_id=?""", (req.status, assigned, req.case_id if req.case_id is not None else existing["case_id"], req.status, actor, req.status, now, req.status, actor, req.status, now, event_id))
        con.execute("INSERT INTO risk_event_actions(event_id,actor,action,note) VALUES (?,?,?,?)", (event_id, actor, req.status, req.note.strip()))
        updated = con.execute("SELECT * FROM risk_events WHERE event_id=?", (event_id,)).fetchone()
    write_audit(actor, "risk_event.status_changed", "risk_event", event_id, {"from": existing["status"], "to": req.status, "assigned_to": assigned, "case_id": req.case_id, "note": req.note.strip()})
    return _risk_event_record(updated)


def _online_field(row: Any, name: str, default: Any = "") -> Any:
    if isinstance(row, dict):
        return row.get(name, row.get(name.lower(), default))
    candidate = getattr(row, name, default)
    try:
        return candidate() if callable(candidate) else candidate
    except Exception:
        return default


def _capture_online_cid_sessions() -> list[dict[str, Any]]:
    """Read and persist the current authoritative MT5 OnlineGetArray CIDs."""
    import mt5_connection

    manager = mt5_connection.get_manager()
    if not manager or not manager.is_connected:
        raise RuntimeError("The MT5 Manager connection is offline")
    online_rows = manager.call("OnlineGetArray") or []
    if isinstance(online_rows, tuple):
        online_rows = next(
            (part for part in online_rows if isinstance(part, (list, tuple))), []
        )
    online_rows = list(online_rows)
    if getattr(manager, "native_mode", False):
        users = online_account_identities(manager,{
            int(_online_field(row,"Login",0) or 0) for row in online_rows})
    else:
        live_cache = manager.live_risk_cache_view()
        users = {int(row.get("Login",0) or 0):row for row in live_cache.get("users",[])}
    sessions: list[dict[str, Any]] = []
    for row in list(online_rows):
        row_login = int(_online_field(row, "Login", 0) or 0)
        cid = str(_online_field(row, "ComputerID", "") or "").strip()
        if not row_login:
            continue
        user = users.get(row_login, {})
        sessions.append({
            "login": row_login,
            "name": str(user.get("Name", "")),
            "group": str(_online_field(row, "Group", user.get("Group", "")) or ""),
            "computer_id": cid,
            "address": str(_online_field(row, "Address", "") or ""),
            "session_id": str(_online_field(row, "SessionID", "") or ""),
            "connection_type": str(_online_field(row, "Type", "") or ""),
            "build": int(_online_field(row, "Build", 0) or 0),
            "connected_at": str(_online_field(row, "Time", "") or ""),
            "online": True,
        })
    with _conn() as con:
        for row in sessions:
            if not row["computer_id"]:
                continue
            con.execute(
                """INSERT INTO cid_observations
                   (login,computer_id,name,account_group,address) VALUES (?,?,?,?,?)
                   ON CONFLICT(login,computer_id) DO UPDATE SET
                     name=excluded.name, account_group=excluded.account_group,
                     address=excluded.address, last_seen=datetime('now')""",
                (row["login"], row["computer_id"], row["name"], row["group"], row["address"]),
            )
    return sessions


def _store_cid_login_events(events: list[dict[str, Any]]) -> int:
    """Persist parsed journal evidence and make it available to CID linkage."""
    with _conn() as con:
        before = con.total_changes
        for row in events:
            con.execute(
                """INSERT OR IGNORE INTO cid_login_events
                   (event_key,occurred_at,login,computer_id,ip_address,device_type,
                    build,access_server,ping_ms,country,city,isp,raw_message)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["event_key"], row["occurred_at"] or datetime.now(timezone.utc).isoformat(),
                    row["login"], row["computer_id"], row["ip_address"],
                    row["device_type"], row["build"], row["access_server"],
                    row["ping_ms"], row["country"], row["city"], row["isp"],
                    row["raw_message"],
                ),
            )
            con.execute(
                """INSERT INTO cid_observations
                   (login,computer_id,address,first_seen,last_seen) VALUES (?,?,?,?,?)
                   ON CONFLICT(login,computer_id) DO UPDATE SET
                     address=excluded.address,
                     first_seen=MIN(first_seen,excluded.first_seen),
                     last_seen=MAX(last_seen,excluded.last_seen)""",
                (
                    row["login"], row["computer_id"], row["ip_address"],
                    row["occurred_at"] or datetime.now(timezone.utc).isoformat(),
                    row["occurred_at"] or datetime.now(timezone.utc).isoformat(),
                ),
            )
        return con.total_changes - before


@api.post("/api/operations/cid-audit")
def operation_cid_audit(req: CidAuditRequest):
    """Fetch, retain, and analyze historical CID login evidence from MT5 journals."""
    now = datetime.now(timezone.utc)
    if req.all_history:
        range_start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        range_end = now
        range_mode = "all_history"
    elif req.from_date and req.to_date:
        range_start = datetime.combine(req.from_date, time.min, tzinfo=timezone.utc)
        range_end = datetime.combine(req.to_date, time.max, tzinfo=timezone.utc)
        range_mode = "custom"
    else:
        range_end = now
        range_start = now - timedelta(hours=req.lookback_hours)
        range_mode = "lookback"
    from_ts = int(range_start.timestamp())
    to_ts = int(range_end.timestamp())
    events, fetch_error = request_cid_login_journal_isolated(
        req.server, req.login, req.password, from_ts, to_ts
    )
    _store_cid_login_events(events)
    with _conn() as con:
        stored = [
            dict(row) for row in con.execute(
                """SELECT event_key,occurred_at,login,computer_id,ip_address,
                          device_type,build,access_server,ping_ms,country,city,isp,raw_message
                   FROM cid_login_events
                   WHERE occurred_at>=? AND occurred_at<=?
                   ORDER BY occurred_at DESC
                   LIMIT 10000""",
                (range_start.isoformat(), range_end.isoformat()),
            ).fetchall()
        ]
    payload = analyze_cid_login_events(stored)
    payload.update({
        "status": "ok" if stored else "no_data",
        "lookback_hours": req.lookback_hours,
        "range_mode": range_mode,
        "from_date": range_start.date().isoformat(),
        "to_date": range_end.date().isoformat(),
        "source": "MT5 Manager LoggerServerRequest client-login journal",
        "source_updated_at": datetime.now(timezone.utc).isoformat(),
        "fresh_events": len(events),
        "warning": fetch_error or "",
    })
    return payload


@api.post("/api/operations/cases")
def create_case(req: CaseCreateRequest, request: Request):
    actor = _operations_admin_actor(request)
    with _conn() as con:
        cursor = con.execute("INSERT INTO investigation_cases (client_login,title,assigned_to,status,priority,summary,evidence_json,created_by) VALUES (?,?,?,?,?,?,?,?)", (req.client_login, req.title, req.assigned_to, req.status, req.priority, req.summary, json.dumps(req.evidence, default=str), actor))
        case_id = cursor.lastrowid
        con.execute("INSERT INTO case_events (case_id,action,actor,details_json) VALUES (?,?,?,?)", (case_id, "created", actor, json.dumps(req.model_dump(exclude={"evidence"}), default=str)))
        row = con.execute("SELECT * FROM investigation_cases WHERE id=?", (case_id,)).fetchone()
    write_audit(actor, "case.created", "case", case_id, {"client_login": req.client_login, "priority": req.priority})
    return _case_record(row)


@api.put("/api/operations/cases/{case_id}")
def update_case(case_id: int, req: CaseUpdateRequest, request: Request):
    actor = _operations_admin_actor(request)
    updates = {key: value for key, value in req.model_dump(exclude={"actor", "note"}).items() if value is not None}
    if "evidence" in updates: updates["evidence_json"] = json.dumps(updates.pop("evidence"), default=str)
    with _conn() as con:
        if updates:
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            con.execute(f"UPDATE investigation_cases SET {', '.join(f'{key}=?' for key in updates)} WHERE id=?", (*updates.values(), case_id))
        details = {"changes": updates, "note": req.note}
        con.execute("INSERT INTO case_events (case_id,action,actor,details_json) VALUES (?,?,?,?)", (case_id, "updated", actor, json.dumps(details, default=str)))
        row = con.execute("SELECT * FROM investigation_cases WHERE id=?", (case_id,)).fetchone()
    if not row: raise HTTPException(status_code=404, detail="Case not found")
    write_audit(actor, "case.updated", "case", case_id, details)
    return _case_record(row)


@api.delete("/api/operations/cases/{case_id}")
def delete_case(case_id: int, request: Request):
    actor = _operations_admin_actor(request)
    with _conn() as con:
        row = con.execute("SELECT * FROM investigation_cases WHERE id=?", (case_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Investigation case was not found")
        previous = _case_record(row)
        con.execute("DELETE FROM investigation_cases WHERE id=?", (case_id,))
    write_audit(actor, "case.deleted", "case", case_id, {
        "client_login": previous["client_login"],
        "title": previous["title"],
    })
    return {"ok": True, "id": case_id}


@api.get("/api/operations/cases/{case_id}/history")
def case_history(case_id: int):
    with _conn() as con:
        rows = [dict(row) for row in con.execute("SELECT * FROM case_events WHERE case_id=? ORDER BY id DESC", (case_id,)).fetchall()]
    for row in rows: row["details"] = _json_column(row.pop("details_json", "{}"), {})
    return {"events": rows}


@api.post("/api/operations/reviews")
def save_dealer_review(req: DealerReviewRequest, request: Request):
    actor = _operations_admin_actor(request)
    with _conn() as con:
        row = con.execute("SELECT id FROM investigation_cases WHERE client_login=? AND status NOT IN ('resolved','closed') ORDER BY id DESC LIMIT 1", (req.client_login,)).fetchone()
        if row:
            case_id = row["id"]
            con.execute("UPDATE investigation_cases SET summary=?, assigned_to=CASE WHEN assigned_to='' THEN ? ELSE assigned_to END, status='monitoring', updated_at=datetime('now') WHERE id=?", (req.notes, actor, case_id))
        else:
            cursor = con.execute("INSERT INTO investigation_cases (client_login,title,assigned_to,status,priority,summary,created_by) VALUES (?,?,?,?,?,?,?)", (req.client_login, f"Dealer review #{req.client_login}", actor, "monitoring", "medium", req.notes, actor))
            case_id = cursor.lastrowid
        con.execute("INSERT INTO case_events (case_id,action,actor,details_json) VALUES (?,?,?,?)", (case_id, "dealer_review", actor, json.dumps({"decision": req.decision, "notes": req.notes})))
    write_audit(actor, "dealer_review.saved", "client", req.client_login, {"decision": req.decision, "case_id": case_id})
    return {"ok": True, "case_id": case_id}


@api.get("/api/operations/limits")
def list_limits():
    with _conn() as con: rows = [dict(row) for row in con.execute("SELECT * FROM exposure_limits ORDER BY id DESC").fetchall()]
    return {"limits": rows}


@api.post("/api/operations/limits")
def create_limit(req: ExposureLimitRequest, request: Request):
    actor = _operations_admin_actor(request)
    with _conn() as con:
        cursor = con.execute("INSERT INTO exposure_limits (scope_type,scope_value,metric,threshold,warning_pct,escalation_level,enabled,created_by) VALUES (?,?,?,?,?,?,?,?)", (req.scope_type, req.scope_value, req.metric, req.threshold, req.warning_pct, req.escalation_level, int(req.enabled), actor))
        row = con.execute("SELECT * FROM exposure_limits WHERE id=?", (cursor.lastrowid,)).fetchone()
    write_audit(actor, "limit.created", "exposure_limit", cursor.lastrowid, req.model_dump(exclude={"actor"}))
    return dict(row)


@api.delete("/api/operations/limits/{limit_id}")
def delete_limit(limit_id: int, request: Request):
    actor = _operations_admin_actor(request)
    with _conn() as con:
        cursor = con.execute("DELETE FROM exposure_limits WHERE id=?", (limit_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Exposure limit was not found")
    write_audit(actor, "limit.deleted", "exposure_limit", limit_id)
    return {"ok": True}


@api.put("/api/operations/limits/{limit_id}")
def update_limit(limit_id: int, req: ExposureLimitRequest, request: Request):
    actor = _operations_admin_actor(request)
    with _conn() as con:
        changed=con.execute("UPDATE exposure_limits SET scope_type=?,scope_value=?,metric=?,threshold=?,warning_pct=?,escalation_level=?,enabled=? WHERE id=?",
                            (req.scope_type,req.scope_value,req.metric,req.threshold,req.warning_pct,req.escalation_level,int(req.enabled),limit_id)).rowcount
        if not changed:
            raise HTTPException(404,"Exposure limit was not found")
        row=dict(con.execute("SELECT * FROM exposure_limits WHERE id=?",(limit_id,)).fetchone())
    write_audit(actor,"limit.updated","exposure_limit",limit_id,req.model_dump())
    return row


@api.post("/api/operations/alerts/acknowledge")
def acknowledge_alert(req: AlertAcknowledgeRequest, request: Request):
    actor = _operations_admin_actor(request)
    with _conn() as con: con.execute("INSERT INTO alert_acknowledgements (alert_key,actor) VALUES (?,?) ON CONFLICT(alert_key) DO UPDATE SET actor=excluded.actor, acknowledged_at=datetime('now')", (req.alert_key, actor))
    write_audit(actor, "alert.acknowledged", "alert", req.alert_key)
    return {"ok": True}


@api.get("/api/operations/audit")
def audit_log(limit: int = Query(300, ge=1, le=2000)):
    with _conn() as con: rows = [dict(row) for row in con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    for row in rows: row["details"] = _json_column(row.pop("details_json", "{}"), {})
    return {"events": rows}


@api.get("/api/operations/channels")
def list_channels():
    with _conn() as con: rows = [dict(row) for row in con.execute("SELECT * FROM notification_channels ORDER BY id").fetchall()]
    return {"channels": rows}


@api.post("/api/operations/channels")
def create_channel(req: NotificationChannelRequest, request: Request):
    actor = _operations_admin_actor(request)
    with _conn() as con:
        cursor = con.execute("INSERT INTO notification_channels (name,channel_type,destination,min_severity,enabled) VALUES (?,?,?,?,?)", (req.name, req.channel_type, req.destination, req.min_severity, int(req.enabled)))
        row = con.execute("SELECT * FROM notification_channels WHERE id=?", (cursor.lastrowid,)).fetchone()
    write_audit(actor, "notification_channel.created", "notification_channel", cursor.lastrowid, {"name": req.name, "type": req.channel_type})
    return dict(row)


@api.delete("/api/operations/channels/{channel_id}")
def delete_channel(channel_id: int, request: Request):
    actor = _operations_admin_actor(request)
    with _conn() as con:
        cursor = con.execute("DELETE FROM notification_channels WHERE id=?", (channel_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Notification channel was not found")
    write_audit(actor, "notification_channel.deleted", "notification_channel", channel_id)
    return {"ok": True}


def _store_daily_reconciliation() -> dict[str, Any]:
    import mt5_connection
    mgr = mt5_connection.get_manager()
    if not mgr or not mgr.is_connected: return {"status": "offline"}
    snapshot = mgr.snapshot()
    users = snapshot.users
    metrics = {"accounts": len(users), "balance": round(sum(_to_float(row.get("Balance"), 0.0) for row in users), 2), "equity": round(sum(_to_float(row.get("Equity"), 0.0) for row in users), 2), "margin": round(sum(_to_float(row.get("Margin"), 0.0) for row in users), 2), "open_positions": len(snapshot.positions), "source": "MT5 manager live cache"}
    report_date = datetime.now(timezone.utc).date().isoformat()
    status = "complete" if users else "waiting_for_manager_cache"
    warnings = [] if users else ["Manager user cache is not populated."]
    with _conn() as con: con.execute("INSERT INTO daily_reconciliations (report_date,status,metrics_json,warnings_json,source_updated_at) VALUES (?,?,?,?,?) ON CONFLICT(report_date) DO UPDATE SET status=excluded.status, metrics_json=excluded.metrics_json, warnings_json=excluded.warnings_json, source_updated_at=excluded.source_updated_at, created_at=datetime('now')", (report_date, status, json.dumps(metrics), json.dumps(warnings), snapshot.last_update))
    return {"report_date": report_date, "status": status, "metrics": metrics, "warnings": warnings, "source_updated_at": snapshot.last_update}


@api.post("/api/operations/reconciliation/run")
def run_reconciliation(request: Request):
    actor = _operations_admin_actor(request)
    result = _store_daily_reconciliation()
    write_audit(actor, "reconciliation.run", "daily_reconciliation", result.get("report_date", ""), {"status": result.get("status")})
    return result


@api.get("/api/operations/reconciliation")
def reconciliation_history():
    with _conn() as con: rows = [dict(row) for row in con.execute("SELECT * FROM daily_reconciliations ORDER BY report_date DESC LIMIT 90").fetchall()]
    for row in rows:
        row["metrics"] = _json_column(row.pop("metrics_json", "{}"), {})
        row["warnings"] = _json_column(row.pop("warnings_json", "[]"), [])
    return {"rows": rows}


def _compute_operations_intelligence(req: IntelligenceRequest, actor: str):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=req.lookback_days)
    midpoint = start + (end - start) / 2
    warnings: list[str] = []
    with yielding_history_manager_session() as api_handle:
        groups, _, group_warnings = _resolve_groups_for_request(api_handle, req.selected_groups, req.group_filter_rules)
        warnings.extend(group_warnings)
        if not groups: raise HTTPException(status_code=400, detail="Select at least one live group.")
        users, group_map, user_warnings = _collect_user_scope(api_handle, groups, _normalize_login_set(req.excluded_logins))
        warnings.extend(user_warnings)
        allowed = set(users)
        deals: list[Any] = []
        seen: set[int] = set()
        for group in groups:
            rows, error = request_deals_by_group(api_handle, group, int(start.timestamp()), int(end.timestamp()))
            if error:
                warnings.append(f"{group}: {error}")
                continue
            for deal in rows:
                login = _login_from_record(deal)
                ticket = _to_int(_value(deal, "Deal", "deal", "Ticket", "ticket"), 0) or 0
                if login not in allowed or (ticket and ticket in seen): continue
                if ticket: seen.add(ticket)
                deals.append(deal)

    by_login: dict[int, list[Any]] = defaultdict(list)
    deposits: dict[int, float] = defaultdict(float)
    for deal in deals:
        login = _login_from_record(deal)
        if login is None: continue
        by_login[login].append(deal)
        action = _to_int(_value(deal, "Action", "action"), None)
        amount = _to_float(_value(deal, "Profit", "profit", "Amount", "amount"), 0.0)
        if action == DEAL_BALANCE and amount > 0: deposits[login] += amount

    lifecycles: list[dict[str, Any]] = []
    clients: list[dict[str, Any]] = []
    for login, login_deals in by_login.items():
        rows = build_client_trade_lifecycles(login_deals, login)
        for row in rows: lifecycles.append(row | {"login": login})
        pnl = sum(_to_float(row.get("pnl"), 0.0) for row in rows)
        volume = sum(_to_float(row.get("volume"), 0.0) for row in rows)
        current_pnl = sum(_to_float(row.get("pnl"), 0.0) for row in rows if _to_int(row.get("close_ts"), 0) >= int(midpoint.timestamp()))
        previous_pnl = pnl - current_pnl
        wins = sum(_to_float(row.get("pnl"), 0.0) > 0 for row in rows)
        total_deposit = deposits.get(login, 0.0)
        concentration: dict[str, float] = defaultdict(float)
        for row in rows: concentration[str(row.get("symbol", ""))] += _to_float(row.get("volume"), 0.0)
        max_concentration = max(concentration.values(), default=0.0) / max(volume, 1e-9) * 100
        clients.append({"login": login, "name": users.get(login, {}).get("name", ""), "group": group_map.get(login, ""), "trades": len(rows), "volume": round(volume, 2), "realized_pnl": round(pnl, 2), "win_rate": round(wins / max(len(rows), 1) * 100, 1), "deposits": round(total_deposit, 2), "profit_to_deposit_pct": round(pnl / total_deposit * 100, 1) if total_deposit else None, "current_half_pnl": round(current_pnl, 2), "previous_half_pnl": round(previous_pnl, 2), "pnl_change": round(current_pnl - previous_pnl, 2), "symbol_concentration_pct": round(max_concentration, 1), "deposit_profit_anomaly": bool(pnl >= max(500.0, total_deposit * 2.0) and (total_deposit > 0 or pnl >= 1000.0))})

    maxima = {key: max((_to_float(row.get(key), 0.0) for row in clients), default=1.0) or 1.0 for key in ("trades", "volume")}
    max_abs_pnl = max((abs(_to_float(row.get("realized_pnl"), 0.0)) for row in clients), default=1.0) or 1.0
    for row in clients:
        row["observed_value_score"] = round(100 * (0.35 * row["trades"] / maxima["trades"] + 0.35 * row["volume"] / maxima["volume"] + 0.30 * abs(row["realized_pnl"]) / max_abs_pnl), 1)
        row["score_method"] = "Observed 35% trade count + 35% volume + 30% absolute realized P&L within selected period"
    clients.sort(key=lambda row: row["observed_value_score"], reverse=True)

    copy_groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    latency_candidates = []
    for row in lifecycles:
        bucket = int(_to_int(row.get("open_ts"), 0) / 3)
        copy_groups[(str(row.get("symbol", "")), str(row.get("side", "")), bucket)].append(row)
        if _to_float(row.get("pnl"), 0.0) >= 10 and _to_float(row.get("hold_seconds"), 0.0) <= 300:
            latency_candidates.append({"login": row["login"], "position_id": row.get("position_id"), "symbol": row.get("symbol"), "opened_at": row.get("open_time"), "hold_seconds": row.get("hold_seconds"), "pnl": row.get("pnl"), "classification": "rapid_profitable_trade_requires_tick_gap_confirmation", "source": "MT5 deals"})
    copy_signals = []
    for (symbol, side, _), rows in copy_groups.items():
        logins = sorted({row["login"] for row in rows})
        if len(logins) > 1:
            copy_signals.append({"symbol": symbol, "side": side, "accounts": logins, "positions": [row.get("position_id") for row in rows], "opened_at": min(str(row.get("open_time", "")) for row in rows), "window_seconds": 3, "source": "MT5 deals", "classification": "coordinated_timing_candidate_not_proof"})

    write_audit(actor, "intelligence.scanned", "mt5_history", "", {"groups": len(groups), "clients": len(clients), "deals": len(deals), "lookback_days": req.lookback_days})
    return {"generated_at": end.isoformat(), "period": {"from": start.date().isoformat(), "to": end.date().isoformat(), "days": req.lookback_days}, "clients": clients, "copy_trading_signals": copy_signals[:200], "latency_candidates": latency_candidates[:500], "deposit_profit_anomalies": [row for row in clients if row["deposit_profit_anomaly"]], "methodology": {"profitability": "Closed MT5 position lifecycles", "historical_comparison": "First half versus second half of selected period", "copy_trading": "Same symbol, side and three-second open-time bucket across different accounts", "latency": "Rapid profitable trades only; tick-gap confirmation remains required", "value_score": "Cohort-relative observed activity score, not predicted future revenue"}, "warnings": _dedupe_warnings(warnings)}


def _run_intelligence_job(key: str, req: IntelligenceRequest, actor: str) -> None:
    try:
        result = _compute_operations_intelligence(req, actor)
        with _INTELLIGENCE_LOCK:
            _INTELLIGENCE_JOBS[key] = {"status": "complete", "result": result, "finished": time_module.monotonic()}
    except Exception as exc:
        logger.exception("Intelligence scan failed")
        with _INTELLIGENCE_LOCK:
            _INTELLIGENCE_JOBS[key] = {"status": "error", "detail": str(exc), "finished": time_module.monotonic()}


@api.post("/api/operations/intelligence")
def operations_intelligence(req: IntelligenceRequest, request: Request):
    actor = _operations_admin_actor(request)
    scope = req.model_dump(mode="json")
    scope["selected_groups"] = sorted(set(scope.get("selected_groups") or []))
    scope["excluded_logins"] = sorted(set(scope.get("excluded_logins") or []))
    key = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()[:24]
    now = time_module.monotonic()
    with _INTELLIGENCE_LOCK:
        job = _INTELLIGENCE_JOBS.get(key)
        if job and job["status"] == "complete" and now - job["finished"] < 60:
            return {"status": "complete", "job_id": key, **job["result"]}
        if job and job["status"] == "running":
            return {"status": "running", "job_id": key}
        # Bound cache memory while preserving jobs currently using the sole Manager.
        for old_key, old_job in list(_INTELLIGENCE_JOBS.items()):
            if old_job["status"] != "running" and now - old_job.get("finished", now) > 300:
                del _INTELLIGENCE_JOBS[old_key]
        _INTELLIGENCE_JOBS[key] = {"status": "running", "started": now}
    threading.Thread(target=_run_intelligence_job, args=(key, req, actor), daemon=True, name="intelligence-scan").start()
    return {"status": "running", "job_id": key}


@api.get("/api/operations/intelligence/{job_id}")
def operations_intelligence_status(job_id: str, request: Request):
    _operations_admin_actor(request)
    with _INTELLIGENCE_LOCK:
        job = _INTELLIGENCE_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Intelligence scan expired. Run it again.")
        if job["status"] == "complete":
            return {"status": "complete", "job_id": job_id, **job["result"]}
        if job["status"] == "error":
            return {"status": "error", "job_id": job_id, "detail": job["detail"]}
        return {"status": "running", "job_id": job_id}


@api.websocket("/api/operations/ws")
async def operations_websocket(websocket: WebSocket):
    user = await asyncio.to_thread(_verify_session_token, websocket.cookies.get(SESSION_COOKIE_NAME, ""))
    if not user or not bool(user.get("is_admin")):
        await websocket.close(code=1008, reason="Administrator authentication is required")
        return
    await websocket.accept()
    last_state = None
    last_risk_version = _IN_APP_RISK_EVENT_VERSION
    last_sent = 0.0
    try:
        while True:
            import mt5_connection
            mgr = mt5_connection.get_manager()
            connected = bool(mgr and mgr.is_connected)
            # A busy snapshot lock must not stall HTTP assets or navigation.
            marker = await asyncio.to_thread(mgr.update_marker) if connected else 0
            state = (connected, marker)
            now = time_module.monotonic()
            if state != last_state or now - last_sent >= 30:
                await websocket.send_json({"type": "manager_tick" if state != last_state else "heartbeat", "connected": state[0], "source_updated_at": state[1], "sent_at": datetime.now(timezone.utc).isoformat()})
                last_state, last_sent = state, now
            risk_version = _IN_APP_RISK_EVENT_VERSION
            if risk_version != last_risk_version:
                await websocket.send_json({"type": "risk_events_updated", "version": risk_version})
                last_risk_version = risk_version
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return


from services.history_api import install as _install_history
import sys as _sys
_HISTORY_WORKER = _install_history(_sys.modules[__name__])
from services.live_stream import install as _install_live_stream
_install_live_stream(_sys.modules[__name__])


def _frontend_index_path() -> Path | None:
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return index_path
    return None


@api.get("/")
def serve_frontend_index():
    index_path = _frontend_index_path()
    if index_path is None:
        raise HTTPException(status_code=404, detail="Frontend build not found.")
    # The HTML file names the current hashed JS/CSS bundles.  Never let a
    # browser retain an older HTML shell after a deployment/build.
    return FileResponse(index_path, headers={"Cache-Control": "no-store, max-age=0"})


@api.get("/{full_path:path}")
def serve_frontend_spa(full_path: str):
    if full_path.startswith("api/") or full_path in {"docs", "redoc", "openapi.json"}:
        raise HTTPException(status_code=404, detail="Not found.")

    cached_asset = _FRONTEND_ASSET_CACHE.get(full_path.replace("\\", "/"))
    if cached_asset:
        content, media_type = cached_asset
        return Response(
            content=content,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    candidate = FRONTEND_DIST / full_path
    if candidate.is_file():
        return FileResponse(candidate)

    if "." in Path(full_path).name:
        raise HTTPException(status_code=404, detail="Not found.")

    index_path = _frontend_index_path()
    if index_path is None:
        raise HTTPException(status_code=404, detail="Frontend build not found.")
    return FileResponse(index_path, headers={"Cache-Control": "no-store, max-age=0"})


if __name__ == "__main__":
    import uvicorn

    api_port = os.getenv("RMS_ANALYTICS_PORT", "8001") if os.getenv("RMS_NATIVE_URL") else os.environ.get("PORT", "8000")
    uvicorn.run("api:api", host=os.getenv("RMS_API_HOST", "0.0.0.0"), port=int(api_port), reload=False)
