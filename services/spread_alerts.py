"""Server-side spread alerts from fresh MT5 quotes, independent of browser tabs."""
from datetime import datetime, timezone
import json
import math
import threading

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA = """
CREATE TABLE IF NOT EXISTS spread_alert_settings(
 id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL,
 updated_by TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spread_alert_state(
 source TEXT NOT NULL, symbol TEXT NOT NULL, threshold REAL NOT NULL,
 active INTEGER NOT NULL, last_queued REAL NOT NULL,
 PRIMARY KEY(source,symbol));
CREATE TABLE IF NOT EXISTS spread_alert_events(
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, symbol TEXT NOT NULL,
 spread REAL NOT NULL, threshold REAL NOT NULL, bid REAL NOT NULL, ask REAL NOT NULL,
 tick_time TEXT NOT NULL, created_at REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_spread_pending ON spread_alert_events(status,id);
"""


class SpreadAlertSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: bool = False
    default_threshold_points: float | None = Field(default=None, gt=0, le=1e9)
    symbol_thresholds: dict[str, float] = Field(default_factory=dict, max_length=2000)
    cooldown_seconds: int = Field(default=300, ge=30, le=86400)

    @field_validator("symbol_thresholds")
    @classmethod
    def validate_symbols(cls, values):
        clean = {}
        for symbol, value in values.items():
            symbol = symbol.strip()
            if not symbol or len(symbol) > 128 or any(ord(c) < 32 for c in symbol):
                raise ValueError("Use exact MT5 symbol names, including their suffixes")
            if not math.isfinite(value) or not 0 < value <= 1e9:
                raise ValueError("Spread thresholds must be finite and greater than zero")
            if symbol in clean:
                raise ValueError("Each symbol can have only one spread threshold")
            clean[symbol] = value
        return clean


def fresh_spreads(symbols, ticks, reference_time, max_age=5):
    """Points = (ask-bid) * 10**Digits; broker time, never client wall time."""
    rows = []
    for name, tick in ticks.items():
        spec = symbols.get(name)
        if not spec or not tick.get("available"):
            continue
        try:
            digits = int(spec["Digits"])
            bid, ask = float(tick["bid"]), float(tick["ask"])
            stamp = datetime.fromisoformat(str(tick["tick_time"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            age = (reference_time - stamp).total_seconds()
            if not (0 <= digits <= 12 and math.isfinite(bid) and math.isfinite(ask)
                    and 0 < bid <= ask and -1 <= age <= max_age):
                continue
            spread = round((ask - bid) * 10**digits, 6)
            rows.append({"symbol": name, "spread": spread, "bid": bid, "ask": ask,
                         "tick_time": tick["tick_time"]})
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return rows


class SpreadAlertService:
    def __init__(self, connection, sender, delivery_enabled):
        self.connection = connection
        self.sender = sender
        self.delivery_enabled = delivery_enabled
        self.lock = threading.RLock()
        self.states = {}
        self.source = None
        self.settings = None

    def reset_cache(self):
        with self.lock:
            self.settings = None
            self.states = {}
            self.source = None

    def recover_interrupted(self):
        with self.connection() as con:
            con.execute("UPDATE spread_alert_events SET status='unknown',detail='Delivery interrupted; acceptance is unconfirmed' WHERE status='sending'")

    def read_settings(self):
        with self.lock:
            if self.settings is None:
                with self.connection() as con:
                    row = con.execute("SELECT payload FROM spread_alert_settings WHERE id=1").fetchone()
                self.settings = SpreadAlertSettings.model_validate_json(row[0]) if row else SpreadAlertSettings()
            return self.settings.model_copy(deep=True)

    def save_settings(self, settings, actor):
        with self.lock, self.connection() as con:
            con.execute("INSERT INTO spread_alert_settings VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                        "payload=excluded.payload,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                        (settings.model_dump_json(), actor, datetime.now(timezone.utc).isoformat()))
            # Changed settings must never deliver a breach detected under old rules.
            con.execute("UPDATE spread_alert_events SET status='cancelled',detail='Settings changed' WHERE status='queued'")
            self.settings = settings.model_copy(deep=True)

    def scan(self, rows, source, now):
        with self.lock:
            settings = self.read_settings()
            if not settings.enabled or not self.delivery_enabled():
                return 0
            if source != self.source:
                with self.connection() as con:
                    self.states = {row["symbol"]: dict(row) for row in con.execute(
                        "SELECT * FROM spread_alert_state WHERE source=?", (source,))}
                self.source = source
            updates, events = [], []
            pending = None
            for row in rows:
                name, spread = row["symbol"], row["spread"]
                threshold = settings.symbol_thresholds.get(name, settings.default_threshold_points)
                if threshold is None:
                    continue
                state = self.states.get(name, {"active": False, "last_queued": 0, "threshold": threshold})
                active = bool(state["active"])
                # A small recovery band avoids repeated crossings at the boundary.
                if spread <= threshold * .9:
                    active = False
                breach = spread > threshold
                due = breach and (not state["last_queued"] or now - state["last_queued"] >= settings.cooldown_seconds)
                # Reserve capacity before persisting the cooldown; never lose alerts to a full queue.
                queued = False
                if due:
                    if pending is None:
                        with self.connection() as con:
                            pending = con.execute("SELECT COUNT(*) FROM spread_alert_events WHERE status='queued'").fetchone()[0]
                    if pending + len(events) < 128:
                        events.append((source, name, spread, threshold, row["bid"], row["ask"], row["tick_time"], now))
                        state = {**state, "last_queued": now}
                        queued = True
                new = {"active": active or breach, "last_queued": state["last_queued"], "threshold": threshold}
                if new != {key: state[key] for key in new} or queued:
                    updates.append((source, name, threshold, int(new["active"]), new["last_queued"]))
                self.states[name] = new
            if updates or events:
                with self.connection() as con:
                    con.executemany("INSERT INTO spread_alert_state VALUES(?,?,?,?,?) ON CONFLICT(source,symbol) "
                                    "DO UPDATE SET threshold=excluded.threshold,active=excluded.active,last_queued=excluded.last_queued", updates)
                    con.executemany("INSERT INTO spread_alert_events(source,symbol,spread,threshold,bid,ask,tick_time,created_at) "
                                    "VALUES(?,?,?,?,?,?,?,?)", events)
                    con.execute("DELETE FROM spread_alert_events WHERE id < (SELECT COALESCE(MAX(id),0)-1000 FROM spread_alert_events) "
                                "AND status NOT IN ('queued','sending')")
            return len(events)

    def deliver_one(self, source, now):
        if not self.read_settings().enabled or not self.delivery_enabled():
            return False
        with self.lock, self.connection() as con:
            row = con.execute("SELECT * FROM spread_alert_events WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return False
            row = dict(row)
            settings = self.read_settings()
            if (not settings.enabled or not self.delivery_enabled() or source != row["source"]
                    or now - row["created_at"] > 15):
                con.execute("UPDATE spread_alert_events SET status='cancelled',detail='Alert expired or delivery disabled' WHERE id=?", (row["id"],))
                return True
            con.execute("UPDATE spread_alert_events SET status='sending' WHERE id=?", (row["id"],))
        message = (f"MT5 SPREAD ALERT\nSymbol: {row['symbol']}\nSpread: {row['spread']:g} points\n"
                   f"Threshold: {row['threshold']:g} points\nBid / Ask: {row['bid']:g} / {row['ask']:g}\n"
                   f"MT5 tick: {row['tick_time']}")
        try:
            ok, _ = self.sender(message, route="spread")
        except Exception:
            ok = False
        with self.connection() as con:
            con.execute("UPDATE spread_alert_events SET status=?,detail=? WHERE id=?",
                        ("sent" if ok else "failed", "Delivered" if ok else "Telegram delivery failed; check bot and channel settings", row["id"]))
        return True

    def status(self):
        with self.connection() as con:
            row = con.execute("SELECT symbol,spread,threshold,status,detail,created_at FROM spread_alert_events ORDER BY id DESC LIMIT 1").fetchone()
        return {"settings": self.read_settings().model_dump(), "last_alert": dict(row) if row else None,
                "telegram_enabled": bool(self.delivery_enabled()), "quote_max_age_seconds": 5}
