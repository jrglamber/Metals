"""Production read-only XAG realised reporting contract and no double count."""
import json
import time
from urllib.request import Request,urlopen

BASE="https://metals-production-bb81.up.railway.app"

def get(path):
    with urlopen(Request(BASE+path,method="GET",
                         headers={"Accept":"application/json",
                                  "User-Agent":"PEP-XAG-Realised-ReadOnly-Acceptance"}),timeout=75) as r:
        if r.status!=200:raise AssertionError(path+": HTTP "+str(r.status))
        return json.loads(r.read().decode("utf-8","replace"))

def check():
    audit=get("/api/metals-xag-transaction-audit")
    lane=get("/api/live-metals-lane-accounting")
    top=get("/dashboard/top")
    if not (audit.get("ok") and audit.get("net_realised_gbp_verified") and
            audit.get("transaction_history_complete")):
        raise AssertionError("Independent XAG net GBP ledger not reconciled")
    if not lane.get("xag_short_net_gbp_verified"):
        raise AssertionError("Metals lane API not exposing broker-verified XAG cash")
    if lane.get("reconciled") is True:
        raise AssertionError("Unproven XAU split falsely marked complete")
    proof=lane.get("xag_short_verified_realised") or {}
    if not proof.get("ok") or proof.get("scope")!="XAG_SHORT_BROKER_NET_CASH_GBP":
        raise AssertionError("XAG realised proof incomplete")
    if top.get("status")!="ok":
        raise AssertionError("Top dashboard unavailable")
    s=top.get("strategy") or {}
    xau=top.get("xau_only_strategy") or {}
    a=top.get("accounting") or {}
    ax=top.get("xau_only_accounting") or {}
    if s.get("xag_realised_verified") is not True:
        raise AssertionError("Metals top still displays XAU-only realised")
    for top_field,source_field in (
            ("realized_pnl","realised_all_time_gbp"),
            ("week_pnl","realised_week_gbp"),
            ("month_pnl","realised_month_gbp")):
        joined=s if top_field=="realized_pnl" else a
        original=xau if top_field=="realized_pnl" else ax
        if abs(float(joined[top_field])-float(original[top_field])
               -float(proof[source_field]))>0.02:
            raise AssertionError("XAG realised incorrectly added to "+top_field)
    if s.get("realised_scope")!="XAU_LEGACY_PLUS_XAG_SHORT_NET_BROKER_VERIFIED":
        raise AssertionError("Historical realised scope not labelled accurately")
    if not (s.get("high_water_gbp") is not None and s.get("open_trades") is not None):
        raise AssertionError("Broker-proven open scope corrupted")
    print("PASS: XAG Short broker GBP cash reconciled and added once to Metals top; "
          "XAU-only source preserved; "
          "XAG alltime GBP "+str(proof["realised_all_time_gbp"])+"; "
          "XAG week GBP "+str(proof["realised_week_gbp"])+"; "
          "combined month GBP "+str(a["month_pnl"]))

if __name__=="__main__":
    last=""
    for attempt in range(23):
        try:
            check()
            break
        except Exception as e:
            last=f"{type(e).__name__}: {e}"
            print("Attempt",attempt+1,"-",last)
            time.sleep(6)
    else:
        raise SystemExit("FAIL: "+last)
