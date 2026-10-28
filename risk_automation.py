from __future__ import annotations

import copy
import fnmatch
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _field(row: dict[str, Any], *names: str, default: Any = None) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name in row:
            return row[name]
        if name.lower() in lowered:
            return lowered[name.lower()]
    return default


class RiskAutomationService:
    """Persistent risk-rule evaluation and controlled MT5 account mutation."""

    CONDITION_ALIASES = {
        "toxic_behavior": "toxicity_score",
        "vps_usage": "vps_usage",
        "scalping_activity": "scalping_rate",
        "high_win_rate": "win_rate",
        "abnormal_profitability": "profit_pct",
        "excessive_trading_frequency": "trades_per_day",
        "hedging_activity": "hedged_symbols",
        "suspicious_patterns": "risk_score",
    }
    INTERNAL_ACTIONS = {
        "restrict_symbols", "restrict_lot_size", "add_label",
        "monitoring_list", "manual_review",
    }

    def __init__(
        self,
        db_path: str,
        manager_provider: Callable[[], Any],
        audit_writer: Callable[..., None],
        telegram_sender: Callable[[str], tuple[bool, str]],
    ) -> None:
        self.db_path = db_path
        self.manager_provider = manager_provider
        self.audit_writer = audit_writer
        self.telegram_sender = telegram_sender
        self.run_lock = threading.Lock()

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self.db_path, timeout=15.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 15000")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def ensure_schema(self) -> None:
        with self._conn() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS risk_automation_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    priority INTEGER NOT NULL DEFAULT 100,
                    source_group TEXT NOT NULL DEFAULT '*',
                    target_group TEXT NOT NULL DEFAULT '',
                    condition_logic TEXT NOT NULL DEFAULT 'AND',
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    actions_json TEXT NOT NULL DEFAULT '[]',
                    alert_events_json TEXT NOT NULL DEFAULT '[]',
                    delay_minutes INTEGER NOT NULL DEFAULT 0,
                    require_approval INTEGER NOT NULL DEFAULT 0,
                    revert_when_clear INTEGER NOT NULL DEFAULT 0,
                    simulation_mode INTEGER NOT NULL DEFAULT 1,
                    cooldown_minutes INTEGER NOT NULL DEFAULT 60,
                    excluded_logins_json TEXT NOT NULL DEFAULT '[]',
                    created_by TEXT NOT NULL DEFAULT 'system',
                    updated_by TEXT NOT NULL DEFAULT 'system',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_rule_states (
                    rule_id INTEGER NOT NULL REFERENCES risk_automation_rules(id) ON DELETE CASCADE,
                    client_login INTEGER NOT NULL,
                    matched INTEGER NOT NULL DEFAULT 0,
                    first_matched_at TEXT,
                    last_evaluated_at TEXT NOT NULL,
                    last_executed_at TEXT,
                    original_group TEXT NOT NULL DEFAULT '',
                    last_values_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(rule_id, client_login)
                );
                CREATE TABLE IF NOT EXISTS risk_execution_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id INTEGER REFERENCES risk_automation_rules(id) ON DELETE SET NULL,
                    rule_name TEXT NOT NULL,
                    client_login INTEGER NOT NULL,
                    client_name TEXT NOT NULL DEFAULT '',
                    account_equity REAL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    previous_group TEXT NOT NULL DEFAULT '',
                    new_group TEXT NOT NULL DEFAULT '',
                    detected_json TEXT NOT NULL DEFAULT '{}',
                    actions_json TEXT NOT NULL DEFAULT '[]',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    actor TEXT NOT NULL DEFAULT 'system',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_approval_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id INTEGER NOT NULL REFERENCES risk_automation_rules(id) ON DELETE CASCADE,
                    client_login INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    requested_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT NOT NULL DEFAULT '',
                    decision_note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS risk_client_controls (
                    client_login INTEGER PRIMARY KEY,
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    monitoring INTEGER NOT NULL DEFAULT 0,
                    restricted_symbols_json TEXT NOT NULL DEFAULT '[]',
                    max_lot_size REAL,
                    manual_review INTEGER NOT NULL DEFAULT 0,
                    updated_by TEXT NOT NULL DEFAULT 'system',
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_risk_rules_enabled_priority
                    ON risk_automation_rules(enabled, priority, id);
                CREATE INDEX IF NOT EXISTS idx_risk_history_created
                    ON risk_execution_history(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_risk_approvals_status
                    ON risk_approval_queue(status, requested_at DESC);
                """
            )
            rule_columns = {row["name"] for row in con.execute("PRAGMA table_info(risk_automation_rules)")}
            if "excluded_logins_json" not in rule_columns:
                con.execute("ALTER TABLE risk_automation_rules ADD COLUMN excluded_logins_json TEXT NOT NULL DEFAULT '[]'")
            history_columns = {row["name"] for row in con.execute("PRAGMA table_info(risk_execution_history)")}
            if "account_equity" not in history_columns:
                con.execute("ALTER TABLE risk_execution_history ADD COLUMN account_equity REAL")

    @staticmethod
    def _rule(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        for source, target, default in (
            ("conditions_json", "conditions", []),
            ("actions_json", "actions", []),
            ("alert_events_json", "alert_events", []),
            ("excluded_logins_json", "excluded_logins", []),
        ):
            data[target] = _json(data.pop(source, default), default)
        for key in ("enabled", "require_approval", "revert_when_clear", "simulation_mode"):
            data[key] = bool(data.get(key))
        return data

    @staticmethod
    def _history(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        for source, target, default in (
            ("detected_json", "detected", {}),
            ("actions_json", "actions", []),
            ("result_json", "result", {}),
        ):
            data[target] = _json(data.pop(source, default), default)
        return data

    def list_rules(self) -> list[dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM risk_automation_rules ORDER BY priority, id").fetchall()
        return [self._rule(row) for row in rows]

    def get_rule(self, rule_id: int) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM risk_automation_rules WHERE id=?", (rule_id,)).fetchone()
        return self._rule(row) if row else None

    def save_rule(self, payload: dict[str, Any], actor: str, rule_id: int | None = None) -> dict[str, Any]:
        now = _now()
        values = (
            str(payload.get("name", "")).strip(), str(payload.get("description", "")).strip(),
            int(bool(payload.get("enabled", False))), int(payload.get("priority", 100)),
            str(payload.get("source_group", "*")).strip() or "*", str(payload.get("target_group", "")).strip(),
            str(payload.get("condition_logic", "AND")).upper(), json.dumps(payload.get("conditions", [])),
            json.dumps(payload.get("actions", [])), json.dumps(payload.get("alert_events", [])),
            int(payload.get("delay_minutes", 0)), int(bool(payload.get("require_approval", False))),
            int(bool(payload.get("revert_when_clear", False))), int(bool(payload.get("simulation_mode", True))),
            int(payload.get("cooldown_minutes", 60)),
            json.dumps(sorted({int(login) for login in payload.get("excluded_logins", []) if int(login) > 0})),
            actor, now,
        )
        with self._conn() as con:
            if rule_id is None:
                cursor = con.execute(
                    """INSERT INTO risk_automation_rules
                    (name,description,enabled,priority,source_group,target_group,condition_logic,conditions_json,
                     actions_json,alert_events_json,delay_minutes,require_approval,revert_when_clear,simulation_mode,
                     cooldown_minutes,excluded_logins_json,created_by,updated_by,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values[:-2] + (actor, actor, now, now),
                )
                rule_id = int(cursor.lastrowid)
                action = "risk_rule.created"
            else:
                before = con.execute("SELECT * FROM risk_automation_rules WHERE id=?", (rule_id,)).fetchone()
                if not before:
                    raise KeyError("Risk automation rule was not found")
                con.execute(
                    """UPDATE risk_automation_rules SET
                    name=?,description=?,enabled=?,priority=?,source_group=?,target_group=?,condition_logic=?,conditions_json=?,
                    actions_json=?,alert_events_json=?,delay_minutes=?,require_approval=?,revert_when_clear=?,simulation_mode=?,
                    cooldown_minutes=?,excluded_logins_json=?,updated_by=?,updated_at=? WHERE id=?""",
                    values + (rule_id,),
                )
                if bool(before["simulation_mode"]) and not bool(payload.get("simulation_mode", True)):
                    # A simulation records when it would have executed. Clear
                    # that marker when promoting the rule so current matches
                    # can be applied on the next live evaluation.
                    con.execute(
                        "UPDATE risk_rule_states SET last_executed_at=NULL WHERE rule_id=?",
                        (rule_id,),
                    )
                action = "risk_rule.updated"
        saved = self.get_rule(rule_id)
        self.audit_writer(actor, action, "risk_rule", rule_id, {"rule": saved})
        return saved or {}

    def delete_rule(self, rule_id: int, actor: str) -> None:
        rule = self.get_rule(rule_id)
        if not rule:
            raise KeyError("Risk automation rule was not found")
        with self._conn() as con:
            con.execute("DELETE FROM risk_automation_rules WHERE id=?", (rule_id,))
        self.audit_writer(actor, "risk_rule.deleted", "risk_rule", rule_id, {"previous": rule})

    def _manager_snapshot(self) -> tuple[Any, Any]:
        manager = self.manager_provider()
        if not manager or not getattr(manager, "is_connected", False):
            raise RuntimeError("The MT5 Manager connection is offline")
        return manager, manager.snapshot()

    def _load_states(self, rule_ids: list[int]) -> dict[tuple[int, int], dict[str, Any]]:
        if not rule_ids:
            return {}
        placeholders = ",".join("?" for _ in rule_ids)
        with self._conn() as con:
            rows = con.execute(
                f"SELECT * FROM risk_rule_states WHERE rule_id IN ({placeholders})",
                tuple(rule_ids),
            ).fetchall()
        return {(int(row["rule_id"]), int(row["client_login"])): dict(row) for row in rows}

    def _flush_run_state_changes(
        self,
        state_rows: list[tuple[Any, ...]],
        rejected_approval_keys: list[tuple[int, int]],
    ) -> None:
        if not state_rows and not rejected_approval_keys:
            return
        with self._conn() as con:
            if rejected_approval_keys:
                con.executemany(
                    "DELETE FROM risk_approval_queue WHERE rule_id=? AND client_login=? AND status='rejected'",
                    rejected_approval_keys,
                )
            if state_rows:
                con.executemany(
                    """INSERT INTO risk_rule_states
                    (rule_id,client_login,matched,first_matched_at,last_evaluated_at,last_executed_at,original_group,last_values_json)
                    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(rule_id,client_login) DO UPDATE SET
                    matched=excluded.matched,first_matched_at=excluded.first_matched_at,last_evaluated_at=excluded.last_evaluated_at,
                    last_executed_at=excluded.last_executed_at,original_group=excluded.original_group,last_values_json=excluded.last_values_json""",
                    state_rows,
                )

    def client_metrics(self, snapshot: Any) -> list[dict[str, Any]]:
        positions_by_login: dict[int, list[dict[str, Any]]] = {}
        for position in snapshot.positions:
            login = _integer(_field(position, "Login", "login"), 0)
            if login:
                positions_by_login.setdefault(login, []).append(position)
        result: list[dict[str, Any]] = []
        for user in snapshot.users:
            login = _integer(_field(user, "Login", "login"), 0)
            if not login:
                continue
            positions = positions_by_login.get(login, [])
            balance = _number(_field(user, "Balance", "balance"))
            equity_value = _field(user, "Equity", "equity", default=None)
            equity = _number(equity_value) if equity_value is not None else None
            floating = sum(_number(_field(row, "Profit", "profit")) for row in positions)
            lots = sum(abs(_number(_field(row, "Volume", "volume"))) for row in positions)
            if lots > 1000:
                lots /= 10000.0
            sides: dict[str, set[int]] = {}
            for row in positions:
                symbol = str(_field(row, "Symbol", "symbol", default=""))
                sides.setdefault(symbol, set()).add(_integer(_field(row, "Action", "action", "Type", "type"), -1))
            raw = {str(key): value for key, value in user.items()}
            metrics = {
                "login": login,
                "name": str(_field(user, "Name", "name", default="")),
                "group": str(_field(user, "Group", "group", default="")),
                "balance": balance,
                "equity": equity,
                "floating_pnl": floating,
                "profit_pct": round((floating / balance * 100.0), 2) if balance else 0.0,
                "margin": _number(_field(user, "Margin", "margin")),
                "margin_level": _number(_field(user, "MarginLevel", "margin_level")),
                "leverage": _number(_field(user, "Leverage", "leverage")),
                "open_positions": len(positions),
                "gross_lots": round(lots, 4),
                "hedged_symbols": sum(len(values) > 1 for values in sides.values()),
                "win_rate": _number(_field(user, "WinRate", "win_rate")),
                "toxicity_score": _number(_field(user, "ToxicityScore", "toxicity_score")),
                "risk_score": _number(_field(user, "RiskScore", "risk_score")),
                "scalping_rate": _number(_field(user, "ScalpingRate", "scalping_rate")),
                "trades_per_day": _number(_field(user, "TradesPerDay", "trades_per_day", default=len(positions))),
                "vps_usage": str(_field(user, "VPS", "vps", "vps_status", default="")).lower() in {"1", "true", "yes", "detected"},
                "enabled": bool(_field(user, "Enabled", "enabled", default=True)),
                "raw": raw,
            }
            result.append(metrics)
        return result

    def enrich_historical_metrics(
        self,
        manager: Any,
        clients: list[dict[str, Any]],
        rules: list[dict[str, Any]],
        states: dict[tuple[int, int], dict[str, Any]] | None = None,
    ) -> None:
        """Attach closed-trade metrics for the lookback windows used by rules."""
        historical_types = {
            "toxic_behavior", "scalping_activity", "high_win_rate",
            "abnormal_profitability", "excessive_trading_frequency", "suspicious_patterns",
        }
        groups_by_window: dict[int, set[str]] = {}
        for rule in rules:
            excluded_logins = {int(login) for login in rule.get("excluded_logins", [])}
            rule_windows = {
                int(condition.get("window_days") or 30)
                for condition in rule.get("conditions", [])
                if condition.get("type") in historical_types
            }
            if not rule_windows:
                continue
            matching_groups = {
                client["group"]
                for client in clients
                if client["login"] not in excluded_logins and client.get("group") and (
                    self._source_matches(rule, client["group"])
                    or bool((states or {}).get((int(rule["id"]), int(client["login"])), {}).get("matched"))
                )
            }
            for days in rule_windows:
                groups_by_window.setdefault(days, set()).update(matching_groups)
        if not groups_by_window:
            return
        by_login = {client["login"]: client for client in clients}
        now_ts = int(time.time())
        for days, groups in sorted(groups_by_window.items())[:5]:
            deals: list[Any] = []
            history_available = False
            for group in sorted(groups):
                try:
                    response = manager.call("DealRequestByGroup", group, now_ts - days * 86400, now_ts)
                    history_available = True
                    if isinstance(response, tuple) and len(response) == 2 and isinstance(response[1], (str, type(None))):
                        response = response[0]
                    deals.extend(list(response or []))
                except Exception:
                    continue
            if not history_available:
                continue
            positions: dict[int, dict[int, dict[str, Any]]] = {}
            for deal in deals:
                row = deal if isinstance(deal, dict) else {
                    name: getattr(deal, name, None)
                    for name in ("Login", "PositionID", "Position", "Deal", "Time", "TimeMsc", "Profit", "Commission", "Swap", "Fee", "Entry")
                }
                login = _integer(_field(row, "Login", "login"), 0)
                position_id = _integer(_field(row, "PositionID", "Position", "position_id"), 0)
                if login not in by_login or not position_id:
                    continue
                item = positions.setdefault(login, {}).setdefault(position_id, {"times": [], "net": 0.0, "closed": False})
                timestamp = _number(_field(row, "TimeMsc", "time_msc", "Time", "time"), 0)
                if timestamp > 1e11:
                    timestamp /= 1000.0
                if timestamp:
                    item["times"].append(timestamp)
                item["net"] += sum(_number(_field(row, key, key.lower())) for key in ("Profit", "Commission", "Swap", "Fee"))
                entry = _integer(_field(row, "Entry", "entry"), -1)
                item["closed"] = item["closed"] or entry in {1, 3}
            for login, client in by_login.items():
                lifecycles = [item for item in positions.get(login, {}).values() if item["closed"] or len(item["times"]) > 1]
                trades = len(lifecycles)
                wins = sum(item["net"] > 0 for item in lifecycles)
                realized = sum(item["net"] for item in lifecycles)
                scalps = sum(item["times"] and max(item["times"]) - min(item["times"]) <= 300 for item in lifecycles)
                win_rate = round(wins / trades * 100.0, 2) if trades else 0.0
                scalping_rate = round(scalps / trades * 100.0, 2) if trades else 0.0
                profit_pct = round(realized / client["balance"] * 100.0, 2) if client["balance"] else 0.0
                frequency = round(trades / max(days, 1), 2)
                toxicity = min(100.0, round(win_rate * 0.35 + scalping_rate * 0.30 + min(abs(profit_pct), 100) * 0.20 + min(frequency * 5, 100) * 0.15, 2))
                client.setdefault("windows", {})[str(days)] = {
                    "closed_trades": trades, "win_rate": win_rate, "realized_pnl": round(realized, 2),
                    "profit_pct": profit_pct, "trades_per_day": frequency, "scalping_rate": scalping_rate,
                    "toxicity_score": toxicity, "risk_score": toxicity,
                }

    @staticmethod
    def _compare(actual: Any, operator: str, expected: Any) -> bool:
        if operator in {"contains", "not_contains"}:
            found = str(expected).lower() in str(actual).lower()
            return found if operator == "contains" else not found
        if operator in {"in", "not_in"}:
            values = expected if isinstance(expected, list) else [part.strip() for part in str(expected).split(",")]
            found = str(actual).lower() in {str(value).lower() for value in values}
            return found if operator == "in" else not found
        if operator in {"equals", "not_equals"}:
            equal = str(actual).lower() == str(expected).lower()
            return equal if operator == "equals" else not equal
        left, right = _number(actual), _number(expected)
        return {
            "gt": left > right, "gte": left >= right, "lt": left < right,
            "lte": left <= right,
        }.get(operator, left == right)

    def evaluate(self, rule: dict[str, Any], client: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        for condition in rule.get("conditions", []):
            kind = str(condition.get("type", "custom"))
            metric = str(condition.get("field") or self.CONDITION_ALIASES.get(kind, kind))
            days = int(condition.get("window_days") or (30 if kind in {
                "toxic_behavior", "scalping_activity", "high_win_rate", "abnormal_profitability",
                "excessive_trading_frequency", "suspicious_patterns",
            } else 0))
            window_values = client.get("windows", {}).get(str(days), {}) if days else {}
            actual = window_values.get(metric, client.get(metric, client.get("raw", {}).get(metric)))
            available = actual is not None
            matched = available and self._compare(actual, str(condition.get("operator", "gte")), condition.get("value"))
            checks.append({
                "type": kind, "field": metric, "operator": condition.get("operator", "gte"),
                "threshold": condition.get("value"), "detected_value": actual, "available": available,
                "matched": bool(matched), "window_days": days, "source": "closed_mt5_deals" if metric in window_values else "live_manager_cache",
            })
        if not checks:
            return False, {"logic": rule.get("condition_logic", "AND"), "conditions": []}
        logic = str(rule.get("condition_logic", "AND")).upper()
        matched = any(item["matched"] for item in checks) if logic == "OR" else all(item["matched"] for item in checks)
        return matched, {"logic": logic, "conditions": checks}

    def _source_matches(self, rule: dict[str, Any], group: str) -> bool:
        pattern = str(rule.get("source_group") or "*")
        return fnmatch.fnmatchcase(group.lower(), pattern.lower())

    def simulate(self, rule: dict[str, Any]) -> dict[str, Any]:
        manager, snapshot = self._manager_snapshot()
        clients = self.client_metrics(snapshot)
        self.enrich_historical_metrics(manager, clients, [rule])
        matches = []
        evaluated = 0
        excluded_logins = {int(login) for login in rule.get("excluded_logins", [])}
        for client in clients:
            if client["login"] in excluded_logins:
                continue
            if not self._source_matches(rule, client["group"]):
                continue
            evaluated += 1
            matched, detected = self.evaluate(rule, client)
            if matched:
                matches.append({key: client[key] for key in ("login", "name", "group", "balance", "equity", "floating_pnl", "open_positions", "gross_lots")} | {"detected": detected})
        return {"evaluated": evaluated, "matched": len(matches), "clients": matches[:500], "simulation": True, "source_updated_at": snapshot.last_update}

    def _rights_bits(self) -> tuple[int, int]:
        try:
            import MT5Manager
            rights = MT5Manager.MTUser.EnUsersRights
            return int(getattr(rights, "USER_RIGHT_ENABLED", 1)), int(getattr(rights, "USER_RIGHT_TRADE_DISABLED", 4))
        except Exception:
            return 1, 4

    def _apply_internal_action(self, login: int, action: dict[str, Any], actor: str) -> dict[str, Any]:
        with self._conn() as con:
            row = con.execute("SELECT * FROM risk_client_controls WHERE client_login=?", (login,)).fetchone()
            current = dict(row) if row else {}
            labels = _json(current.get("labels_json", "[]"), [])
            symbols = _json(current.get("restricted_symbols_json", "[]"), [])
            action_type = action["type"]
            if action_type == "add_label" and action.get("value") and action["value"] not in labels:
                labels.append(str(action["value"]))
            if action_type == "restrict_symbols":
                symbols = list(dict.fromkeys(symbols + list(action.get("symbols") or [])))
            monitoring = int(current.get("monitoring", 0) or action_type == "monitoring_list")
            review = int(current.get("manual_review", 0) or action_type == "manual_review")
            max_lot = action.get("value") if action_type == "restrict_lot_size" else current.get("max_lot_size")
            con.execute(
                """INSERT INTO risk_client_controls
                (client_login,labels_json,monitoring,restricted_symbols_json,max_lot_size,manual_review,updated_by,updated_at)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(client_login) DO UPDATE SET
                labels_json=excluded.labels_json,monitoring=excluded.monitoring,
                restricted_symbols_json=excluded.restricted_symbols_json,max_lot_size=excluded.max_lot_size,
                manual_review=excluded.manual_review,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                (login, json.dumps(labels), monitoring, json.dumps(symbols), max_lot, review, actor, _now()),
            )
        return {"type": action_type, "status": "applied", "enforcement": "internal_control"}

    def execute_actions(
        self,
        rule: dict[str, Any],
        client: dict[str, Any],
        actor: str,
        revert: bool = False,
        manager: Any | None = None,
    ) -> dict[str, Any]:
        if manager is None:
            manager, _ = self._manager_snapshot()
        login = int(client["login"])
        results: list[dict[str, Any]] = []
        previous_group = str(client.get("group", ""))
        new_group = previous_group
        user = None
        user_changed = False
        internal_actions: list[dict[str, Any]] = []
        enabled_bit, trade_disabled_bit = self._rights_bits()
        for action in rule.get("actions", []):
            action_type = str(action.get("type", ""))
            try:
                if action_type in self.INTERNAL_ACTIONS:
                    internal_actions.append(action)
                    results.append({"type": action_type, "status": "prepared", "enforcement": "internal_control"})
                    continue
                if user is None:
                    user = manager.call("UserGet", login)
                    if not user:
                        raise RuntimeError("UserGet returned no account")
                if action_type == "move_group":
                    target = str(client.get("original_group", "")) if revert else str(action.get("target_group") or rule.get("target_group") or "")
                    if not target:
                        raise RuntimeError("Target group is not configured")
                    user.Group = target
                    new_group = target
                    user_changed = True
                elif action_type == "disable_account":
                    user.Rights = int(getattr(user, "Rights", 0)) & ~enabled_bit
                    user_changed = True
                elif action_type in {"disable_trading", "read_only"}:
                    user.Rights = int(getattr(user, "Rights", 0)) | trade_disabled_bit
                    user_changed = True
                elif action_type == "set_leverage":
                    user.Leverage = int(action.get("value"))
                    user_changed = True
                else:
                    raise RuntimeError(f"Unsupported action: {action_type}")
                results.append({"type": action_type, "status": "prepared", "enforcement": "mt5"})
            except Exception as exc:
                results.append({"type": action_type, "status": "failed", "error": str(exc)})
        if user_changed:
            ok = bool(manager.call("UserUpdate", user))
            if not ok:
                for result in results:
                    if result.get("status") == "prepared" and result.get("enforcement") == "mt5":
                        result.update(status="failed", error="MT5 UserUpdate returned false")
            else:
                for result in results:
                    if result.get("status") == "prepared" and result.get("enforcement") == "mt5":
                        result["status"] = "applied"
                if new_group != previous_group:
                    actual_group = ""
                    for attempt in range(3):
                        refreshed = manager.call("UserGet", login, required=False, default=None)
                        actual_group = str(
                            getattr(refreshed, "Group", None)
                            or (refreshed.get("Group") if isinstance(refreshed, dict) else "")
                            or ""
                        )
                        if actual_group == new_group:
                            break
                        if attempt < 2:
                            time.sleep(0.2)
                    if actual_group != new_group:
                        for result in results:
                            if result.get("type") == "move_group":
                                result.update(
                                    status="failed",
                                    error=f"Group verification failed (server returned {actual_group or 'no user'})",
                                )
        mt5_failed = any(item.get("status") == "failed" and item.get("enforcement") == "mt5" for item in results)
        for action in internal_actions:
            action_type = str(action.get("type", ""))
            target = next(
                (item for item in results if item.get("type") == action_type and item.get("status") == "prepared"),
                None,
            )
            if not target:
                continue
            if mt5_failed:
                target.update(status="failed", error="Skipped because MT5 account update failed")
                continue
            try:
                target.update(self._apply_internal_action(login, action, actor))
            except Exception as exc:
                target.update(status="failed", error=str(exc))
        status = "failed" if any(item["status"] == "failed" for item in results) else "success"
        if status == "success" and not results:
            status = "no_actions"
        return {"status": status, "previous_group": previous_group, "new_group": new_group, "actions": results}

    def _record_history(self, rule: dict[str, Any], client: dict[str, Any], event_type: str, status: str, detected: dict[str, Any], result: dict[str, Any], actor: str) -> int:
        with self._conn() as con:
            cursor = con.execute(
                """INSERT INTO risk_execution_history
                (rule_id,rule_name,client_login,client_name,account_equity,event_type,status,previous_group,new_group,
                 detected_json,actions_json,result_json,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rule.get("id"), rule.get("name", "Manual override"), client["login"], client.get("name", ""),
                 client.get("equity"), event_type, status, result.get("previous_group", client.get("group", "")), result.get("new_group", client.get("group", "")),
                 json.dumps(detected), json.dumps(rule.get("actions", [])), json.dumps(result), actor, _now()),
            )
        history_id = int(cursor.lastrowid)
        self.audit_writer(actor, f"risk_rule.{event_type}", "risk_execution", history_id, {
            "rule_id": rule.get("id"), "client_login": client["login"], "status": status,
            "previous": {"group": result.get("previous_group")}, "new": {"group": result.get("new_group")},
            "result": result,
        })
        return history_id

    def _alert(self, rule: dict[str, Any], client: dict[str, Any], event_type: str, detected: dict[str, Any], result: dict[str, Any]) -> None:
        events = set(rule.get("alert_events") or [])
        if event_type not in events and "all" not in events:
            return
        condition_text = "; ".join(
            f"{item.get('field')}={item.get('detected_value')} (threshold {item.get('operator')} {item.get('threshold')})"
            for item in detected.get("conditions", []) if item.get("matched")
        ) or "Condition cleared / manual event"
        message = "\n".join([
            "MT5 RISK AUTOMATION ALERT",
            f"Client: {client.get('name') or 'Unknown'} (#{client.get('login')})",
            f"Rule: {rule.get('name')}", f"Event: {event_type}",
            f"Condition: {condition_text}",
            f"Previous group: {result.get('previous_group') or client.get('group') or '-'}",
            f"New group: {result.get('new_group') or client.get('group') or '-'}",
            f"Actions: {', '.join(item.get('type', '') for item in result.get('actions', [])) or 'None'}",
            f"Status: {result.get('status')}", f"Date/time: {_now()}",
        ])
        threading.Thread(target=self.telegram_sender, args=(message,), daemon=True).start()

    def _queue_approval(self, rule: dict[str, Any], client: dict[str, Any], detected: dict[str, Any]) -> bool:
        payload = {"rule": rule, "client": client, "detected": detected}
        with self._conn() as con:
            existing = con.execute(
                "SELECT id,status FROM risk_approval_queue WHERE rule_id=? AND client_login=? ORDER BY id DESC LIMIT 1",
                (rule["id"], client["login"]),
            ).fetchone()
            if existing and existing["status"] in {"pending", "rejected"}:
                return False
            con.execute(
                "INSERT INTO risk_approval_queue (rule_id,client_login,payload_json,status,requested_at) VALUES (?,?,?,'pending',?)",
                (rule["id"], client["login"], json.dumps(payload), _now()),
            )
        result = {"status": "approval_required", "previous_group": client["group"], "new_group": client["group"], "actions": []}
        self._record_history(rule, client, "approval_required", "pending", detected, result, "system")
        self._alert(rule, client, "approval_required", detected, result)
        return True

    def run(self, rule_id: int | None = None, actor: str = "system") -> dict[str, Any]:
        if not self.run_lock.acquire(blocking=False):
            return {"status": "already_running", "evaluated": 0, "matched": 0, "executed": 0}
        started = time.perf_counter()
        try:
            manager, snapshot = self._manager_snapshot()
            rules = [self.get_rule(rule_id)] if rule_id else self.list_rules()
            rules = [rule for rule in rules if rule and (rule_id is not None or rule["enabled"])]
            clients = self.client_metrics(snapshot)
            states = self._load_states([int(rule["id"]) for rule in rules])
            self.enrich_historical_metrics(manager, clients, rules, states)
            summary = {
                "status": "complete", "rules": len(rules), "clients": len(clients),
                "evaluated": 0, "matched": 0, "executed": 0, "approvals": 0,
                "simulated": 0, "failed": 0,
            }
            for rule in rules:
                excluded_logins = {int(login) for login in rule.get("excluded_logins", [])}
                state_rows: list[tuple[Any, ...]] = []
                rejected_approval_keys: list[tuple[int, int]] = []
                for client in clients:
                    if client["login"] in excluded_logins:
                        continue
                    state_key = (int(rule["id"]), int(client["login"]))
                    prior_data = states.get(state_key, {}).copy()
                    was_matched = bool(prior_data.get("matched"))
                    if not self._source_matches(rule, client["group"]) and not (was_matched and rule["revert_when_clear"]):
                        continue
                    summary["evaluated"] += 1
                    matched, detected = self.evaluate(rule, client)
                    original_group = prior_data.get("original_group") or client["group"]
                    now_text = _now()
                    first_matched = prior_data.get("first_matched_at") if was_matched else (now_text if matched else None)
                    due = True
                    if matched and rule["delay_minutes"] and first_matched:
                        due = (datetime.now(timezone.utc) - datetime.fromisoformat(first_matched)).total_seconds() >= rule["delay_minutes"] * 60
                    if matched:
                        summary["matched"] += 1
                        if not was_matched:
                            result = {"status": "triggered", "previous_group": client["group"], "new_group": client["group"], "actions": []}
                            self._record_history(rule, client, "triggered", "detected", detected, result, actor)
                            self._alert(rule, client, "triggered", detected, result)
                        if due and not prior_data.get("last_executed_at"):
                            if rule["simulation_mode"]:
                                summary["simulated"] += 1
                                result = {"status": "simulated", "previous_group": client["group"], "new_group": rule.get("target_group") or client["group"], "actions": [{"type": item.get("type"), "status": "would_apply"} for item in rule["actions"]]}
                                self._record_history(rule, client, "simulation", "simulated", detected, result, actor)
                                prior_data["last_executed_at"] = _now()
                            elif rule["require_approval"]:
                                if self._queue_approval(rule, client, detected):
                                    summary["approvals"] += 1
                            else:
                                result = self.execute_actions(rule, client, actor, manager=manager)
                                event = "failed" if result["status"] == "failed" else "executed"
                                self._record_history(rule, client, event, result["status"], detected, result, actor)
                                self._alert(rule, client, event, detected, result)
                                summary["failed" if event == "failed" else "executed"] += 1
                                if event != "failed":
                                    prior_data["last_executed_at"] = _now()
                    elif was_matched and rule["revert_when_clear"] and not rule["simulation_mode"] and prior_data.get("last_executed_at"):
                        revert_client = client | {"original_group": original_group}
                        revert_rule = copy.deepcopy(rule)
                        revert_rule["actions"] = [{"type": "move_group"}]
                        result = self.execute_actions(revert_rule, revert_client, actor, revert=True, manager=manager)
                        event = "failed" if result["status"] == "failed" else "reverted"
                        self._record_history(rule, client, event, result["status"], detected, result, actor)
                        self._alert(rule, client, event, detected, result)
                        summary["failed" if event == "failed" else "executed"] += 1
                        prior_data["last_executed_at"] = None
                    elif not matched:
                        prior_data["last_executed_at"] = None
                        rejected_approval_keys.append((int(rule["id"]), int(client["login"])))
                    row = (
                        int(rule["id"]), int(client["login"]), int(matched),
                        first_matched if matched else None, _now(),
                        prior_data.get("last_executed_at"), original_group, json.dumps(detected),
                    )
                    state_rows.append(row)
                    states[state_key] = {
                        "rule_id": row[0], "client_login": row[1], "matched": row[2],
                        "first_matched_at": row[3], "last_evaluated_at": row[4],
                        "last_executed_at": row[5], "original_group": row[6],
                        "last_values_json": row[7],
                    }
                self._flush_run_state_changes(state_rows, rejected_approval_keys)
            return summary | {
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "source_updated_at": snapshot.last_update,
                "completed_at": _now(),
            }
        finally:
            self.run_lock.release()

    def decide_approval(self, approval_id: int, decision: str, actor: str, note: str = "") -> dict[str, Any]:
        with self._conn() as con:
            row = con.execute("SELECT * FROM risk_approval_queue WHERE id=?", (approval_id,)).fetchone()
        if not row:
            raise KeyError("Approval request was not found")
        approval = dict(row)
        if approval["status"] != "pending":
            raise ValueError("Approval request has already been decided")
        payload = _json(approval["payload_json"], {})
        rule, client, detected = payload["rule"], payload["client"], payload["detected"]
        if decision == "approved":
            manager, snapshot = self._manager_snapshot()
            live_client = next(
                (item for item in self.client_metrics(snapshot) if item["login"] == client["login"]),
                None,
            )
            if not live_client:
                raise ValueError("The account is not available in the current verified MT5 snapshot")
            client = live_client
            result = self.execute_actions(rule, client, actor, manager=manager)
            event = "failed" if result["status"] == "failed" else "executed"
            self._record_history(rule, client, event, result["status"], detected, result, actor)
            self._alert(rule, client, event, detected, result)
            with self._conn() as con:
                if event != "failed":
                    con.execute("UPDATE risk_rule_states SET last_executed_at=? WHERE rule_id=? AND client_login=?", (_now(), rule["id"], client["login"]))
        else:
            result = {"status": "rejected", "previous_group": client["group"], "new_group": client["group"], "actions": []}
            self._record_history(rule, client, "approval_rejected", "rejected", detected, result, actor)
        with self._conn() as con:
            con.execute("UPDATE risk_approval_queue SET status=?,decided_at=?,decided_by=?,decision_note=? WHERE id=?", (decision, _now(), actor, note, approval_id))
        self.audit_writer(actor, f"risk_approval.{decision}", "risk_approval", approval_id, {"note": note, "result": result})
        return {"id": approval_id, "status": decision, "result": result}

    def manual_override(self, login: int, name: str, group: str, actions: list[dict[str, Any]], actor: str, reason: str) -> dict[str, Any]:
        rule = {"id": None, "name": "Manual override", "target_group": "", "actions": actions, "alert_events": []}
        manager, snapshot = self._manager_snapshot()
        client = next(
            (item for item in self.client_metrics(snapshot) if item["login"] == login),
            {"login": login, "name": name, "group": group, "equity": None},
        )
        result = self.execute_actions(rule, client, actor, manager=manager)
        history_id = self._record_history(rule, client, "manual_override", result["status"], {"reason": reason}, result, actor)
        return result | {"history_id": history_id}

    def overview(self, limit: int = 200) -> dict[str, Any]:
        with self._conn() as con:
            history = [self._history(row) for row in con.execute("SELECT * FROM risk_execution_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
            approvals = []
            for row in con.execute("SELECT * FROM risk_approval_queue ORDER BY id DESC LIMIT ?", (limit,)).fetchall():
                data = dict(row)
                data["payload"] = _json(data.pop("payload_json", "{}"), {})
                approvals.append(data)
            controls = []
            for row in con.execute("SELECT * FROM risk_client_controls ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall():
                data = dict(row)
                data["labels"] = _json(data.pop("labels_json", "[]"), [])
                data["restricted_symbols"] = _json(data.pop("restricted_symbols_json", "[]"), [])
                controls.append(data)
        rules = self.list_rules()
        return {
            "rules": rules, "history": history, "approvals": approvals, "controls": controls,
            "summary": {
                "rules": len(rules), "enabled": sum(rule["enabled"] for rule in rules),
                "pending_approvals": sum(row["status"] == "pending" for row in approvals),
                "executions_24h": sum(str(row["created_at"]) >= datetime.now(timezone.utc).date().isoformat() for row in history),
                "failures": sum(row["status"] == "failed" for row in history),
            },
        }
