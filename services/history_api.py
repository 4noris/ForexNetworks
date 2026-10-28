"""Existing application adapters and authenticated database-only reporting routes."""
from datetime import date, datetime, timedelta, timezone
import json
import os
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, model_validator

from services.historical import ArchiveSourceMismatch, HistoryStore, number
from services.history_worker import HistoryWorker, normalize


class HistoryFilters(BaseModel):
    from_date: date
    to_date: date
    group: str = Field(default="",max_length=256)
    login: int | None = Field(default=None,ge=1)
    symbol: str = Field(default="",max_length=128)
    currency: str = Field(default="",max_length=32)

    @model_validator(mode="after")
    def dates(self):
        if self.from_date > self.to_date or (self.to_date-self.from_date).days > 3660:
            raise ValueError("Choose an ordered range of at most ten years")
        if self.to_date > datetime.now(timezone.utc).date():
            raise ValueError("Future UTC dates are unavailable")
        return self

    def filters(self):
        values = self.model_dump(mode="json")
        return {key:values[key] for key in HistoryFilters.model_fields}


class SnapshotRiskThresholds(BaseModel):
    enabled: bool = True
    exposure_increase_pct: float = Field(default=100,ge=1,le=100000,allow_inf_nan=False)
    minimum_lot_increase: float = Field(default=1,gt=0,le=1000000,allow_inf_nan=False)
    floating_change_pct: float = Field(default=20,ge=1,le=100000,allow_inf_nan=False)
    minimum_floating_change: float = Field(default=100,gt=0,allow_inf_nan=False)
    margin_utilization_pct: float = Field(default=80,ge=1,le=10000,allow_inf_nan=False)


class HistorySettings(BaseModel):
    interval_minutes: int = Field(default=60,ge=30,le=1440)
    snapshot_seconds: int = Field(default=300,ge=60,le=3600)
    reconciliation_hour: int = Field(default=2,ge=0,le=23)
    backfill_from: date
    risk_thresholds: SnapshotRiskThresholds | None = None

    @model_validator(mode="after")
    def dates(self):
        if self.backfill_from > datetime.now(timezone.utc).date():
            raise ValueError("Backfill must start on or before today UTC")
        return self


class ExportRequest(HistoryFilters):
    format: str = Field(default="csv",pattern="^(csv|xlsx)$")
    report: str = Field(default="accounts",pattern="^(accounts|pnl|exposure|trading-activity|risk-events)$")
    dimension: str = Field(default="day",pattern="^(day|week|month|login|account_group|symbol)$")
    kind: str = Field(default="deal",pattern="^(deal|order|closed)$")


class SyncRequest(BaseModel):
    kind: str = Field(default="incremental",pattern="^(incremental|reconcile)$")


def validated_filters(from_date: date, to_date: date, group: str=Query("",max_length=256),
                      login: int | None=Query(None,ge=1),symbol: str=Query("",max_length=128),currency: str=Query("",max_length=32)):
    try:
        return HistoryFilters(from_date=from_date,to_date=to_date,group=group,login=login,symbol=symbol,currency=currency)
    except ValueError as exc:
        raise HTTPException(422,"Choose an ordered UTC range of at most ten years ending on or before today") from exc


class MT5HistoryProvider:
    def __init__(self, app, store):
        self.app, self.store = app, store
        self.metadata = {}

    def check_source(self):
        server, login, _ = self.app.configured_manager_credentials()
        self.store.bind_source(f"{server}|{login}")

    def verify_source(self):
        server, login, _ = self.app.configured_manager_credentials()
        self.store.verify_source(f"{server}|{login}")

    def users_for_group(self, sdk, group):
        cached = self.metadata.get(group)
        if cached and time.monotonic()-cached[0] < 60:
            return cached[1]
        a = self.app
        raw_users, error = a.request_users_by_group(sdk,group)
        if error:
            raise RuntimeError("Account metadata unavailable")
        import mt5_connection
        manager = mt5_connection.get_manager()
        with manager.cache_lock:
            currency = manager.cache.get("group_currencies",{}).get(group) or ""
        users = {}
        for user in raw_users:
            if str(a._value(user,"Group",default="")) == group:
                login = int(a._value(user,"Login",default=0))
                if login:
                    users[login] = {"currency":currency or a._value(user,"Currency",default="")}
        self.metadata[group] = (time.monotonic(),users,currency)
        return users

    def groups(self):
        configured = [g.strip() for g in os.getenv("HISTORY_GROUPS", "").split(";") if g.strip()]
        if configured:
            return sorted(set(configured))
        settings = self.app.get_book_group_settings()
        return sorted({g for book in ("a_book","b_book","cent","hybrid") for g in settings.get(book,[])})

    def snapshot(self, store):
        self.check_source()
        import mt5_connection
        manager = mt5_connection.get_manager()
        groups = self.groups()
        if not manager or not manager.is_connected or not groups:
            return False
        view = manager.live_risk_cache_view(groups)
        with manager.cache_lock:
            specs = dict(manager.cache.get("symbol_cache",{}))
            currencies = dict(manager.cache.get("group_currencies",{}))
        captured = store.snapshots(view,specs)
        # Initial history can arrive before group metadata. Repair unresolved
        # currencies from the real group configuration, never from a USD default.
        for group in store.unknown_currency_groups():
            currency=currencies.get(group)
            if not currency:
                with self.app.yielding_history_manager_session() as sdk:
                    method=getattr(sdk,"GroupRequest",None)
                    if callable(method):
                        raw=method(group)
                        code,raw,_=self.app._unwrap_response(raw)
                        if code in (None,0):
                            currency=str(self.app._value(raw,"Currency",default="") or "").strip()
            if currency:
                store.resolve_group_currency(group,currency)
        return captured

    def fetch(self, group, start, end):
        self.check_source()
        a = self.app
        with a.yielding_history_manager_session() as sdk:
            users = self.users_for_group(sdk,group)
            deals, error = a.request_deals_by_group(sdk,group,start,end)
            if error:
                raise RuntimeError("Deal history batch unavailable")
            result = []
            for record in deals:
                # Group-scoped source history can include a login no longer in
                # today's user catalog. Preserve its source row instead of
                # silently losing history; unknown currency remains per-login.
                source_login = int(a._value(record,"Login",default=0))
                users.setdefault(source_login,{"currency":self.metadata[group][2]})
                row = normalize(record,"deal",users,group,a._value)
                if row and start*1000 <= row["time_ms"] < (end+1)*1000:
                    result.append(row)
            # Verified against the installed SDK: HistoryRequest takes a login;
            # HistoryRequestByGroup takes a group. Missing SDK support
            # remains explicit in capabilities; it never blocks supported deal history.
            method = getattr(sdk,"HistoryRequestByGroup",None)
            if callable(method):
                response = method(group,start,end)
                code, _, _ = a._unwrap_response(response)
                if code in {1,13} or (response is False and a.brokerage_pnl._last_error_code() in {1,13}):
                    orders = []
                else:
                    data, error = a._response_to_data(response,"HistoryRequestByGroup")
                    if error or response is False:
                        raise RuntimeError("Order history batch unavailable")
                    orders = a._as_list(data)
                for record in orders:
                    source_login = int(a._value(record,"Login",default=0))
                    users.setdefault(source_login,{"currency":self.metadata[group][2]})
                    row = normalize(record,"order",users,group,a._value)
                    if row and start*1000 <= row["time_ms"] < (end+1)*1000:
                        result.append(row)
            return result


def install(app_module):
    a = app_module
    store = HistoryStore(a.DB_PATH)
    initial = os.getenv("HISTORY_BACKFILL_FROM") or (datetime.now(timezone.utc).date()-timedelta(days=30)).isoformat()
    date.fromisoformat(initial)
    store.migrate(initial,int(os.getenv("HISTORY_SYNC_INTERVAL_MINUTES","60")),
                  int(os.getenv("HISTORY_SNAPSHOT_SECONDS","300")),int(os.getenv("HISTORY_RECONCILIATION_HOUR_UTC","2")))
    provider = MT5HistoryProvider(a,store)
    worker = HistoryWorker(store,provider,a._RUNTIME_MAINTENANCE_STOP)
    router = APIRouter(prefix="/api/dealing-risk")

    def authorized(request: Request):
        # Existing app has administrator roles, not a per-user group entitlement
        # model. Match risk-event access, denying non-admins rather than guessing.
        return a._operations_admin_actor(request)

    def source_issue():
        try:
            provider.verify_source()
        except ArchiveSourceMismatch as exc:
            return str(exc)
        return None

    def archive_actor(actor=Depends(authorized)):
        issue = source_issue()
        if issue:
            raise HTTPException(409, issue)
        return actor

    @router.get("/config")
    def refresh_config():
        return {"live_refresh_seconds":max(5,min(60,float(os.getenv("LIVE_RISK_REFRESH_SECONDS","5")))),
                "operational_refresh_seconds":max(15,min(300,float(os.getenv("OPERATIONAL_REFRESH_SECONDS","30"))))}

    @router.get("/health")
    def risk_health(actor=Depends(authorized)):
        import mt5_connection
        manager=mt5_connection.get_manager()
        connected=bool(manager and manager.is_connected)
        groups=manager.group_cache_view() if manager else []
        marker=manager.update_marker() if manager else 0
        age=max(0,time.time()-marker) if marker else None
        books=a.get_book_group_settings()
        mismatches=[{"book":book,"group":group} for book in ("a_book","b_book","cent","hybrid")
                    for group in books.get(book,[]) if group not in groups]
        with store.connect() as con:
            unresolved=[dict(row) for row in con.execute("SELECT account_group,COUNT(*) records FROM history_records WHERE currency LIKE 'UNKNOWN:%' GROUP BY account_group")]
            limits=con.execute("SELECT COUNT(*) FROM exposure_limits WHERE enabled=1").fetchone()[0]
            alerts=con.execute("SELECT COUNT(*) FROM risk_events WHERE status NOT IN ('RESOLVED','CLOSED') AND severity IN ('critical','high')").fetchone()[0]
            pending=con.execute("SELECT COUNT(*) FROM risk_approval_queue WHERE status='pending'").fetchone()[0]
        archive=store.status()
        issue=source_issue()
        checks=[{"id":"connection","label":"MT5 connection","state":"ok" if connected else "critical","value":"Connected" if connected else "Disconnected","href":"/manager"},
                {"id":"freshness","label":"Live data","state":"ok" if connected and age is not None and age<=10 else "warning","value":f"{age:.1f}s old" if age is not None else "Initializing","href":"/manager"},
                {"id":"groups","label":"Book mappings","state":"warning" if mismatches else "ok","value":f"{len(mismatches)} need review" if mismatches else "Aligned with MT5","href":"/settings"},
                {"id":"currency","label":"Historical currencies","state":"warning" if unresolved else "ok","value":f"{sum(row['records'] for row in unresolved)} unresolved records" if unresolved else "Verified","href":"/dealing-risk/analytics"},
                {"id":"archive","label":"Historical sync","state":"critical" if issue else "ok" if archive["last_success"] and not archive["snapshot_stale"] else "warning","value":"Source conflict" if issue else "Current snapshots" if not archive["snapshot_stale"] else "Snapshot delayed","href":"/dealing-risk/analytics"}]
        return {"checks":checks,"groups":groups,"group_mismatches":mismatches,"unresolved_currencies":unresolved,
                "source_updated_at":marker or None,"data_age_seconds":age,"connected":connected,"stale":not connected or age is None or age>10,
                "enabled_limits":limits,"open_high_risk_events":alerts,"pending_approvals":pending,
                "generated_at":datetime.now(timezone.utc).isoformat(),"status":"attention" if any(c["state"]!='ok' for c in checks) else "healthy"}

    @router.get("/sync/status")
    def status(actor=Depends(authorized)):
        result = store.status()
        result["source_error"] = source_issue()
        result["sync_blocked"] = bool(result["source_error"])
        from calendar_feed import _server_zone
        result["manager_timezone"] = str(_server_zone())
        import mt5_connection
        manager = mt5_connection.get_manager()
        result["source_connected"] = bool(manager and manager.is_connected)
        result["configured_groups"] = provider.groups()
        result["capabilities"] = {"deals": bool(manager and manager.api and callable(getattr(manager.api,"DealRequestByGroup",None))),
                                  "orders": bool(manager and manager.api and callable(getattr(manager.api,"HistoryRequestByGroup",None))),
                                  "snapshots":"Observed since archive activation only", "deleted_records":"Not inferred from absence; requires source tombstones"}
        return result

    @router.post("/sync/run")
    def sync(req: SyncRequest, actor=Depends(archive_actor)):
        job_id = store.enqueue(req.kind,actor)
        if not job_id:
            raise HTTPException(429,"Wait at least 30 seconds between synchronization requests")
        a.write_audit(actor,"history.sync_queued","history",job_id,{"kind":req.kind})
        return {"job_id":job_id,"status":"queued"}

    @router.put("/sync/settings")
    def settings(req: HistorySettings, actor=Depends(authorized)):
        store.configure(req.interval_minutes,req.snapshot_seconds,req.reconciliation_hour,req.backfill_from.isoformat(),req.risk_thresholds.model_dump() if req.risk_thresholds else None)
        a.write_audit(actor,"history.settings","history","schedule",req.model_dump(mode="json"))
        return store.settings()

    @router.get("/analytics/catalog")
    def catalog(actor=Depends(archive_actor)):
        with store.connect() as con:
            groups = [r[0] for r in con.execute("SELECT DISTINCT account_group FROM history_checkpoints ORDER BY account_group")]
            currencies = [r[0] for r in con.execute("SELECT DISTINCT currency FROM history_daily ORDER BY currency")]
            symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM history_daily WHERE symbol<>'' ORDER BY symbol")]
        return {"groups":sorted(set(groups+provider.groups())),"currencies":currencies,"symbols":symbols}

    @router.get("/realized")
    def realized(filters: HistoryFilters=Depends(validated_filters),groups: list[str]=Query(default=[],max_length=1000),actor=Depends(archive_actor)):
        if not groups:
            raise HTTPException(400,"Select at least one group")
        result = store.report({**filters.filters(),"groups":sorted(set(groups))},"day",1,1)
        result["coverage"] = [store.checkpoint(group) for group in sorted(set(groups))]
        result["source"] = "Historical archive; not a live MT5 P&L request"
        return result

    @router.get("/analytics/pnl")
    def pnl(filters: HistoryFilters=Depends(validated_filters),dimension: str="day",page: int=Query(1,ge=1),page_size: int=Query(100,ge=1,le=1000),descending: bool=False,sort: str=Query("label",pattern="^(label|net|lots|deals)$"),actor=Depends(archive_actor)):
        try:
            return store.report(filters.filters(),dimension,page,page_size,descending,sort)
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc

    @router.get("/analytics/overview")
    def overview(filters: HistoryFilters=Depends(validated_filters),page: int=Query(1,ge=1),page_size: int=Query(100,ge=1,le=1000),actor=Depends(archive_actor)):
        scope = filters.filters()
        report = store.report(scope,"day",1,10000)
        curves = {}
        for row in report["rows"]:
            curve = curves.setdefault(row["currency"],{"cumulative":number(0),"peak":number(0),"max_drawdown":number(0),"available":True})
            if row["net"] is None:
                curve["available"] = False
            if curve["available"]:
                curve["cumulative"] += number(row["net"])
                curve["peak"] = max(curve["peak"],curve["cumulative"])
                curve["max_drawdown"] = max(curve["max_drawdown"],curve["peak"]-curve["cumulative"])
                row["cumulative"] = str(curve["cumulative"])
                row["drawdown"] = str(curve["peak"]-curve["cumulative"])
            else:
                row["cumulative"] = row["drawdown"] = None
        report["curves"] = {c:{k:str(v) if k != "available" else v for k,v in data.items()} for c,data in curves.items()}
        report["series"] = report["rows"]
        report["rows"] = report["rows"][(page-1)*page_size:page*page_size]
        report["page"], report["page_size"] = page,page_size
        report["series_truncated"] = report["total"] > 10000
        if report["series_truncated"]:
            for curve in report["curves"].values():
                curve["available"] = False
        report["drawdown_definition"] = "Maximum peak-to-trough decrease in cumulative daily realized client net trading P&L, starting at zero. This is not account equity drawdown. Missing pre-archive equity history cannot be reconstructed."
        report["snapshots"] = store.snapshot_report(scope,page_size=1000)
        previous = dict(scope)
        days = (filters.to_date-filters.from_date).days+1
        previous["to_date"] = (filters.from_date-timedelta(days=1)).isoformat()
        previous["from_date"] = (filters.from_date-timedelta(days=days)).isoformat()
        report["previous_period"] = store.report(previous,"day",1,1)["totals"]
        report["freshness"] = store.status()
        return report

    @router.get("/analytics/exposure")
    def exposure(filters: HistoryFilters=Depends(validated_filters),page: int=Query(1,ge=1),page_size: int=Query(100,ge=1,le=1000),dimension: str=Query("symbol",pattern="^(symbol|login|account_group)$"),actor=Depends(archive_actor)):
        return store.snapshot_report(filters.filters(),True,page,page_size,dimension)

    @router.get("/analytics/accounts")
    def accounts(filters: HistoryFilters=Depends(validated_filters),page: int=Query(1,ge=1),page_size: int=Query(100,ge=1,le=1000),descending: bool=False,sort: str=Query("label",pattern="^(label|net|lots|deals)$"),actor=Depends(archive_actor)):
        result = store.report(filters.filters(),"login",page,page_size,descending,sort)
        result["snapshots"] = store.snapshot_report(filters.filters(),page=page,page_size=page_size,dimension="login",latest_only=True)
        return result

    @router.get("/analytics/trading-activity")
    def activity(filters: HistoryFilters=Depends(validated_filters),kind: str=Query("deal",pattern="^(deal|order|closed)$"),page: int=Query(1,ge=1),page_size: int=Query(100,ge=1,le=1000),descending: bool=True,actor=Depends(archive_actor)):
        return store.activity(filters.filters(),kind,page,page_size,descending)

    @router.get("/analytics/risk-events")
    def risks(filters: HistoryFilters=Depends(validated_filters),page: int=Query(1,ge=1),page_size: int=Query(100,ge=1,le=1000),severity: str="",actor=Depends(archive_actor)):
        return store.risk_report(filters.filters(),page,page_size,severity)

    @router.post("/analytics/exports")
    def create_export(req: ExportRequest,actor=Depends(archive_actor)):
        job_id = uuid.uuid4().hex
        with store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            count = con.execute("SELECT COUNT(*) FROM history_exports WHERE actor=? AND (status IN ('queued','running') OR created_at>?)",(actor,time.time()-60)).fetchone()[0]
            if count >= 3:
                raise HTTPException(429,"Wait for the current exports to finish")
            con.execute("INSERT INTO history_exports(id,status,actor,format,filters,created_at) VALUES(?,'queued',?,?,?,?)",
                        (job_id,actor,req.format,json.dumps({**req.filters(),"report":req.report,"dimension":req.dimension,"kind":req.kind}),time.time()))
        a.write_audit(actor,"history.export_queued","history",job_id,{"format":req.format,"report":req.report,**req.filters()})
        return {"id":job_id,"status":"queued"}

    @router.get("/analytics/exports/{export_id}")
    def export_status(export_id: str,actor=Depends(archive_actor)):
        with store.connect() as con:
            row = con.execute("SELECT id,status,format,created_at,completed_at,error FROM history_exports WHERE id=? AND actor=? AND created_at>=?",(export_id,actor,time.time()-86400)).fetchone()
        if not row:
            raise HTTPException(404,"Export not found")
        return dict(row)

    @router.get("/analytics/exports/{export_id}/download")
    def download(export_id: str,actor=Depends(archive_actor)):
        with store.connect() as con:
            row = con.execute("SELECT * FROM history_exports WHERE id=? AND actor=? AND created_at>=?",(export_id,actor,time.time()-86400)).fetchone()
        if not row:
            raise HTTPException(404,"Export not found")
        if row["status"] != "completed":
            raise HTTPException(409,"Export is not ready")
        fmt = row["format"]
        media = "text/csv" if fmt == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return Response(row["content"],media_type=media,headers={"Content-Disposition":f'attachment; filename="risk-analytics-{export_id}.{fmt}"'})

    a.api.include_router(router)
    return worker
