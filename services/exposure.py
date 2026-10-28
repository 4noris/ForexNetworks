"""Complete native-book exposure views. No SDK calls or price-derived P&L."""
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone
import math
import threading
import time


def finite(value):
    return float(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else None


def timestamp(value):
    if isinstance(value,(int,float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z","+00:00")).timestamp()
    except (ValueError,TypeError):
        return None


class Metric:
    def __init__(self):
        self.buy = self.sell = self.buy_price = self.sell_price = 0.0
        self.buy_prices_complete = self.sell_prices_complete = True
        self.money = {}
        self.money_complete = {}
        self.logins = set()
        self.positions = 0
        self.buys = self.sells = 0

    def add(self, row):
        side, lots = row["action"],row["volume"]
        price = row["price_open"]
        if side == "BUY":
            self.buys += 1
            self.buy += lots
            self.buy_prices_complete &= price is not None
            self.buy_price += lots * (price or 0)
        else:
            self.sells += 1
            self.sell += lots
            self.sell_prices_complete &= price is not None
            self.sell_price += lots * (price or 0)
        currency = row["currency"]
        self.money[currency] = self.money.get(currency,0) + (row["floating_pnl"] or 0)
        self.money_complete[currency] = self.money_complete.get(currency,True) and row["floating_pnl"] is not None and currency != "UNKNOWN"
        self.logins.add(row["login"])
        self.positions += 1

    def read(self, currency, include_prices=False):
        return {"buy_lots":round(self.buy,8),"sell_lots":round(self.sell,8),
                "net_lots":round(self.buy-self.sell,8),"gross_lots":round(self.buy+self.sell,8),
                "avg_buy":self.buy_price/self.buy if include_prices and self.buy and self.buy_prices_complete else None,
                "avg_sell":self.sell_price/self.sell if include_prices and self.sell and self.sell_prices_complete else None,
                "floating_pnl":round(self.money[currency],2) if currency in self.money and self.money_complete[currency] else None,
                "positions":self.positions,"clients":len(self.logins),"buys":self.buys,"sells":self.sells}


def aggregate(rows, key):
    result = {}
    for row in rows:
        identity = key(row)
        if identity not in result:
            result[identity]=Metric()
        result[identity].add(row)
    return result


def policy_util(metric, rules, scope_type, scope_value):
    checks = []
    # An exact policy for a metric overrides that metric's wildcard default.
    for field in ("net_lots","gross_lots"):
        exact=rules.get((scope_type,str(scope_value),field),[])
        for rule in exact or rules.get((scope_type,"*",field),[]):
            threshold = finite(rule.get("threshold"))
            if threshold is None or threshold <= 0:
                continue
            value = abs(metric.buy-metric.sell) if field=="net_lots" else metric.buy+metric.sell
            utilization = value/threshold*100
            if not math.isfinite(utilization):
                continue
            warning = finite(rule.get("warning_pct")) or 80
            checks.append({"id":rule.get("id"),"metric":field,"value":round(value,8),"threshold":threshold,
                           "utilization":round(utilization,2),"warning_pct":warning,
                           "status":"critical" if utilization>=100 else "warning" if utilization>=warning else "normal"})
    worst = max(checks,key=lambda r:({"normal":0,"warning":1,"critical":2}[r["status"]],r["utilization"]),default=None)
    return {"status":worst["status"] if worst else "unconfigured", "utilization":worst["utilization"] if worst else None,
            "limit_checks":checks}


class ObservedExposureHistory:
    """Bounded real samples: recent seconds, older minute observations, no backfill."""
    def __init__(self,max_scopes=32):
        self.lock=threading.Lock()
        self.scopes=OrderedDict()
        self.max_scopes=max_scopes

    def observe(self,key,point,range_seconds=14400,record=True):
        with self.lock:
            rows=self.scopes.pop(key,[])
            if record and (not rows or point["source_ms"]>rows[-1]["source_ms"]):
                if rows and point["source_ms"]//1000==rows[-1]["source_ms"]//1000:
                    rows[-1]=point
                else:
                    rows.append(point)
            cutoff=point["source_ms"]-86400000
            compact=[]
            for row in rows:
                if row["source_ms"] < cutoff:
                    continue
                bucket=row["source_ms"]//(1000 if point["source_ms"]-row["source_ms"]<900000 else 60000)
                if compact and compact[-1][0]==bucket:
                    compact[-1]=(bucket,row)
                else:
                    compact.append((bucket,row))
            rows=[r for _,r in compact][-2400:]
            self.scopes[key]=rows
            while len(self.scopes)>self.max_scopes:
                self.scopes.popitem(last=False)
            selected=[r for r in rows if r["source_ms"]>=point["source_ms"]-range_seconds*1000]
            if len(selected)>240:
                step=math.ceil(len(selected)/239)
                selected=selected[::step]+([selected[-1]] if selected[-1] is not selected[::step][-1] else [])
            return list(selected),rows[0]["source_ms"] if rows else None


def build_exposure(cache, params, rules=(), *, quote_now=None, quote_max_age=30):
    """O(open positions), using the immutable login index; no scan of inactive users."""
    now=time.time()
    source=finite(cache.get("last_update")) or 0
    source_age=max(0,now-source)
    if not source or cache.get("status")!="connected" or cache.get("errors"):
        return {"status":"warming","source_updated_at":source or None,"complete":False,
                "warnings":["Waiting for a verified native MT5 book"],"summary":{},"groups":[],"symbols":[]}
    selected=set(params.get("selected_groups") or [])
    excluded=set(params.get("excluded_logins") or [])
    users=cache.get("users_by_login",{})
    currencies=cache.get("group_currencies",{})
    specs=cache.get("symbol_cache",{})
    ticks=cache.get("tick_cache",{})
    quote_now=quote_now if quote_now is not None else now
    positions=[]
    missing=0
    for raw in cache.get("positions",[]):
        login=raw.get("Login")
        user=users.get(login)
        if user is None:
            missing+=1
            continue
        group=str(user.get("Group") or "")
        if group not in selected or login in excluded:
            continue
        lots=finite(raw.get("Volume"))
        action=raw.get("Action")
        if lots is None or lots<0 or action not in (0,1):
            missing+=1
            continue
        symbol=str(raw.get("Symbol") or "")
        currency=str(currencies.get(group) or user.get("Currency") or "UNKNOWN").strip().upper()
        profit,swap=finite(raw.get("Profit")),finite(raw.get("Swap"))
        opened=timestamp(raw.get("TimeCreate"))
        margin=finite(user.get("Margin"))
        positions.append({"ticket":str(raw.get("Ticket") or ""),"login":str(login),"name":str(user.get("Name") or ""),
                          "group":group,"symbol":symbol,"action":"BUY" if action==0 else "SELL","volume":lots,
                          "price_open":finite(raw.get("PriceOpen")),"price_current":finite(raw.get("PriceCurrent")),
                          "profit":profit,"swap":swap,"commission":finite(raw.get("Commission")),
                          "floating_pnl":profit+swap if profit is not None and swap is not None else None,
                          "currency":currency,"margin_level":finite(user.get("Margin Level %")) if margin and margin>0 else None,
                          "opened_at":datetime.fromtimestamp(opened,timezone.utc).isoformat() if opened and opened>0 else None,
                          "age_seconds":max(0,quote_now-opened) if opened else None})
    by_group=aggregate(positions,lambda p:p["group"])
    by_client=aggregate(positions,lambda p:p["login"])
    by_symbol=aggregate(positions,lambda p:p["symbol"])
    rule_index=defaultdict(list)
    for rule in rules:
        if rule.get("enabled"):
            rule_index[(rule.get("scope_type"),str(rule.get("scope_value")),rule.get("metric"))].append(rule)
    policies={kind:{identity:policy_util(metric,rule_index,kind,identity) for identity,metric in rows.items()}
              for kind,rows in (("group",by_group),("account",by_client),("symbol",by_symbol))}
    group_filter=params.get("group") or ""
    symbol_filter=params.get("symbol") or ""
    query=str(params.get("query") or "").strip().lower()
    exact_login=str(int(query)) if query.isdigit() and users.get(int(query)) is not None else None
    direction=params.get("direction","BOTH")
    wanted=params.get("risk_status","ALL")
    def matches(row,with_group=True):
        return ((not with_group or not group_filter or row["group"]==group_filter)
                and (not symbol_filter or row["symbol"]==symbol_filter)
                and (direction=="BOTH" or row["action"]==direction)
                and (not query or (row["login"]==exact_login if exact_login else query in str(row["login"]) or query in row["name"].lower()))
                and (wanted=="ALL" or any(policies[k][row[f]]["status"]==wanted for k,f in
                                         (("account","login"),("group","group"),("symbol","symbol")))))
    comparison=[r for r in positions if matches(r,False)]
    rows=[r for r in comparison if not group_filter or r["group"]==group_filter]
    available_currencies=sorted({r["currency"] for r in rows if r["currency"]!="UNKNOWN"})
    requested=params.get("money_currency") or ""
    currency=requested if requested in available_currencies else "USD" if "USD" in available_currencies else next(iter(available_currencies),"")
    all_metric=Metric()
    comparison_metric=Metric()
    for row in rows:all_metric.add(row)
    for row in comparison:comparison_metric.add(row)
    summary=all_metric.read(currency)
    summary.update(currency=currency,currencies=available_currencies,unfiltered_positions=len(positions),
                   unknown_currency_positions=sum(r["currency"]=="UNKNOWN" for r in rows),data_age_seconds=round(source_age,2))
    filtered_groups=aggregate(rows,lambda p:p["group"])
    filtered_clients=aggregate(rows,lambda p:p["login"])
    filtered_symbols=aggregate(rows,lambda p:p["symbol"])
    pairs=aggregate(rows,lambda p:(p["login"],p["symbol"]))
    group_symbol_metrics=aggregate(rows,lambda p:(p["group"],p["symbol"]))
    client_prices=defaultdict(list)
    group_prices=defaultdict(list)
    def price_row(symbol,metric):
        values=metric.read(currency,True)
        return {"symbol":symbol,"buy_lots":values["buy_lots"],"sell_lots":values["sell_lots"],
                "avg_buy":values["avg_buy"] if symbol else None,"avg_sell":values["avg_sell"] if symbol else None}
    for (login,symbol),metric in pairs.items():
        client_prices[login].append(price_row(symbol,metric))
    for (group,symbol),metric in group_symbol_metrics.items():
        group_prices[group].append(price_row(symbol,metric))
    def average_fields(prices):
        prices=sorted(prices,key=lambda row:row["symbol"])
        buys=[row for row in prices if row["buy_lots"]>0]
        sells=[row for row in prices if row["sell_lots"]>0]
        return {"price_breakdown":prices,"avg_buy":buys[0]["avg_buy"] if len(buys)==1 else None,
                "avg_sell":sells[0]["avg_sell"] if len(sells)==1 else None}
    client_rows=defaultdict(list)
    client_positions=defaultdict(list)
    for row in rows:client_positions[row["login"]].append(row)
    for login,metric in filtered_clients.items():
        first=client_positions[login][0]
        client_rows[first["group"]].append({"login":login,"name":first["name"],"group":first["group"],
             "currency":first["currency"],"margin_level":first["margin_level"],**metric.read(currency),**average_fields(client_prices[login]),**policies["account"][login]})
    sort_key=params.get("sort_by","gross_lots")
    descending=params.get("sort_desc",True)
    def sort_rows(values,name):
        key=name if sort_key=="name" else sort_key
        return sorted(values,key=lambda r:(str(r.get(key) or "").lower() if key==name else (r.get(key) is not None,r.get(key) or 0)),reverse=descending)
    expanded_groups=set(params.get("expanded_groups") or [])
    expanded_clients={str(login) for login in params.get("expanded_clients") or []}
    detail_limit=params.get("detail_limit",100)
    clients_limit=params.get("clients_limit",100)
    groups=[]
    remaining_clients=2000
    for group,metric in filtered_groups.items():
        item={"group":group,**metric.read(currency),**average_fields(group_prices[group]),**policies["group"][group]}
        if group in expanded_groups:
            total=len(client_rows[group])
            offset=min((params.get("client_offsets") or {}).get(group,0),max(0,((total-1)//clients_limit)*clients_limit))
            shown=min(clients_limit,remaining_clients)
            item["accounts"]=sort_rows(client_rows[group],"name")[offset:offset+shown]
            item["accounts_total"]=total
            item["accounts_offset"]=offset
            item["detail_budget_reached"]=shown==0
            remaining_clients-=len(item["accounts"])
        groups.append(item)
    symbols=[]
    stale_symbols=[]
    for symbol,metric in filtered_symbols.items():
        tick=ticks.get(symbol,{})
        stamp=timestamp(tick.get("tick_time"))
        bid,ask=finite(tick.get("bid")),finite(tick.get("ask"))
        # SDK TimeServer has second precision while quote timestamps have
        # milliseconds. Permit its sub-second rounding difference, not future data.
        stale=not tick.get("available") or bid is None or ask is None or stamp is None or not -1<=quote_now-stamp<=quote_max_age
        if stale:stale_symbols.append(symbol)
        symbols.append({"symbol":symbol,**metric.read(currency,True),**policies["symbol"][symbol],
                        "digits":specs.get(symbol,{}).get("Digits",5),"bid":bid,"ask":ask,
                        "quote_time":tick.get("tick_time"),"quote_stale":stale})
    detail={}
    for login in expanded_clients:
        if login not in client_positions:
            continue
        total=len(client_positions[login])
        offset=min((params.get("detail_offsets") or {}).get(str(login),0),max(0,((total-1)//detail_limit)*detail_limit))
        detail[str(login)]={"total":total,"offset":offset,"limit":detail_limit,
                            "positions":sorted(client_positions[login],key=lambda p:(-p["volume"],p["ticket"]))[offset:offset+detail_limit]}
    alerts=[]
    for kind,values in policies.items():
        visible={r["group"] if kind=="group" else r["symbol"] if kind=="symbol" else r["login"] for r in rows}
        for identity,item in values.items():
            if identity in visible and item["status"] in {"warning","critical"}:
                alerts.append({"scope_type":kind,"scope_value":str(identity),**item})
    alerts.sort(key=lambda a:(a["status"]!="critical",-a["utilization"]))
    # Additional views also come from the full filtered book, before display caps.
    concentration=[]
    paired_rows=defaultdict(list)
    for row in rows:paired_rows[(row["login"],row["symbol"])].append(row)
    for (login,symbol),metric in pairs.items():
        first=client_positions[login][0]
        account_metric=filtered_clients[login]
        total=account_metric.buy+account_metric.sell
        opens=[r["opened_at"] for r in paired_rows[(login,symbol)] if r["opened_at"]]
        concentration.append({"login":login,"symbol":symbol,"group":first["group"],"name":first["name"],
                              **metric.read(currency,True),"first_open":min(opens) if opens else None,"last_open":max(opens) if opens else None,"concentration_pct":round((metric.buy+metric.sell)/total*100,2) if total else 0})
    concentration.sort(key=lambda a:(-a["concentration_pct"],-a["gross_lots"]))
    aged=[r for r in rows if r["age_seconds"] is not None and r["age_seconds"]>=5*86400]
    overnight=[r for r in rows if r["age_seconds"] is not None and r["age_seconds"]>=86400]
    aged_metric,overnight_metric=Metric(),Metric()
    for row in aged:aged_metric.add(row)
    for row in overnight:overnight_metric.add(row)
    warnings=[]
    if missing:warnings.append(f"{missing} position records are awaiting verified account identity or volume; coverage is partial.")
    if source_age>10:warnings.append("MT5 updates are delayed; these are the last verified source observations.")
    located=None
    if query.isdigit():
        account=users.get(int(query))
        if account and account.get("Group") in selected and int(query) not in excluded:
            located={"login":query,"name":str(account.get("Name") or ""),"group":account["Group"],
                     "visible":str(int(query)) in filtered_clients}
            located["login"]=str(int(query))
    elif query and len(filtered_clients)==1:
        login=next(iter(filtered_clients))
        row=client_positions[login][0]
        located={"login":login,"name":row["name"],"group":row["group"],"visible":True}
    return {"status":"connected" if source_age<=10 else "delayed","complete":not missing,"source_updated_at":source,
            "generated_at":datetime.now(timezone.utc).isoformat(),"summary":summary,"selected_groups":sorted(selected),
            "available_groups":sorted(by_group),"available_symbols":sorted(by_symbol),"groups":sort_rows(groups,"group"),"located_account":located,
            "symbols":sorted(symbols,key=lambda s:-s["gross_lots"]),"details":detail,
            "top_clients":sorted([r for values in client_rows.values() for r in values],
                                 key=lambda c:-abs(c["net_lots"]) if params.get("client_metric","net")=="net" else -c["gross_lots"])[:10],
            "alerts":alerts[:100],"alerts_total":len(alerts),"stale_symbols":sorted(stale_symbols),
            "limits":[{key:r.get(key) for key in ("id","scope_type","scope_value","metric","threshold","warning_pct","enabled","escalation_level")} for r in rules if r.get("metric") in {"gross_lots","net_lots"}],
            "concentration":concentration[:200],"concentration_total":len(concentration),
            "aged":{"count":len(aged),"floating_pnl":aged_metric.read(currency)["floating_pnl"],
                    "positions":sorted(aged,key=lambda r:-r["age_seconds"])[:100],
                    "overnight_count":len(overnight),"overnight_floating":overnight_metric.read(currency)["floating_pnl"]},
            "comparison_floating":comparison_metric.read(currency)["floating_pnl"],"warnings":warnings}
