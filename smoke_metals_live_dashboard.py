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
    page=get("/dashboard")
    marker="fetch('/api/live-metals-open-positions'"
    if marker not in page:
        raise AssertionError("ACTUAL dashboard HTML does not contain the combined position loader")
    if "pep-standard-metals-layout-script" not in page:
        raise AssertionError("Actual native dashboard script absent")
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
