"""Read-only production audit of XAG Short stop-risk coverage."""
import json
import urllib.request

BASE="https://metals-production-bb81.up.railway.app"

def get(path):
    request=urllib.request.Request(BASE+path,method="GET",
        headers={"Accept":"application/json","User-Agent":"PEP-Metals-ReadOnly-Risk-Audit"})
    with urllib.request.urlopen(request,timeout=30) as response:
        if response.status!=200: raise AssertionError(path+": HTTP "+str(response.status))
        return json.loads(response.read().decode())

risk=get("/api/portfolio-risk")
positions=get("/api/live-metals-open-positions")
pnl=get("/api/live-metals-open-pnl")
print("RISK ok",risk.get("ok"),"scope",risk.get("scope"),
      "open",risk.get("open_trade_count"),"quantified",risk.get("quantified_trade_count"),
      "unquantified",risk.get("unquantified_trade_count"),
      "no_stop",risk.get("without_stop_trade_count"),
      "no_conversion",risk.get("conversion_missing_trade_count"),
      "error",str(risk.get("error") or "")[:160])
by=risk.get("by_instrument") or {}
for instrument in ("XAU_USD","XAG_USD"):
    row=by.get(instrument) or {}
    print(instrument,{k:row.get(k) for k in ("open_trade_count","short_trade_count",
        "long_trade_count","quantified_trade_count","without_stop_trade_count",
        "conversion_missing_trade_count","capital_at_risk_gbp",
        "locked_profit_floor_gbp")})
print("BROKER_POSITIONS ok",positions.get("ok"),
      "count",positions.get("count"),"xau",positions.get("xau_count"),
      "xag_short",positions.get("xag_short_count"),
      "xag_links_ok",positions.get("xag_broker_link_reconciled"))
print("COMBINED_PNL ok",pnl.get("ok"),"open_count",pnl.get("open_count"))

assert positions.get("ok") is True and positions.get("xag_broker_link_reconciled") is True
assert risk.get("ok") is True, "Risk producer did not provide a broker-verified result"
assert risk.get("scope")=="metals"
assert int(risk["open_trade_count"])==int(positions["count"]), "Risk scope still omits live XAG trades"
assert int((by.get("XAU_USD") or {}).get("open_trade_count",0))==int(positions["xau_count"])
assert int((by.get("XAG_USD") or {}).get("open_trade_count",0))==int(positions["xag_short_count"])
assert int((by.get("XAG_USD") or {}).get("short_trade_count",0))==int(positions["xag_short_count"])
assert int(risk["quantified_trade_count"]) + int(risk["unquantified_trade_count"]) == int(risk["open_trade_count"])
ids={str(x.get("broker_trade_id")) for x in positions.get("trades",[])}
risk_ids=set(map(str,risk.get("broker_trade_ids") or []))
assert ids==risk_ids, "Risk feed does not cover exact live broker trade IDs"
print("PASS: risk reader includes exact XAU + XAG Short broker position set")
if int(risk["unquantified_trade_count"]) != 0:
    raise SystemExit("INCOMPLETE: not all broker stops currently measurable, Hub must continue withholding complete floor")
print("PASS: all XAU and XAG Short broker stop prices quantified")
