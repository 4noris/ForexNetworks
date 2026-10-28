"""Test-only source observations for browser checks. Never imported by RMS."""
import json
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from services.exposure import build_exposure, ObservedExposureHistory

request=json.load(sys.stdin)
params=request.get("params",{})
now=time.time()
tick=request.get("tick",0)
cache={"status":"connected","errors":[],"last_update":now,"groups":["Fixture A","Fixture B"],
       "group_currencies":{"Fixture A":"USD","Fixture B":"EUR"},
       "users_by_login":{100200:{"Login":100200,"Name":"Fixture One","Group":"Fixture A","Margin":100,"Margin Level %":250},
                         100201:{"Login":100201,"Name":"Fixture Two","Group":"Fixture A","Margin":50,"Margin Level %":500},
                         200300:{"Login":200300,"Name":"Fixture Three","Group":"Fixture B","Margin":50,"Margin Level %":750}},
       "positions":[{"Login":100200,"Ticket":"18446744073709551615","Symbol":"XAUUSD","Action":0,"Volume":2,"PriceOpen":100+tick*2,"PriceCurrent":110,"Profit":20+tick*2,"Swap":-1,"TimeCreate":now-7*86400},
                    {"Login":100201,"Ticket":"18446744073709551614","Symbol":"XAUUSD","Action":1,"Volume":1,"PriceOpen":120,"PriceCurrent":110,"Profit":-5,"Swap":0,"TimeCreate":now-100},
                    {"Login":200300,"Ticket":"18446744073709551613","Symbol":"XAUUSD","Action":0,"Volume":3,"PriceOpen":105,"PriceCurrent":110,"Profit":15,"Swap":-.5,"TimeCreate":now-100}],
       "tick_cache":{"XAUUSD":{"available":True,"bid":109,"ask":110,"tick_time":datetime.fromtimestamp(now,timezone.utc).isoformat()}},
       "symbol_cache":{"XAUUSD":{"Digits":2}}}
if request.get("multi_symbols"):
    cache["positions"].append({"Login":100200,"Ticket":"18446744073709551612","Symbol":"TEST_OTHER","Action":0,"Volume":1,"PriceOpen":1000,"PriceCurrent":1010,"Profit":10,"Swap":0,"TimeCreate":now-100})
rules=request.get("rules") or [{"id":1,"scope_type":"group","scope_value":"Fixture A","metric":"gross_lots","threshold":2,"warning_pct":70,"enabled":True,"escalation_level":"critical"},
                                {"id":2,"scope_type":"account","scope_value":"*","metric":"net_lots","threshold":2,"warning_pct":80,"enabled":True,"escalation_level":"critical"}]
result=build_exposure(cache,params,rules)
result["history"],result["observed_since_ms"]=ObservedExposureHistory().observe("fixture",{"source_ms":int(now*1000),"gross_lots":result["summary"]["gross_lots"],"net_lots":result["summary"]["net_lots"],"floating_pnl":result["summary"]["floating_pnl"],"comparison_floating":result["comparison_floating"]})
json.dump(result,sys.stdout)
