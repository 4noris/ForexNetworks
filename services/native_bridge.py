"""Strict read-only projection of the Go/C++ feed into existing RMS views.

No CSV, demo records, price interpolation, or equity reconstruction is used.
Incomplete snapshots, dropped sequences and disconnects invalidate readiness.
"""
from collections import defaultdict
from datetime import datetime, timezone
import json
import gc
import zlib
import orjson
import math
import logging
import os
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from .native_totals import NativeAccountTotals
from .native_money import NativeGroupMoney


KINDS = {"users", "accounts", "positions", "groups", "symbols", "ticks", "summary", "exposure", "deals"}
ACCOUNT_FIELDS = ("Balance", "Credit", "Equity", "Margin", "Free Margin", "Margin Level %", "Floating PnL", "Swap")


def validate_account(row):
    if any(not isinstance(row.get(field), (int, float)) or not math.isfinite(row[field]) for field in ACCOUNT_FIELDS):
        raise ResyncRequired("Native account metrics are missing or invalid")


def current_publish_boundary(event, dirty, now_ms):
    """Drain delayed updates before rebuilding a potentially large account view."""
    received = event.get("received_ms")
    if not isinstance(received, (int, float)) or not 0 <= now_ms - received <= 2000:
        return False
    if dirty - {"ticks", "transport", "deals"}:
        if (event.get("kind"), event.get("op")) == ("accounts", "cycle_end"):
            return True
        # Positions precede their matching account reads. Publish that cycle
        # once, after account metrics arrive. Empty books have no account read.
        return (event.get("kind"), event.get("op"), event.get("data")) == ("positions", "replace", [])
    return True


class ResyncRequired(RuntimeError):
    pass


class CompressedStateStream:
    """Bounded gzip decoding without buffered-file reads across ACK boundaries."""
    def __init__(self, response):
        self.response = response
        self.decoder = zlib.decompressobj(31)
        self.pending = bytearray()
        self.encoded = b""

    def readline(self, limit=64 * 1024 * 1024 + 1):
        while True:
            end = self.pending.find(b"\n")
            if end >= 0 or len(self.pending) >= limit:
                end = min(end + 1 if end >= 0 else limit, limit)
                result = bytes(self.pending[:end])
                del self.pending[:end]
                return result
            encoded = self.encoded or self.response.read1(65536)
            if not encoded:
                if not self.decoder.eof or self.pending:
                    raise EOFError("Compressed native stream ended between frames")
                return b""
            data = self.decoder.decompress(encoded, min(65536, limit-len(self.pending)))
            self.pending.extend(data)
            self.encoded = self.decoder.unconsumed_tail
            if self.decoder.eof and self.decoder.unused_data:
                self.encoded = self.decoder.unused_data
                self.decoder = zlib.decompressobj(31)


class NativeProjection:
    def __init__(self):
        self.records = {}
        self.epoch = None
        self.sequence = 0
        self.profile_revision = 0
        self.account_ms = 0
        self.position_ms = 0
        self.connected = False
        self.complete = False
        self.server_time_msc = 0
        self.coverage_pending_since = None
        self.merged_users = {}
        self.dirty_users = set()
        self.previous_active = set()
        self.published_users = []
        self.published_logins = {}
        self.published_slots = {}
        self.totals = NativeAccountTotals()
        self.group_money = NativeGroupMoney()
        self.compactable_rows = ()

    def snapshot(self, value):
        if not value.get("complete") or not value.get("connected"):
            raise ResyncRequired("Native snapshot is incomplete")
        records = value.get("records", {})
        if not isinstance(records, dict) or any(kind not in KINDS for kind in records):
            raise ResyncRequired("Unknown native record schema")
        users, accounts = records.get("users", {}), records.get("accounts", {})
        if not set(users) <= set(accounts):
            raise ResyncRequired("Native accounts do not cover the user snapshot")
        for row in accounts.values():
            validate_account(row)
        self.records = records
        self.epoch = value["epoch"]
        self.sequence = int(value["sequence"])
        self.profile_revision = self.sequence
        self.account_ms = int(value.get("account_ms", 0))
        self.position_ms = int(value.get("position_ms", 0))
        self.server_time_msc = int(value.get("server_time_msc", 0))
        self.coverage_pending_since = None
        self.connected = self.complete = True
        self.merged_users = {}
        self.dirty_users = set(users)
        self.previous_active = set()
        self.published_users = []
        self.published_logins = {}
        self.published_slots = {}
        self.totals = NativeAccountTotals()
        self.group_money = NativeGroupMoney()
        self.compactable_rows = ()

    def apply(self, event):
        if event.get("op") == "keepalive":
            return "transport"
        if event.get("epoch") != self.epoch or event.get("sequence") != self.sequence + 1:
            raise ResyncRequired("Native event epoch or sequence changed")
        self.sequence = event["sequence"]
        kind, operation = event.get("kind"), event.get("op")
        if operation == "reset" or (operation == "status" and not event.get("data", {}).get("connected")):
            raise ResyncRequired("Native collector disconnected")
        if operation in {"heartbeat", "snapshot_end"}:
            clock = event.get("data", {}).get("server_time_msc")
            if isinstance(clock, int) and clock > 0:
                self.server_time_msc = clock
            return "transport"
        if kind not in KINDS:
            raise ResyncRequired("Unsupported native event")
        if kind == "users":
            self.profile_revision = self.sequence
        target = self.records.setdefault(kind, {})
        key, data = str(event.get("id", "")), event.get("data")
        if operation == "upsert":
            if not isinstance(data, dict):
                raise ResyncRequired("Native record is not an object")
            if kind == "accounts":
                validate_account(data)
            if kind in {"users", "accounts"}:
                self.dirty_users.add(key)
            target[key] = data
        elif operation == "delete":
            target.pop(key, None)
            if kind in {"users", "accounts"}:
                self.dirty_users.add(key)
            if kind == "users":
                self.merged_users.pop(key, None)
                self.records.setdefault("accounts", {}).pop(key, None)
                self.records["positions"] = {ticket: row for ticket, row in self.records.get("positions", {}).items() if str(row.get("Login")) != key}
        elif operation == "clean_login":
            self.records[kind] = {ticket: row for ticket, row in target.items() if str(row.get("Login")) != key}
        elif operation == "replace":
            if not isinstance(data, list):
                raise ResyncRequired("Native replacement is not an array")
            field = "Ticket" if kind == "positions" else "Symbol"
            replacement = {str(row[field]): row for row in data}
            if len(replacement) != len(data):
                raise ResyncRequired("Duplicate native replacement key")
            self.records[kind] = replacement
            if kind == "positions":
                self.position_ms = event["received_ms"]
        elif operation == "cycle_end" and kind == "accounts":
            self.account_ms = event["received_ms"]
        else:
            raise ResyncRequired("Unsupported native operation")
        return kind

    def apply_changes(self, frame):
        """Apply a complete version span of latest state, including tombstones."""
        if (frame.get("op") != "changes" or frame.get("epoch") != self.epoch
                or frame.get("base") != self.sequence or type(frame.get("sequence")) is not int
                or frame["sequence"] < self.sequence):
            raise ResyncRequired("Native state version or epoch changed")
        if not frame.get("connected") or not frame.get("complete"):
            raise ResyncRequired("Native source disconnected")
        updates = frame.get("updates", {})
        if not isinstance(updates, dict) or any(kind not in KINDS for kind in updates):
            raise ResyncRequired("Unknown native state schema")
        if any(type(frame.get(field)) is not int or frame[field] < 0
               for field in ("account_ms", "position_ms", "server_time_msc")):
            raise ResyncRequired("Invalid native observation times")
        patches=frame.get("patches", {})
        if not isinstance(patches,dict) or any(kind!="positions" for kind in patches):
            raise ResyncRequired("Invalid native field patches")
        if patches:
            updates={kind:dict(rows) if isinstance(rows,dict) else rows for kind,rows in updates.items()}
            for kind,rows in patches.items():
                if not isinstance(rows,dict):
                    raise ResyncRequired("Invalid native position patches")
                table=updates.setdefault(kind,{})
                if not isinstance(table,dict):
                    raise ResyncRequired("Invalid native update table")
                for key,fields in rows.items():
                    old=self.records.get(kind,{}).get(key)
                    if (not isinstance(fields,dict) or old is None or key in table
                            or not fields.keys() <= old.keys()):
                        raise ResyncRequired("Native position patch has no verified baseline")
                    table[key]={**old,**fields}
        # Validate the entire batch before changing a previously verified state.
        for kind, rows in updates.items():
            if not isinstance(rows, dict):
                raise ResyncRequired("Invalid native update table")
            for row in rows.values():
                if row is not None and not isinstance(row, dict):
                    raise ResyncRequired("Invalid native update row")
                if kind == "accounts" and row is not None:
                    validate_account(row)
        changed = set()
        for kind, rows in updates.items():
            target = self.records.setdefault(kind, {})
            if rows:
                changed.add(kind)
            for key, row in rows.items():
                if row is None:
                    target.pop(key, None)
                    if kind in {"users", "accounts"}:
                        self.dirty_users.add(key)
                    if kind == "users":
                        self.merged_users.pop(key, None)
                else:
                    target[key] = row
                    if kind in {"accounts", "users"}:
                        self.dirty_users.add(key)
        if updates.get("users"):
            self.profile_revision = frame["sequence"]
        for field in ("account_ms", "position_ms", "server_time_msc"):
            value = frame.get(field, 0)
            if field == "server_time_msc":
                if value > 0:
                    if value != self.server_time_msc:
                        changed.add("ticks")
                    self.server_time_msc = value
            else:
                if value != getattr(self, field):
                    changed.add("accounts" if field == "account_ms" else "positions")
                setattr(self, field, value)
        self.sequence = frame["sequence"]
        return changed

    def capture(self, previous=None):
        """Capture immutable rows quickly; view construction runs off the reader."""
        view = NativeProjection()
        view.records = {kind: dict(rows) for kind, rows in self.records.items()}
        for field in ("epoch", "sequence", "profile_revision", "account_ms", "position_ms", "connected", "complete", "server_time_msc"):
            setattr(view, field, getattr(self, field))
        view.dirty_users = set(self.dirty_users)
        self.dirty_users.clear()
        if previous is not None and previous.epoch == self.epoch:
            view.merged_users = previous.merged_users
            view.previous_active = previous.previous_active
            view.published_users = previous.published_users
            view.published_logins = previous.published_logins
            view.published_slots = previous.published_slots
            view.totals = previous.totals
            view.group_money = previous.group_money
            view.coverage_pending_since = previous.coverage_pending_since
            view.dirty_users.update(previous.dirty_users)
        else:
            view.dirty_users.update(view.records.get("users", {}))
        return view

    def publish(self, manager, still_current=lambda: True):
        profiles, accounts = self.records.get("users", {}), self.records.get("accounts", {})
        membership_changed = (len(profiles) != len(self.published_slots) or
            any((key in profiles) != (key in self.published_slots) for key in self.dirty_users))
        new_profiles = profiles.keys() - self.published_slots.keys() if membership_changed else set()
        changed_profiles = self.dirty_users | new_profiles
        if any(key in profiles and key not in accounts for key in changed_profiles):
            # User callbacks may arrive before the next native account-read cycle.
            # Keep the preceding verified generation until coverage completes.
            now = time.monotonic()
            if self.coverage_pending_since is None:
                self.coverage_pending_since = now
            if now - self.coverage_pending_since > 5:
                raise ResyncRequired("Native account coverage did not complete within five seconds")
            return
        self.coverage_pending_since = None
        positions = list(self.records.get("positions", {}).values())
        by_login = defaultdict(list)
        for position in positions:
            by_login[int(position["Login"])].append(position)
        active = {str(login) for login in by_login}
        # Keep previous generations immutable. Copy only changed accounts and
        # accounts whose floating positions may have changed or closed.
        changed = changed_profiles | active | self.previous_active
        updated = {}
        for key in changed:
            if key in profiles:
                updated[key] = dict(profiles[key])
                updated[key].update({field:accounts[key][field] for field in ACCOUNT_FIELDS})
        updated_rows = list(updated.values())
        manager._merge_live_position_pnl(updated_rows, {int(row["Login"]): row for row in updated_rows}, positions)
        incremental_metrics = hasattr(manager, "_build_native_metrics")
        if membership_changed:
            removed = self.published_slots.keys() - profiles.keys()
            for key in removed:
                old_row = self.published_users[self.published_slots[key]]
                if incremental_metrics:
                    self.totals.replace(key, old_row, None)
                    self.group_money.replace(old_row, None)
                self.merged_users.pop(key, None)
        for key in (profiles if membership_changed else updated):
            if key not in updated:
                continue
            row = updated[key]
            old_row = (self.published_users[self.published_slots[key]]
                       if key in self.published_slots else None)
            if incremental_metrics:
                self.totals.replace(key, old_row, row)
                self.group_money.replace(old_row, row)
        self.merged_users.update(updated)
        if membership_changed:
            users = [self.merged_users[key] for key in profiles]
            users_by_login = {int(row["Login"]): row for row in users}
            self.published_slots = {key: index for index, key in enumerate(profiles)}
        else:
            users = list(self.published_users)
            users_by_login = dict(self.published_logins)
            for key, row in updated.items():
                users[self.published_slots[key]] = row
                users_by_login[int(row["Login"])] = row
        self.published_users, self.published_logins = users, users_by_login
        self.compactable_rows = tuple(updated)
        self.dirty_users.clear()
        self.previous_active = active
        groups = self.records.get("groups", {})
        currencies = {key: str(row.get("Currency", "")) for key, row in groups.items()}
        source_time = (min(self.account_ms, self.position_ms) if positions else self.position_ms) / 1000
        update = {"users": users, "users_by_login": users_by_login,
            "positions": positions, "positions_by_login": dict(by_login),
            "groups": sorted(groups), "group_currencies": currencies,
            "symbol_cache": dict(self.records.get("symbols", {})),
            "summary_rows": list(self.records.get("summary", {}).values()),
            "exposure_rows": list(self.records.get("exposure", {}).values()),
            "metrics": self.totals.read(self.merged_users, positions) if incremental_metrics else manager._build_metrics(users, positions),
            "last_update": source_time, "status": "connected", "errors": [],
            "refresh_phase": "cpp_native_live", "native_epoch": self.epoch,
            "native_sequence": self.sequence, "native_profile_revision": self.profile_revision, "native_received_at": time.time()}
        if incremental_metrics:
            update["native_money_totals"] = self.group_money.snapshot()
        with manager.cache_lock:
            if not still_current():
                return False
            manager.cache.update(update)
        self.publish_ticks(manager, still_current)
        return True

    def compact_rows(self, state):
        """Share verified immutable rows instead of retaining three dictionaries.

        Account-authoritative columns are selected explicitly when merging, so
        aliased financial rows can never override a newer profile name/group.
        Identity checks preserve callbacks received since this view was captured.
        The caller holds the reader's state lock.
        """
        if state.epoch != self.epoch:
            return
        for key in self.compactable_rows:
            row = self.merged_users[key]
            for kind in ("users", "accounts"):
                observed = self.records[kind].get(key)
                if state.records.get(kind,{}).get(key) is observed:
                    state.records[kind][key] = row
                self.records[kind][key] = row
        self.compactable_rows = ()

    def publish_ticks(self, manager, still_current=lambda: True):
        checked = datetime.now(timezone.utc).isoformat()
        with manager.cache_lock:
            previous = dict(manager.cache.get("tick_cache", {}))
        for symbol, tick in self.records.get("ticks", {}).items():
            bid, ask = tick.get("Bid"), tick.get("Ask")
            stamp = tick.get("TimeMsc") or (tick.get("Time", 0) * 1000)
            valid = all(isinstance(value, (float, int)) and math.isfinite(value) and value > 0 for value in (bid, ask, stamp)) and ask >= bid
            previous[symbol] = {"available": valid, "bid": bid if valid else None, "ask": ask if valid else None,
                "tick_time": datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat() if stamp else None,
                "checked_at": checked, "source": "mt5_cpp_tick_callback"}
        with manager.cache_lock:
            if not still_current():
                return False
            manager.cache["tick_cache"] = previous
            if self.server_time_msc:
                manager.cache["server_time_msc"] = self.server_time_msc
                manager.cache["server_time_checked_at"] = checked


class NativeBridge:
    def __init__(self, manager):
        self.manager = manager
        self.url = os.environ["RMS_NATIVE_URL"].rstrip("/")
        self.token = os.environ["RMS_NATIVE_TOKEN"]
        self.response = None
        self.state = NativeProjection()
        self.state_lock = threading.Lock()
        self.publish_event = threading.Event()
        self.closed = threading.Event()
        self.revision = 0
        self.available = False
        self.publisher = None
        self.core_pending = False
        self.reader_diagnostic_at = 0

    def request(self, path, timeout=5):
        headers = {"Authorization": "Bearer " + self.token}
        if path.startswith("/internal/native/changes?"):
            headers["Accept-Encoding"] = "gzip"
            headers["X-Native-Field-Patches"] = "1"
        return urlopen(Request(self.url + path, headers=headers), timeout=timeout)

    def acknowledge(self, frame):
        if frame.get("stream_id"):
            body=json.dumps({"stream_id":frame["stream_id"],"sequence":frame["sequence"]}).encode()
            with urlopen(Request(self.url+"/internal/native/ack",data=body,
                                headers={"Authorization":"Bearer "+self.token,"Content-Type":"application/json"}), timeout=5):
                pass

    def unavailable(self, reason):
        self.available = False
        with self.state_lock:
            self.state = NativeProjection()
            self.core_pending = False
        self.publish_event.set()
        with self.manager.cache_lock:
            # The UI retains its explicitly dated last-verified view. Drop
            # superseded raw generations here before parsing a replacement.
            self.manager.cache.update({"users":[], "users_by_login":{}, "positions":[], "positions_by_login":{},
                                       "summary_rows":[], "exposure_rows":[], "symbol_cache":{}, "tick_cache":{}, "metrics":{}, "native_money_totals":{}})
            self.manager.cache["status"] = "disconnected"
            self.manager.cache["errors"] = [reason]
            self.manager.cache["refresh_phase"] = "native_unavailable"

    def close(self):
        self.closed.set()
        self.publish_event.set()
        if self.response:
            self.response.close()

    def _publish_loop(self):
        previous = None
        last_revision = -1
        last_publish = 0
        last_diagnostic = 0
        while not self.closed.is_set() and not self.manager.stop_event.is_set():
            if not self.publish_event.wait(0.2):
                continue
            self.publish_event.clear()
            if not self.available:
                previous = None
                view = None
                last_revision = -1
                continue
            self.closed.wait(max(0, 0.1 - (time.monotonic() - last_publish)))
            try:
                with self.state_lock:
                    revision = self.revision
                    core = self.core_pending
                    self.core_pending = False
                    if core:
                        view = self.state.capture(previous if revision == last_revision else None)
                        previous, last_revision = view, revision
                    else:
                        view = NativeProjection()
                        view.records = {"ticks": dict(self.state.records.get("ticks", {}))}
                        view.server_time_msc = self.state.server_time_msc
            except MemoryError:
                self.unavailable("MT5 live cache is recovering from memory pressure; last verified values are delayed")
                previous = view = None
                if self.response:
                    self.response.close()
                self.closed.wait(1)
                continue
            valid = lambda: self.available and self.revision == revision and not self.closed.is_set()
            if not core:
                try:
                    view.publish_ticks(self.manager, valid)
                except (ValueError, KeyError, MemoryError) as exc:
                    logging.getLogger(__name__).warning("Native quote publication unavailable: %s", exc)
                last_publish = time.monotonic()
                continue
            # Stale observations stay visibly stale. They do not require
            # closing a healthy ordered event stream and reloading all users.
            source_ms = min(view.account_ms, view.position_ms) if view.records.get("positions") else view.position_ms
            if not 0 <= time.time() * 1000 - source_ms <= 10000:
                continue
            try:
                started=time.monotonic()
                published = view.publish(self.manager, valid)
                if published:
                    with self.state_lock:
                        if valid():
                            view.compact_rows(self.state)
                last_publish = time.monotonic()
                if last_publish-last_diagnostic>=10:
                    logging.getLogger(__name__).info("Native projection: build_ms=%.0f source_age_ms=%.0f accounts=%d",
                        (last_publish-started)*1000,time.time()*1000-source_ms,len(view.records.get("users",{})))
                    last_diagnostic=last_publish
            except (ValueError, KeyError, ResyncRequired, MemoryError) as exc:
                logging.getLogger(__name__).warning("Native publication unavailable: %s", exc)
                self.unavailable("Native account coverage is incomplete; live values are not current")
                if self.response:
                    self.response.close()

    def run(self):
        collection_start = {}
        def collection_timing(phase, info):
            generation=info["generation"]
            if phase=="start":
                collection_start[generation]=time.monotonic()
            else:
                duration=time.monotonic()-collection_start.pop(generation,time.monotonic())
                if duration>=0.05:
                    logging.getLogger(__name__).info("Native process GC: generation=%d elapsed_ms=%.0f",generation,duration*1000)
        diagnose_gc=os.environ.get("RMS_NATIVE_GC_DIAGNOSTICS")=="1"
        if diagnose_gc:
            gc.callbacks.append(collection_timing)
        self.publisher = threading.Thread(target=self._publish_loop, name="rms-native-publisher", daemon=True)
        self.publisher.start()
        while not self.manager.stop_event.is_set():
            try:
                with self.request("/internal/native/health") as response:
                    status = json.load(response)
                if not status.get("ready"):
                    raise ResyncRequired("Waiting for a complete, connected native MT5 snapshot")
                with self.request("/internal/native/snapshot", timeout=15) as response:
                    source = json.load(response)
                with self.state_lock:
                    self.state = NativeProjection()
                    self.state.snapshot(source)
                    self.revision += 1
                    self.available = True
                    self.core_pending = True
                del source
                self.publish_event.set()
                query = urlencode({"after": self.state.sequence, "epoch": self.state.epoch})
                with self.request("/internal/native/changes?" + query, timeout=5) as response:
                    self.response = response
                    stream = CompressedStateStream(response) if getattr(response, "headers", {}).get("Content-Encoding") == "gzip" else response
                    while not self.manager.stop_event.is_set():
                        read_start = time.monotonic()
                        line = stream.readline(64 * 1024 * 1024 + 1)
                        read_end = time.monotonic()
                        if not line or len(line) > 64 * 1024 * 1024:
                            raise ResyncRequired("Native feed ended or exceeded the message limit")
                        event = orjson.loads(line)
                        with self.state_lock:
                            if event.get("op") == "changes":
                                changed = self.state.apply_changes(event)
                                core = bool(changed - {"ticks", "deals", "transport"})
                                ticks = "ticks" in changed
                            else:
                                kind = self.state.apply(event)
                                core = (kind == "accounts" and event.get("op") == "cycle_end") or (
                                    kind == "positions" and event.get("op") == "replace" and not event.get("data")
                                )
                                ticks = kind == "ticks"
                            if core:
                                self.core_pending = True
                        if core or ticks:
                            self.publish_event.set()
                        applied_at = time.monotonic()
                        self.acknowledge(event)
                        finished_at = time.monotonic()
                        if finished_at-self.reader_diagnostic_at >= 10:
                            logging.getLogger(__name__).info(
                                "Native reader: bytes=%d read_ms=%.0f apply_ms=%.0f ack_ms=%.0f source_age_ms=%.0f",
                                len(line), (read_end-read_start)*1000,(applied_at-read_end)*1000,
                                (finished_at-applied_at)*1000,time.time()*1000-min(self.state.account_ms,self.state.position_ms))
                            self.reader_diagnostic_at=finished_at
            except (OSError, ValueError, KeyError, ResyncRequired, HTTPError, MemoryError, EOFError, zlib.error) as exc:
                logging.getLogger(__name__).warning("Native feed resynchronizing: %s", exc)
                self.unavailable("Native MT5 feed is unavailable or resynchronizing; live values are not current")
                self.manager.stop_event.wait(1)
            finally:
                self.response = None
                source = None
        self.closed.set()
        self.publish_event.set()
        if diagnose_gc:
            gc.callbacks.remove(collection_timing)
