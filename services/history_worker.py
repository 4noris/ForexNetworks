"""Leased, checkpointed jobs. API lifecycle starts one lightweight supervisor.

SQLite leases coordinate all API processes sharing the database. A crashed
worker resumes a durable queued job; no browser schedules historical downloads.
"""
from datetime import datetime, timedelta, timezone
import logging
import threading
import time
import uuid

from services.historical import ArchiveSourceMismatch, number, text

log = logging.getLogger(__name__)


def normalize(record, kind, users, group, value):
    """Use actual SDK units. Unknown fields stay null; invalid identities fail batch."""
    source_id = value(record, "Deal" if kind == "deal" else "Order", default=None)
    login = value(record,"Login",default=None)
    timestamp = value(record,"TimeMsc" if kind == "deal" else "TimeDoneMsc",default=None)
    if not timestamp:
        timestamp = value(record,"Time" if kind == "deal" else "TimeDone",default=None)
        timestamp = int(timestamp)*1000 if timestamp else None
    if not source_id or not login or not timestamp:
        raise ValueError("Source history record lacks a stable ID, login or timestamp")
    login = int(login)
    if login not in users:
        return None
    user = users[login]
    currency = user.get("currency") or f"UNKNOWN:{login}"
    fields = ("Profit","Commission","Storage","Fee")
    money = [number(value(record,key,default=None)) for key in fields]
    action = int(value(record,"Action" if kind == "deal" else "Type",default=-1))
    volume_ext = number(value(record,"VolumeExt" if kind == "deal" else "VolumeInitialExt",default=None))
    volume = number(value(record,"Volume" if kind == "deal" else "VolumeInitial",default=None))
    lots = volume_ext / 100000000 if volume_ext is not None and volume_ext > 0 else volume / 10000 if volume is not None else None
    net = sum(money) if kind == "deal" and action in (0,1) and all(v is not None for v in money) else None
    raw_fields = ["Deal","Order","PositionID","Login","Symbol","Action","Type","Entry","Time","TimeMsc","TimeSetupMsc",
                  "TimeDoneMsc","Price","PriceOrder","PriceSL","PriceTP","Volume","VolumeExt","VolumeInitial","VolumeInitialExt",
                  "Profit","Commission","Storage","Fee","RateProfit","ContractSize","Digits","Reason","Flags"]
    raw = {key:value(record,key,default=None) for key in raw_fields}
    raw = {key:str(v) if not isinstance(v,(str,int,float,bool,type(None))) else v for key,v in raw.items()}
    return {"kind":kind,"source_id":str(source_id),"login":login,"account_group":group,"currency":currency,
            "symbol":str(value(record,"Symbol",default="") or ""),"time_ms":int(timestamp),"action":action,
            "entry":value(record,"Entry",default=None),"profit":text(money[0]),"commission":text(money[1]),
            "swap":text(money[2]),"fee":text(money[3]),"net":text(net),"lots":text(lots),"raw":raw}


class HistoryWorker:
    def __init__(self, store, provider, stop):
        self.store, self.provider, self.stop = store, provider, stop
        self.owner = uuid.uuid4().hex
        self.source_error = None
        self._source_lock = threading.Lock()
        self._source_checked_at = None
        self._source_ready = True

    def source_available(self):
        """Share a bounded source check across sync, snapshots and exports."""
        check = getattr(self.provider, "check_source", None)
        if check is None:
            return True
        with self._source_lock:
            now = time.monotonic()
            if self._source_checked_at is not None and now - self._source_checked_at < 5:
                return self._source_ready
            try:
                check()
            except ArchiveSourceMismatch as exc:
                if self.source_error is None:
                    log.warning("Historical processing blocked: %s", exc)
                self.source_error = str(exc)
                self._source_ready = False
            else:
                if self.source_error:
                    log.info("Historical source is compatible; processing resumed")
                self.source_error = None
                self._source_ready = True
            self._source_checked_at = now
            return self._source_ready

    def run(self):
        auxiliaries = [threading.Thread(target=self.snapshot_loop,name="history-snapshots",daemon=True),
                       threading.Thread(target=self.export_loop,name="history-exports",daemon=True)]
        for thread in auxiliaries:
            thread.start()
        while not self.stop.is_set():
            try:
                if not self.source_available():
                    self.stop.wait(5)
                    continue
                settings = self.store.settings()
                if self.store.lease("history-sync",self.owner):
                    try:
                        status = self.store.status()
                        now = datetime.now(timezone.utc)
                        reconciled = status["last_reconciliation"]
                        due_reconcile = now.hour >= settings["reconciliation_hour"] and (not reconciled or datetime.fromtimestamp(reconciled,timezone.utc).date() < now.date())
                        if due_reconcile:
                            self.store.enqueue("reconcile")
                        elif not status["last_success"] or time.time()-status["last_success"] >= settings["interval_minutes"]*60:
                            self.store.enqueue()
                        job = self.store.claim(self.owner)
                        if job:
                            self.execute(job,settings)
                    finally:
                        self.store.release("history-sync",self.owner)
            except Exception:
                log.exception("Historical supervisor will retry")
            self.stop.wait(5)
        for thread in auxiliaries:
            thread.join(1)

    def snapshot_loop(self):
        next_snapshot = 0
        while not self.stop.is_set():
            try:
                if not self.source_available():
                    self.stop.wait(5)
                    continue
                if time.time() >= next_snapshot and self.store.lease("history-snapshot",self.owner,60):
                    captured = self.provider.snapshot(self.store)
                    delay = self.store.settings()["snapshot_seconds"] if captured else 5
                    next_snapshot = time.time()+delay
                    self.store.lease("history-snapshot",self.owner,delay)
            except Exception:
                log.warning("Historical snapshot unavailable; retrying")
            self.stop.wait(5)

    def export_loop(self):
        while not self.stop.is_set():
            try:
                if self.store.lease("history-export",self.owner):
                    try:
                        self.process_export()
                    finally:
                        self.store.release("history-export",self.owner)
            except Exception:
                log.exception("Historical export supervisor will retry")
            self.stop.wait(2)

    def execute(self, job, settings):
        job_id = job["id"]
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()
        def heartbeat():
            while not heartbeat_stop.wait(20):
                try:
                    self.store.heartbeat(job_id,self.owner)
                except Exception:
                    lease_lost.set()
                    return
        thread = threading.Thread(target=heartbeat,name="history-lease",daemon=True)
        thread.start()
        try:
            end = int(time.time())
            initial = int(datetime.fromisoformat(settings["backfill_from"]).replace(tzinfo=timezone.utc).timestamp())
            groups = self.provider.groups()
            if not groups:
                raise RuntimeError("No historical synchronization groups configured")
            for group in groups:
                checkpoint = self.store.checkpoint(group)
                start = max(initial,checkpoint["through_ts"]-settings["overlap_days"]*86400) if checkpoint else initial
                if checkpoint and initial < checkpoint["coverage_from"]:
                    start = self.store.backfill_start(group,initial)
                if job["kind"] == "reconcile" and checkpoint:
                    start = max(initial,min(start,end-settings["reconcile_days"]*86400))
                while start <= end:
                    if self.stop.is_set() or lease_lost.is_set():
                        raise RuntimeError("Historical synchronization interrupted; checkpoint retained")
                    self.store.heartbeat(job_id,self.owner)
                    batch_end = min(end,start+86400-1)
                    for attempt in range(3):
                        try:
                            records = self.provider.fetch(group,start,batch_end)
                            if lease_lost.is_set():
                                raise RuntimeError("Historical synchronization lease lost")
                            self.store.heartbeat(job_id,self.owner)
                            self.store.save_batch(job_id,group,start,batch_end,records,owner=self.owner)
                            break
                        except Exception:
                            with self.store.connect() as con:
                                con.execute("UPDATE history_jobs SET failed_batches=failed_batches+1,retries=retries+? WHERE id=?",(int(attempt<2),job_id))
                            if attempt == 2:
                                raise
                            if self.stop.wait(2**attempt):
                                raise RuntimeError("Historical synchronization stopped")
                    start = batch_end+1
                    self.stop.wait(0.05)  # Yield between bounded SDK batches.
            self.store.finish(job_id,owner=self.owner)
        except Exception as exc:
            # Credentials and SDK exception details are kept out of status responses.
            log.warning("Historical job %s failed: %s",job_id,type(exc).__name__,exc_info=True)
            self.store.finish(job_id,"Historical source batch failed or worker stopped. Check server logs and MT5 availability; checkpoint retained.",owner=self.owner)
        finally:
            heartbeat_stop.set()
            thread.join(1)

    def process_export(self):
        import json
        if not self.source_available():
            return
        row = self.store.claim_export(self.owner)
        if not row:
            return
        # Extend the export lease for long report creation with a heartbeat.
        export_stop = threading.Event()
        lease_lost = threading.Event()
        def renew():
            while not export_stop.wait(20):
                try:
                    if self.store.renew_export(row["id"],self.owner):
                        continue
                except Exception:
                    pass
                lease_lost.set()
                return
        thread = threading.Thread(target=renew,daemon=True)
        thread.start()
        try:
            content = self.store.export(json.loads(row["filters"]),row["format"])
            if not lease_lost.is_set():
                self.store.finish_export(row["id"],self.owner,content=content)
        except Exception:
            log.exception("Historical export failed")
            if not lease_lost.is_set():
                self.store.finish_export(row["id"],self.owner,error="Export failed; retry or narrow the range")
        finally:
            export_stop.set()
            thread.join(1)
