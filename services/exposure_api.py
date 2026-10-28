"""Validated exposure reads over existing immutable native cache generations."""
from typing import Annotated
from pydantic import BaseModel, Field
from .exposure import ObservedExposureHistory, build_exposure


class ExposureRequest(BaseModel):
    selected_groups: list[str] = Field(default_factory=list,max_length=2000)
    excluded_logins: list[int] = Field(default_factory=list,max_length=10000)
    group: str = Field(default="",max_length=160)
    symbol: str = Field(default="",max_length=128)
    query: str = Field(default="",max_length=100)
    direction: str = Field(default="BOTH",pattern="^(BOTH|BUY|SELL)$")
    risk_status: str = Field(default="ALL",pattern="^(ALL|normal|warning|critical|unconfigured)$")
    range: str = Field(default="4H",pattern="^(15M|1H|4H|24H)$")
    money_currency: str = Field(default="",max_length=32)
    sort_by: str = Field(default="gross_lots",pattern="^(name|buy_lots|sell_lots|net_lots|gross_lots|floating_pnl|clients|positions|utilization|margin_level)$")
    sort_desc: bool = True
    expanded_groups: list[str] = Field(default_factory=list,max_length=2000)
    expanded_clients: list[int] = Field(default_factory=list,max_length=200)
    detail_limit: int = Field(default=100,ge=1,le=1000)
    clients_limit: int = Field(default=100,ge=1,le=1000)
    detail_offsets: dict[str,Annotated[int,Field(ge=0,le=10000000)]] = Field(default_factory=dict,max_length=200)
    client_offsets: dict[str,Annotated[int,Field(ge=0,le=10000000)]] = Field(default_factory=dict,max_length=2000)
    client_metric: str = Field(default="net",pattern="^(net|gross)$")


HISTORY=ObservedExposureHistory()


def read_exposure(app,req,request):
    import mt5_connection
    try:
        manager=app.get_configured_manager()
    except Exception:
        return {"status":"warming","complete":False,"source_updated_at":None,"summary":{},"groups":[],"symbols":[],
                "warnings":["The configured MT5 source is reconnecting"]}
    with manager._cache_generation() as cache:
        params=req.model_dump()
        catalog=set(cache.get("groups",[]))
        params["selected_groups"]=list(dict.fromkeys(g for g in req.selected_groups if g in catalog))
        if not params["selected_groups"]:
            return {"status":"warming","complete":False,"summary":{},"groups":[],"symbols":[],
                    "warnings":["Select at least one available MT5 group"],"source_updated_at":None}
        with app._conn() as con:
            rules=[dict(row) for row in con.execute("SELECT * FROM exposure_limits WHERE metric IN ('net_lots','gross_lots') ORDER BY id DESC")]
        quote_clock=mt5_connection.quote_reference_time(cache.get("server_time_msc"),cache.get("server_time_checked_at"))
        result=build_exposure(cache,params,rules,quote_now=quote_clock.timestamp(),
                              quote_max_age=mt5_connection.TICK_LIVE_MAX_AGE_SECONDS)
        source_id=(manager.server,manager.manager_login)
    if not result.get("source_updated_at") or not result.get("summary"):
        return result
    user=getattr(request.state,"user",{}) or {}
    # Display expansion, sort and chart range do not create separate histories.
    scope=(user.get("id"),source_id,tuple(sorted(params["selected_groups"])),tuple(sorted(req.excluded_logins)),
           req.group,req.symbol,req.query.strip().lower(),req.direction,req.risk_status,result["summary"]["currency"])
    summary=result["summary"]
    point={"source_ms":int(result["source_updated_at"]*1000),"gross_lots":summary["gross_lots"],
           "net_lots":summary["net_lots"],"floating_pnl":summary["floating_pnl"],
           "comparison_floating":result["comparison_floating"]}
    result["history"],result["observed_since_ms"]=HISTORY.observe(scope,point,
        {"15M":900,"1H":3600,"4H":14400,"24H":86400}[req.range],
        record=result["status"]=="connected" and result["complete"])
    result["history_method"]="Actual source observations collected while this scope is viewed; no simulated or reconstructed history."
    return result
