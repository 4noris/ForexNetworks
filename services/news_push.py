"""Opt-in MT5 client pushes for verified, profitable news-trading matches."""
from datetime import datetime, timezone
import math
import re
from string import Formatter
from uuid import UUID


DEFAULT_MESSAGE = (
    "Account {login}: your {symbol} trade #{position_id} matched a scheduled news release "
    "and realized {profit} {currency}. Recorded news-trading matches: {news_count}. "
    "Please review your trading activity and risk settings."
)
FIELDS = {"login", "symbol", "position_id", "profit", "currency", "news_count"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS news_push_settings(
 id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0,
 minimum_profit REAL NOT NULL DEFAULT 100, currency TEXT NOT NULL DEFAULT 'USD',
 message TEXT NOT NULL, enabled_since REAL NOT NULL DEFAULT 0,
 updated_by TEXT NOT NULL DEFAULT 'system', updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_push_outbox(
 id INTEGER PRIMARY KEY, login INTEGER NOT NULL, position_id TEXT NOT NULL,
 calendar_event_ts INTEGER NOT NULL, profit REAL NOT NULL, currency TEXT NOT NULL,
 symbol TEXT NOT NULL, news_count INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 created_at TEXT NOT NULL, completed_at TEXT, detail TEXT NOT NULL DEFAULT '',
 message TEXT NOT NULL DEFAULT '',
 UNIQUE(login,position_id,calendar_event_ts));
CREATE INDEX IF NOT EXISTS idx_news_push_pending ON news_push_outbox(status,id);
CREATE TABLE IF NOT EXISTS news_push_tests(
 request_id TEXT PRIMARY KEY, login INTEGER NOT NULL, message TEXT NOT NULL,
 actor TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, completed_at TEXT);
CREATE INDEX IF NOT EXISTS idx_news_push_tests_created ON news_push_tests(created_at DESC);
"""


def utc():
    return datetime.now(timezone.utc).isoformat()


def field(record, name):
    value = record.get(name) if isinstance(record, dict) else getattr(record, name, None)
    return value() if callable(value) else value


def validate(settings):
    profit = float(settings["minimum_profit"])
    if not math.isfinite(profit) or profit <= 0:
        raise ValueError("Profit threshold must be greater than zero")
    currency = str(settings["currency"]).strip().upper()
    if not re.fullmatch(r"[A-Z]{3,6}", currency):
        raise ValueError("Choose an account currency, for example USD or USC")
    message = str(settings["message"]).strip()
    if not message or len(message.encode("utf-16-le")) // 2 > 1024:
        raise ValueError("Message must contain 1 to 1024 characters")
    for _, key, spec, conversion in Formatter().parse(message):
        if key is not None and (key not in FIELDS or spec or conversion):
            raise ValueError("Use only the listed message placeholders without formatting expressions")
    return {"enabled": bool(settings["enabled"]), "minimum_profit": profit,
            "currency": currency, "message": message}


class NewsPushService:
    def __init__(self, connect, manager_provider):
        self.connect = connect
        self.manager_provider = manager_provider
        self.started_at = utc()

    def settings(self):
        with self.connect() as con:
            row = con.execute("SELECT * FROM news_push_settings WHERE id=1").fetchone()
        if not row:
            return dict(enabled=False, minimum_profit=100.0, currency="USD",
                        message=DEFAULT_MESSAGE, enabled_since=0.0)
        return {**dict(row), "enabled": bool(row["enabled"])}

    def save(self, settings, actor):
        value = validate(settings)
        now = datetime.now(timezone.utc).timestamp()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT enabled,enabled_since FROM news_push_settings WHERE id=1").fetchone()
            since = old["enabled_since"] if old and old["enabled"] and value["enabled"] else now if value["enabled"] else 0
            con.execute("""INSERT INTO news_push_settings
                (id,enabled,minimum_profit,currency,message,enabled_since,updated_by,updated_at)
                VALUES(1,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                enabled=excluded.enabled,minimum_profit=excluded.minimum_profit,currency=excluded.currency,
                message=excluded.message,enabled_since=excluded.enabled_since,
                updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                (int(value["enabled"]), value["minimum_profit"], value["currency"],
                 value["message"], since, actor, utc()))
            if not value["enabled"]:
                con.execute("UPDATE news_push_outbox SET status='cancelled',completed_at=?,detail='Push notifications disabled' WHERE status='queued'", (utc(),))
        return self.status()

    def status(self):
        with self.connect() as con:
            totals = {row["status"]: row["n"] for row in con.execute("SELECT status,COUNT(*) AS n FROM news_push_outbox GROUP BY status")}
            recent = [dict(row) for row in con.execute("""SELECT id,login,symbol,profit,currency,status,detail,created_at,completed_at
                FROM news_push_outbox ORDER BY id DESC LIMIT 10""")]
            last_test = con.execute("SELECT request_id,login,status,detail,created_at,completed_at FROM news_push_tests ORDER BY created_at DESC LIMIT 1").fetchone()
        return {"settings": self.settings(), "counts": totals, "recent": recent,
                "last_test": {**dict(last_test), "login": str(last_test["login"])} if last_test else None}

    def send_test(self, login, message, actor, request_id):
        """Explicit one-recipient test; independent of automatic alert settings."""
        request_id = str(UUID(str(request_id)))
        if isinstance(login, bool) or not 0 < int(login) <= 9223372036854775807:
            raise ValueError("Enter a valid MT5 login")
        login = int(login)
        message = str(message).strip()
        if not message or len(message.encode("utf-16-le")) // 2 > 1024:
            raise ValueError("Test message must contain 1 to 1024 characters")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT * FROM news_push_tests WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if existing["login"] != login or existing["message"] != message:
                    raise ValueError("This test request already belongs to a different login or message")
                return {"request_id": request_id, "login": str(login), "status": existing["status"], "detail": existing["detail"]}
            con.execute("""INSERT INTO news_push_tests(request_id,login,message,actor,status,created_at)
                VALUES(?,?,?,?,'sending',?)""", (request_id, login, message, actor, utc()))
        status, detail, sending = "failed", "MT5 is reconnecting; try again when connected", False
        try:
            manager = self.manager_provider()
            if manager and manager.is_connected:
                user = manager.call("UserRequest", login)
                if not user:
                    detail = "MT5 did not return this login from the configured server; check the login, server and manager account access"
                elif int(field(user, "Login") or 0) != login:
                    detail = "MT5 returned a different account; notification was not sent"
                elif not str(field(user, "MQID") or "").strip():
                    status, detail = "skipped", "Recipient has no MetaQuotes ID configured"
                else:
                    sending = True
                    result = manager.call("NotificationsSend", [login], message)
                    if result is True or (type(result) is int and result == 0):
                        status, detail = "accepted", "Test accepted by MT5. Check this account's mobile device; device delivery is not confirmed."
                    elif result is False or (type(result) is int and result != 0):
                        detail = "MT5 rejected the test; check manager notification rights and recipient settings"
                    else:
                        status, detail = "unknown", "Unrecognized send outcome. Check the device before sending another test."
        except Exception:
            status = "unknown" if sending else "failed"
            detail = "Send outcome unknown. Check the device before sending another test." if sending else "Unable to read the MT5 recipient"
        with self.connect() as con:
            con.execute("UPDATE news_push_tests SET status=?,detail=?,completed_at=? WHERE request_id=? AND status='sending'",
                        (status, detail, utc(), request_id))
        return {"request_id": request_id, "login": str(login), "status": status, "detail": detail}

    def queue(self, events):
        config = self.settings()
        if not config["enabled"]:
            return 0
        rows = []
        for event in events:
            if (event.get("rule") or event.get("detector")) != "news_trading" or event.get("data_quality") != "VERIFIED":
                continue
            values = event.get("triggering_values") or {}
            try:
                profit = float(values["realized_pnl"])
                opened = float(values["trade_open_ts"])
                login, position, release = int(event["login"]), int(event["position_id"]), int(values["calendar_event_ts"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            currency = str(event.get("account_currency") or "").upper()
            if (not math.isfinite(profit) or not math.isfinite(opened) or profit < config["minimum_profit"] or currency != config["currency"]
                    or opened < config["enabled_since"] or min(login, position, release) <= 0):
                continue
            rows.append((login, str(position), release, profit, currency, str(event.get("symbol") or ""), int(event.get("news_incident_count") or 1), utc()))
        if not rows:
            return 0
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            # Saving a disable/reenable transition cannot leave an old scan queued.
            current = con.execute("SELECT enabled,enabled_since FROM news_push_settings WHERE id=1").fetchone()
            if not current or not current["enabled"] or current["enabled_since"] != config["enabled_since"]:
                return 0
            before = con.total_changes
            con.executemany("""INSERT OR IGNORE INTO news_push_outbox
                (login,position_id,calendar_event_ts,profit,currency,symbol,news_count,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", rows)
            return con.total_changes - before

    def _finish(self, identifier, status, detail):
        with self.connect() as con:
            con.execute("UPDATE news_push_outbox SET status=?,completed_at=?,detail=? WHERE id=? AND status='sending'",
                        (status, utc(), detail, identifier))

    def recover(self):
        # The SDK has no idempotency key. A crashed in-flight call must not be resent.
        with self.connect() as con:
            con.execute("UPDATE news_push_outbox SET status='unknown',completed_at=?,detail='Interrupted during send; delivery outcome unknown, not retried' WHERE status='sending'", (utc(),))
            con.execute("UPDATE news_push_tests SET status='unknown',completed_at=?,detail='Interrupted during send; check the device before sending another test' WHERE status='sending' AND created_at<?", (utc(), self.started_at))

    def process_one(self):
        config = self.settings()
        if not config["enabled"]:
            return False
        manager = self.manager_provider()
        if not manager or not manager.is_connected:
            return False
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            enabled = con.execute("SELECT enabled FROM news_push_settings WHERE id=1").fetchone()
            if not enabled or not enabled["enabled"]:
                return False
            found = con.execute("SELECT * FROM news_push_outbox WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not found:
                return False
            row = dict(found)
            con.execute("UPDATE news_push_outbox SET status='sending' WHERE id=? AND status='queued'", (row["id"],))
        sending = False
        try:
            config = self.settings()
            if not config["enabled"] or row["currency"] != config["currency"] or row["profit"] < config["minimum_profit"]:
                self._finish(row["id"], "cancelled", "Notification no longer meets the enabled settings")
                return True
            user = manager.call("UserRequest", row["login"])
            if not user:
                self._finish(row["id"], "failed", "MT5 did not return this login from the configured server; check the login, server and manager account access")
                return True
            if int(field(user, "Login") or 0) != row["login"]:
                self._finish(row["id"], "failed", "MT5 returned a different account; notification was not sent")
                return True
            if not str(field(user, "MQID") or "").strip():
                self._finish(row["id"], "skipped", "Recipient has no MetaQuotes ID configured")
                return True
            group = field(user, "Group")
            specification = manager.call("GroupRequest", group) if group else None
            if not specification or str(field(specification, "Currency") or "").upper() != row["currency"]:
                self._finish(row["id"], "skipped", "Account currency could not be verified or has changed")
                return True
            message = config["message"].format(login=row["login"], symbol=row["symbol"], position_id=row["position_id"],
                profit=f'{row["profit"]:.2f}', currency=row["currency"], news_count=row["news_count"])
            if len(message.encode("utf-16-le")) // 2 > 1024:
                self._finish(row["id"], "failed", "Rendered notification exceeds 1024 characters")
                return True
            with self.connect() as con:
                con.execute("UPDATE news_push_outbox SET message=? WHERE id=? AND status='sending'", (message, row["id"]))
            sending = True
            result = manager.call("NotificationsSend", [row["login"]], message)
            accepted = result is True or (type(result) is int and result == 0)
            rejected = result is False or (type(result) is int and result != 0)
            self._finish(row["id"], "accepted" if accepted else "failed" if rejected else "unknown",
                "Accepted by MT5 push service; device delivery is not confirmed" if accepted else
                "MT5 rejected the push; check manager notification rights and recipient settings" if rejected else
                "MT5 returned an unrecognized outcome; not retried")
        except Exception:
            self._finish(row["id"], "unknown" if sending else "failed",
                         "Send outcome unknown; not retried" if sending else "Unable to read or prepare the MT5 recipient")
        return True
