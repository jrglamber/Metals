"""Read-only smoke: verify the *served* LIVE Metals top-card contract.

Unlike the old test, checks /dashboard/top (actual card data), not just the
broker API. No credentials, OANDA order methods or strategy state changes.
"""
import json
import math
import time
import urllib.request

BASE="https://metals-production-bb81.up.railway.app"

def get(path, html=False):
    req=urllib.request.Request(
        BASE+path,headers={"Accept":"text/html" if html else "application/json",
                           "User-Agent":"MetalsCombinedTopAcceptance/1.0"},method="GET")
    with urllib.request.urlopen(req,timeout=28) as r:
        if r.status!=200: raise AssertionError(f"{path}: HTTP {r.status}")
        data=r.read(1000000).decode("utf-8","replace")
    return data if html else json.loads(data)

def check():
    html=get("/dashboard",html=True)
    for required in ("Broker P&L · LIVE XAU + XAG SHORT",
                     "High-Water · LIVE XAU + XAG SHORT",
                     "Giveback · LIVE XAU + XAG SHORT",
                     "From combined observed peak"):
        if required not in html:
            raise AssertionError("Combined dashboard renderer missing "+required)
    if "pep-combined-live-metals-pnl-v2" in html:
        raise AssertionError("Old racing DOM patch still injected")

    top=get("/dashboard/top")
    s=top.get("strategy") or {}
    xau=top.get("xau_only_strategy") or {}
    account=top.get("account") or {}
    if top.get("status")!="ok" or s.get("combined_reporting_status")!="BROKER_VERIFIED":
        raise AssertionError("Combined top data unavailable: "+str(s.get("combined_reporting_reason") or top.get("error")))
    if top.get("combined_metals_source")!="/api/live-metals-open-pnl":
        raise AssertionError("Top still uses legacy XAU-only broker data")
    if s.get("combined_reporting_scope")!="LIVE_XAU_LONG_SHORT_PLUS_LIVE_XAG_SHORT":
        raise AssertionError("Live Metals scope mismatched")
    if s.get("open_trades") != s.get("xau_count")+s.get("xag_short_count"):
        raise AssertionError("Top open trades omit XAG")
    if any(s.get(k) is not None for k in ("basket_r","high_water_r","giveback_r")):
        raise AssertionError("XAU-only R relabelled as combined R")
    if not math.isclose(float(s["giveback_gbp"]),
                        max(0.0,float(s["high_water_gbp"])-float(s["headline_pnl"])),
                        abs_tol=0.03):
        raise AssertionError("Combined top high-water/giveback does not reconcile")
    if not math.isclose(float(account["nav"]),
                        float(account["balance"])+float(account["unrealized_pl"]),
                        abs_tol=0.06):
        raise AssertionError("Account NAV corrupted by combined giveback")
    if s["realized_pnl"] != xau["realized_pnl"]:
        raise AssertionError("XAU historical realised unexpectedly changed")
    if xau.get("high_water_gbp") is None:
        raise AssertionError("XAU protection HWM lost")
    # The source API and top endpoint are separate broker snapshots; only
    # comparable counts are asserted across separate requests.
    api=get("/api/live-metals-open-pnl")
    if not api.get("ok") or api.get("high_water_status")!="OBSERVED_COMBINED_LIVE":
        raise AssertionError("Combined producer unavailable")
    if api.get("open_count") != s["open_trades"]:
        raise AssertionError("Top and combined API position counts differ")
    print("PASS: Metals top and API combined",s["open_trades"],
          "trades, XAU",s["xau_count"],"XAG Short",s["xag_short_count"],
          "open P&L",round(float(s["headline_pnl"]),2),
          "HWM",round(float(s["high_water_gbp"]),2),
          "giveback",round(float(s["giveback_gbp"]),2),
          "NAV preserved",round(float(account["nav"]),2),
          "XAU realised/protection separate")

if __name__=="__main__":
    last=None
    for attempt in range(8):
        try:
            check()
            break
        except Exception as exc:
            last=f"{type(exc).__name__}: {exc}"
            print("attempt",attempt+1,"combined top not verified:",last)
            time.sleep(5)
    else:
        raise SystemExit("FAIL: "+str(last))
