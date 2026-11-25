"""Read-only smoke test against the ACTUAL production Metals browser and API.

Never sends broker writes. Avoids printing position IDs/prices.
"""
import json
import os
import time
import urllib.request

ORIGIN = os.environ.get("METALS_LIVE_DASHBOARD_ORIGIN","https://metals-production-bb81.up.railway.app").rstrip("/")


def get(path):
    request=urllib.request.Request(ORIGIN+path,headers={"User-Agent":"ProjectExitPlan-ReadOnlySmoke/1.0","Accept":"application/json, text/html"})
    with urllib.request.urlopen(request,timeout=20) as response:
        if response.status != 200:
            raise AssertionError(f"{path}: HTTP {response.status}")
        return response.read(800000).decode("utf-8",errors="replace")


def validate():
    raw=get("/api/live-metals-open-positions")
    data=json.loads(raw)
    if data.get("scope") != "LIVE_XAU_LONG_SHORT_PLUS_LIVE_XAG_SHORT":
        raise AssertionError("API still has an XAU-only or unknown scope")
    if not (data.get("xau_ok") and data.get("xag_ok")):
        raise AssertionError("Live XAU or XAG broker data unavailable; cannot prove combined total")
    xs=data.get("trades") or []
    xau=sum(t.get("lane")=="XAU_LIVE" for t in xs)
    xag=sum(t.get("lane")=="XAG_SHORT_LIVE" for t in xs)
    if xau != data.get("xau_count") or xag != data.get("xag_count"):
        raise AssertionError("API counts do not match individual rows")
    if data.get("count") != len(xs) or data["count"] != xau+xag:
        raise AssertionError("Combined count incorrect")
    if not data.get("practice_excluded"):
        raise AssertionError("Practice positions are not explicitly excluded")
    if not data.get("xag_broker_link_reconciled"):
        raise AssertionError("Live XAG broker/database ownership mismatch")
    if any(t.get("tracking")!="TRACKED" for t in xs if t.get("lane")=="XAG_SHORT_LIVE"):
        raise AssertionError("One or more live XAG trades not linked to live ledger")
    # Verify the actual production response includes the per-trade manager
    # evidence; avoid printing broker IDs, prices, or individual returns.
    xag_rows=[t for t in xs if t.get("lane")=="XAG_SHORT_LIVE"]
    for row in xag_rows:
        for key in ("manager_mfe_r","manager_mae_r","current_r_approx",
                    "mfe_giveback_r_approx","manager_review_candles",
                    "manager_review_status","manager_last_decision"):
            if key not in row:
                raise AssertionError("XAG manager column missing: "+key)
    mature=sum(bool(t.get("manager_review_candles") is not None
                    and t["manager_review_candles"]>=48) for t in xag_rows)
    current=sum(bool(t.get("manager_review_candles") is not None
                     and t["manager_review_candles"]>=48 and
                     t.get("manager_review_status")=="CURRENT") for t in xag_rows)
    if (mature != data.get("xag_mature_manager_trade_count") or
        current != data.get("xag_mature_manager_current_count")):
        raise AssertionError("XAG manager aggregate counts disagree with individual positions")
    xau_rows=[t for t in xs if t.get("lane")=="XAU_LIVE"]
    for row in xau_rows:
        for key in ("manager_mfe_r","manager_mae_r","current_r_approx",
                    "mfe_giveback_r_approx","manager_review_candles",
                    "manager_review_status","manager_last_decision"):
            if key not in row:
                raise AssertionError("XAU manager column missing: "+key)
    if data.get("xau_broker_link_reconciled") is not True:
        raise AssertionError("XAU broker open long/short population not fully ledger linked")
    print("LIVE_XAU_MFE_REVIEW_SUMMARY "+json.dumps({
        "broker_open_xau":len(xau_rows),
        "mfe_recorded_count":sum(t.get("manager_mfe_r") is not None for t in xau_rows),
        "mae_recorded_count":sum(t.get("manager_mae_r") is not None for t in xau_rows),
        "manager_reviewed_count":sum(t.get("manager_review_candles") is not None for t in xau_rows)}))

    recent=json.loads(get("/api/live-metals-recently-closed?limit=50"))
    if not recent.get("ok") or recent.get("scope")!="LIVE_XAU_LONG_SHORT_AND_XAG_SHORT":
        raise AssertionError("Combined recent-closed LIVE Metals API unavailable")
    closed=recent.get("rows") or []
    if recent.get("count")!=len(closed):
        raise AssertionError("Combined recent closed count mismatch")
    if any(row.get("asset") not in ("XAUUSD","XAGUSD") or
           not row.get("broker_trade_id") or not row.get("closed_at_utc") for row in closed):
        raise AssertionError("Recent-closed feed contains unverified or invalid row")
    if not recent.get("practice_excluded"):
        raise AssertionError("Recent closed live feed does not exclude demo")
    print("LIVE_CLOSED_METALS_COMBINED "+json.dumps({
        "recent_rows":len(closed),
        "xau_rows":recent.get("xau_count"),
        "xag_rows":recent.get("xag_count"),
        "trade_pnl_net_cash_proven":not recent.get("trade_pl_not_net_broker_cash")}))

    for asset,path in (("XAU","/api/xau-nav-budget"),
                       ("XAG","/api/xag-nav-budget")):
        budget=json.loads(get(path))
        if budget.get("ok") is True:
            cap,used=budget.get("cap_gbp"),budget.get("risk_used_gbp")
            if not isinstance(cap,(int,float)) or not isinstance(used,(int,float)) or cap<=0 or used<0:
                raise AssertionError(asset+" broker risk budget invalid")
        elif not budget.get("new_entries_fail_closed"):
            raise AssertionError(asset+" budget unverified but not fail closed")
        print("LIVE_METALS_BUDGET_TILE_SOURCE "+json.dumps({
            "asset":asset,"broker_verified":budget.get("ok") is True,
            "fail_closed":budget.get("new_entries_fail_closed") is True}))

    print("LIVE_XAG_MFE_REVIEW_SUMMARY "+json.dumps({
        "broker_open_xag":xag,"mature_48_completed_candles":mature,
        "mature_reviewed_on_latest_xag_signal":current,
        "mature_stale_or_missing":mature-current,
        "last_xag_signal_observed":data.get("xag_latest_signal_id") is not None,
        "mfe_recorded_count":sum(t.get("manager_mfe_r") is not None for t in xag_rows),
        "mae_recorded_count":sum(t.get("manager_mae_r") is not None for t in xag_rows),
        "giveback_indicative_count":sum(t.get("mfe_giveback_r_approx") is not None
                                        for t in xag_rows)
    }))
    page=get("/dashboard")
    marker="fetch('/api/live-metals-open-positions'"
    if marker not in page:
        raise AssertionError("ACTUAL dashboard HTML does not contain the combined position loader")
    if "pep-standard-metals-layout-script" not in page:
        raise AssertionError("Actual native dashboard script absent")
    if "pep-metals-risk-budget-script" not in page:
        raise AssertionError("ACTUAL HTML missing XAU/XAG NAV risk budget tile script")
    if "/api/xau-nav-budget" not in page or "/api/xag-nav-budget" not in page:
        raise AssertionError("Live risk tile does not reference both actual broker NAV risk gates")
    if "LIVE XAG SHORT" not in page:
        raise AssertionError("XAG live label absent from rendered dashboard")
    print(f"PASS: actual HTML uses combined LIVE loader; XAU={xau}, XAG_SHORT={xag}, combined={len(xs)}; XAG ownership reconciled; practice excluded")


def main():
    problems=[]
    for n in range(20):
        try:
            validate()
            return
        except Exception as exc:
            problems.append(f"{type(exc).__name__}: {exc}")
            print(f"Production route not verified yet (attempt {n+1}/20): {problems[-1]}")
            time.sleep(8)
    raise SystemExit("Live UI smoke failed: "+problems[-1])


if __name__=="__main__":
    main()
