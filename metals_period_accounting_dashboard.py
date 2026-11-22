"""Read-only live Metals period accounting dashboard.

XAU: existing broker-synced owned-XAU ledger since its recorded accounting epoch.
XAG: independently audited, transaction-level broker net GBP cash (SHORT only).
Never substitutes account-wide P/L or unverified figures for strategy cash.
No order execution or trading configuration is touched.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math

from metals_live_analysis_export import (
    _dt, _num, _week, _month, _limited_rows,
)

VERSION = "live_metals_period_accounting_v1_20261122"
SCOPE = "LIVE_XAU_LONG_SHORT_PLUS_LIVE_XAG_SHORT"


def _last_months(now, count):
    local = now.astimezone(__import__("zoneinfo").ZoneInfo("Europe/London"))
    y, m = local.year, local.month
    periods = []
    for _ in range(count):
        periods.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y -= 1
            m = 12
    return periods


def _period_end(key, granularity):
    start = datetime.fromisoformat(key + ("-01" if granularity == "monthly" else "")).replace(tzinfo=timezone.utc)
    if granularity == "weekly":
        return start + timedelta(days=7)
    y, m = start.year, start.month
    return start.replace(year=y + (m == 12), month=1 if m == 12 else m + 1)


def _money(value):
    if value is None:
        return None
    x = float(value)
    return round(x, 2) if math.isfinite(x) else None


def _series(xau, xag, xau_ok, xag_ok, epoch, now, kind):
    period_key = _week if kind == "weekly" else _month
    buckets = defaultdict(lambda: {"xau": 0.0, "xag": 0.0, "xau_events": 0, "xag_events": 0})
    for item in xau:
        key = period_key(_dt(item["transaction_time_utc"]))
        buckets[key]["xau"] += item["net_realized_gbp"]
        buckets[key]["xau_events"] += 1
    for item in xag:
        key = period_key(_dt(item["transaction_time_utc"]))
        buckets[key]["xag"] += item["net_realized_gbp"]
        buckets[key]["xag_events"] += 1
    if kind == "weekly":
        p = _week(now)
        keys = [(datetime.fromisoformat(p) - timedelta(weeks=i)).date().isoformat() for i in range(10)]
    else:
        keys = _last_months(now, 8)
    results = []
    for key in keys:
        b = buckets.get(key) or {"xau": 0.0, "xag": 0.0, "xau_events": 0, "xag_events": 0}
        end = _period_end(key, kind)
        start = _dt(key + ("-01T00:00:00+00:00" if kind == "monthly" else "T00:00:00+00:00"))
        before_epoch = epoch is None or end <= epoch
        partial = epoch is not None and start < epoch < end
        xau_valid = bool(xau_ok and not before_epoch)
        xag_valid = bool(xag_ok)
        combined_valid = xau_valid and xag_valid
        results.append({
            "period": key,
            "xau_gbp": _money(b["xau"]) if xau_valid else None,
            "xag_gbp": _money(b["xag"]) if xag_valid else None,
            "combined_gbp": _money(b["xau"] + b["xag"]) if combined_valid else None,
            "xau_events": b["xau_events"] if xau_valid else None,
            "xag_events": b["xag_events"] if xag_valid else None,
            "status": (
                "PARTIAL_XAU_EPOCH" if combined_valid and partial else
                "VERIFIED_SINCE_XAU_EPOCH" if combined_valid else
                "XAU_OUTSIDE_ACCOUNTING_EPOCH" if before_epoch else
                "INCOMPLETE_DO_NOT_USE_COMBINED"
            ),
        })
    return results


def snapshot(core, xag_reader=None, now=None):
    """Produce a fail-closed period view using the live export's source definitions."""
    now = now or datetime.now(timezone.utc)
    if xag_reader is None:
        from metals_xag_transaction_audit import export_verified_cash_events
        xag_reader = export_verified_cash_events

    xau, invalid = [], []
    epoch = cursor = None
    xau_error = None
    try:
        with core.get_conn() as conn:
            rows = _limited_rows(
                conn, "metals_xau_live_broker_transactions",
                "transaction_id,transaction_time,transaction_type,instrument,pl_gbp,financing_gbp,net_realized_gbp,synced_at_utc",
                "transaction_time ASC,transaction_id ASC",
            )
            state_rows = [dict(r) for r in conn.execute(
                "SELECT key,value,updated_at_utc FROM metals_xau_live_runtime_state "
                "WHERE key IN ('accounting_epoch_utc','broker_transaction_cursor')"
            ).fetchall()]
        states = {r["key"]: r for r in state_rows}
        epoch = _dt((states.get("accounting_epoch_utc") or {}).get("value"))
        cursor = _dt((states.get("broker_transaction_cursor") or {}).get("updated_at_utc"))
        seen = set()
        for r in rows:
            ident = str(r.get("transaction_id") or "").strip()
            date = _dt(r.get("transaction_time"))
            pl, fin, net = (_num(r.get(k)) for k in ("pl_gbp", "financing_gbp", "net_realized_gbp"))
            if (not ident or ident in seen or date is None or
                None in (pl, fin, net) or abs(pl + fin - net) > 0.02 or
                str(r.get("instrument")) != "XAU_USD"):
                invalid.append("invalid_xau_row")
                continue
            seen.add(ident)
            xau.append({"transaction_time_utc": date.isoformat(), "net_realized_gbp": net})
    except Exception as exc:
        xau_error = type(exc).__name__
        xau = []
        invalid.append("unavailable")

    age = (now - cursor).total_seconds() if cursor else None
    cursor_fresh = bool(age is not None and 0 <= age <= 900)
    xau_ok = bool(not invalid and epoch is not None and cursor_fresh and xau_error is None)

    xag, xag_error, xag_ok = [], None, False
    try:
        verified = xag_reader(core)
        proof = verified["proof"]
        if proof.get("ok") is not True or proof.get("scope") != "XAG_SHORT_BROKER_NET_CASH_GBP":
            raise ValueError("XAG broker cash proof missing or wrong scope")
        for r in verified["events"]:
            date = _dt(r.get("transaction_time_utc"))
            net = _num(r.get("net_realized_gbp"))
            if date is None or net is None:
                raise ValueError("Unparseable XAG verified event")
            xag.append({"transaction_time_utc": date.isoformat(), "net_realized_gbp": net})
        expected = _num(proof.get("realised_all_time_gbp"))
        if expected is None or abs(sum(r["net_realized_gbp"] for r in xag) - expected) > 0.02:
            raise ValueError("XAG events do not reconcile to audited total")
        xag_ok = True
    except Exception as exc:
        xag_error = type(exc).__name__
        xag = []

    weekly = _series(xau, xag, xau_ok, xag_ok, epoch, now, "weekly")
    monthly = _series(xau, xag, xau_ok, xag_ok, epoch, now, "monthly")
    return {
        "ok": bool(xau_ok and xag_ok),
        "version": VERSION, "scope": SCOPE, "currency": "GBP",
        "timezone": "Europe/London", "as_of_utc": now.isoformat(),
        "read_only": True, "execution_authority": False,
        "xau": {
            "verified_since_epoch": xau_ok,
            "accounting_epoch_utc": epoch.isoformat() if epoch else None,
            "broker_cursor_fresh": cursor_fresh,
            "broker_cursor_age_seconds": round(age, 1) if age is not None else None,
            "event_count": len(xau) if xau_ok else None,
            "error_type": xau_error or ("InvalidLedgerRows" if invalid else None),
        },
        "xag": {"broker_cash_verified": xag_ok, "event_count": len(xag) if xag_ok else None,
                "error_type": xag_error},
        "weekly": weekly, "monthly": monthly,
        "note": "Net realised GBP only; includes attributed financing and fees; excludes open P/L. "
                "XAU covers owned long+short trades since its accounting epoch. XAG covers live SHORT only. "
                "Amounts are blank, never zero, when verification is missing. "
                "The XAU epoch's first partial week/month is explicitly labelled.",
    }


DASHBOARD_SNIPPET = r'''
<style id="pep-period-financials-style">
 #pep-metals-period-accounting{margin:22px auto;max-width:1280px;padding:20px;border:1px solid #304559;border-radius:16px;background:linear-gradient(140deg,#142332,#0b1622);color:#e8f1fa;box-shadow:0 8px 26px rgba(0,0,0,.2)}
 #pep-metals-period-accounting *{box-sizing:border-box}
 #pep-metals-period-accounting .pf-top{display:flex;gap:14px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap}
 #pep-metals-period-accounting .pf-eyebrow{font-weight:800;font-size:11px;letter-spacing:.14em;color:#94c9ef;text-transform:uppercase}
 #pep-metals-period-accounting h2{margin:5px 0 5px;font-size:clamp(20px,3vw,28px);line-height:1.15}
 #pep-metals-period-accounting p{margin:5px 0 10px;font-size:12px;color:#aec0d2;line-height:1.5}
 #pep-metals-period-accounting .pf-pill{padding:6px 10px;border-radius:30px;background:#2b3d4a;color:#e2edf6;font-size:11px;font-weight:700}
 #pep-metals-period-accounting .pf-pill.good{background:#173e34;color:#8ce1b5}
 #pep-metals-period-accounting .pf-pill.bad{background:#55372c;color:#ffd2a7}
 #pep-metals-period-accounting .pf-sub{font-size:12px;font-weight:700;letter-spacing:.04em;margin:16px 0 9px;color:#e3eaf5}
 #pep-metals-period-accounting .pf-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
 #pep-metals-period-accounting .pf-card{border:1px solid #2d4357;background:#172a3b;border-radius:12px;padding:13px;min-width:0}
 #pep-metals-period-accounting .pf-card.combined{background:linear-gradient(125deg,#163e50,#14304c);border-color:#37728a}
 #pep-metals-period-accounting .pf-label{font-size:12px;color:#b9cddd}
 #pep-metals-period-accounting .pf-value{font-size:clamp(17px,2.3vw,25px);font-weight:800;letter-spacing:-.025em;margin-top:6px;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
 #pep-metals-period-accounting .pf-pos{color:#9be6bf}
 #pep-metals-period-accounting .pf-neg{color:#ffa9ac}
 #pep-metals-period-accounting .pf-sect{margin-top:19px}
 #pep-metals-period-accounting h3{margin:0 0 9px;font-size:16px}
 #pep-metals-period-accounting .pf-scroll{overflow-x:auto;border:1px solid #304559;border-radius:10px}
 #pep-metals-period-accounting table{width:100%;border-collapse:collapse;min-width:510px;font-size:12px;table-layout:auto}
 #pep-metals-period-accounting th,#pep-metals-period-accounting td{padding:11px 12px;text-align:right;border-bottom:1px solid #293d4c;font-variant-numeric:tabular-nums;white-space:nowrap}
 #pep-metals-period-accounting th:first-child,#pep-metals-period-accounting td:first-child{text-align:left}
 #pep-metals-period-accounting th{color:#a5c0d4;background:#152738;font-weight:800}
 #pep-metals-period-accounting tr:last-child td{border-bottom:0}
 #pep-metals-period-accounting .pf-muted{color:#a2b8c9}
 #pep-metals-period-accounting .pf-note{margin-top:15px;border-top:1px solid #33495b;padding-top:12px;line-height:1.6;color:#b0c8d8;font-size:12px}
 @media(max-width:620px){#pep-metals-period-accounting{margin:14px 8px;padding:14px}#pep-metals-period-accounting .pf-cards{grid-template-columns:1fr 1fr}#pep-metals-period-accounting .pf-card.combined{grid-column:1/-1}#pep-metals-period-accounting .pf-value{font-size:20px}}
</style>
<section id="pep-metals-period-accounting" aria-label="Live Metals weekly and monthly financial accounting">
 <div class="pf-top"><div><div class="pf-eyebrow">Live broker cash · GBP</div><h2>Metals accounting</h2>
 <p>XAU long + short · XAG short · Combined. Realised P&amp;L only.</p></div><span id="pf-state" class="pf-pill">Checking verified ledgers…</span></div>
 <div id="pf-content" class="pf-muted" aria-live="polite">Loading weekly and monthly results…</div>
</section>
<script id="pep-metals-period-accounting-script">
(function(){
 var root=document.getElementById("pep-metals-period-accounting");
 if(!root||root.dataset.started==="1")return;
 root.dataset.started="1";
 function cash(v){if(v===null||v===undefined||v==="")return "—";var n=Number(v);if(!Number.isFinite(n))return "—";return (n<0?"−£":"£")+Math.abs(n).toLocaleString("en-GB",{minimumFractionDigits:2,maximumFractionDigits:2});}
 function css(v){return v===null||v===undefined?"":Number(v)<0?"pf-neg":Number(v)>0?"pf-pos":"";}
 function card(label,v,combined){return '<div class="pf-card'+(combined?' combined':'')+'"><div class="pf-label">'+label+'</div><div class="pf-value '+css(v)+'">'+cash(v)+'</div></div>';}
 function rows(list,type){
  if(!Array.isArray(list)||!list.length)return '<div class="pf-muted">No periods available.</div>';
  var html='<div class="pf-scroll"><table><thead><tr><th>'+type+'</th><th>XAU</th><th>XAG short</th><th>Combined</th></tr></thead><tbody>';
  list.forEach(function(r){var note=r.status==="PARTIAL_XAU_EPOCH"?" <span title=\"XAU accounting begins during this period\">*</span>":"";html+='<tr><td>'+r.period+note+'</td><td class="'+css(r.xau_gbp)+'">'+cash(r.xau_gbp)+'</td><td class="'+css(r.xag_gbp)+'">'+cash(r.xag_gbp)+'</td><td class="'+css(r.combined_gbp)+'">'+cash(r.combined_gbp)+'</td></tr>';});
  return html+'</tbody></table></div>';
 }
 async function refresh(){
  try{
   var response=await fetch("/api/live-metals-period-accounting",{cache:"no-store"});
   if(!response.ok)throw Error("HTTP "+response.status);
   var r=await response.json();
   if(!r||r.version!=="live_metals_period_accounting_v1_20261122")throw Error("Unexpected accounting contract");
   var good=r.ok===true, s=document.getElementById("pf-state");
   s.className="pf-pill "+(good?"good":"bad");
   s.textContent=good?"VERIFIED SINCE XAU EPOCH":"PARTIAL / UNVERIFIED";
   var week=(r.weekly||[])[0]||{},month=(r.monthly||[])[0]||{};
   document.getElementById("pf-content").innerHTML=
    '<div class="pf-sub">This week · Monday onward</div><div class="pf-cards">'+
    card("XAU · long + short",week.xau_gbp,false)+card("XAG · short",week.xag_gbp,false)+card("Combined",week.combined_gbp,true)+'</div>'+
    '<div class="pf-sub">This month</div><div class="pf-cards">'+
    card("XAU · long + short",month.xau_gbp,false)+card("XAG · short",month.xag_gbp,false)+card("Combined",month.combined_gbp,true)+'</div>'+
    '<div class="pf-sect"><h3>Weekly realised totals</h3>'+rows(r.weekly,"Week starting")+'</div>'+
    '<div class="pf-sect"><h3>Monthly realised totals</h3>'+rows(r.monthly,"Month")+'</div>'+
    '<div class="pf-note">Net realised GBP, including attributed financing and fees. Open positions and demo accounts are excluded. '+
    'XAU figures cover the recorded accounting epoch onward; an asterisk denotes its partial opening period. '+
    'A dash means not independently verified or outside that accounting period, never an assumed zero. '+
    'The combined amount is shown only when both ledgers are verified. Refreshed '+new Date(r.as_of_utc).toLocaleString("en-GB")+'.</div>';
  }catch(e){
   var state=document.getElementById("pf-state"); state.className="pf-pill bad";state.textContent="REPORTING UNAVAILABLE";
   document.getElementById("pf-content").innerHTML='<div class="pf-note">Accounting data cannot be verified right now. Existing historical values are not being presented as current.</div>';
  }
 }
 refresh();
 setInterval(refresh,60000);
})();
</script>
'''


def install(app, core, prioritize):
    from fastapi.responses import JSONResponse

    def read_only_period_accounting():
        try:
            return JSONResponse(snapshot(core), headers={"Cache-Control": "no-store"})
        except Exception as exc:
            return JSONResponse({
                "ok": False, "version": VERSION, "scope": SCOPE,
                "error_type": type(exc).__name__, "weekly": [], "monthly": [],
            }, headers={"Cache-Control": "no-store"}, status_code=503)

    app.add_api_route("/api/live-metals-period-accounting", read_only_period_accounting,
                      methods=["GET"], include_in_schema=False)
    return prioritize(app, "/api/live-metals-period-accounting", read_only_period_accounting)
