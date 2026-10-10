"""Metals dashboard v2 — strict LIVE XAU vs DEMO separation.

Additive presentation/observability layer only. Existing execution, entry, exit,
harvest, demo/research and accounting behaviour remains owned by the imported
production stack.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict

import metals_live_basket_entrypoint as base

analysis = base.analysis
core = base.core
app = analysis.app

STRICT_RESET_ID = "live-xau-strict-separation-v2-20261007"
try:
    STRICT_RESET_RESULT = base._reset_live_xau_protection_cycle(STRICT_RESET_ID)
except Exception as exc:
    STRICT_RESET_RESULT = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
print("LIVE_XAU_PROTECTION_RESET_RESULT", STRICT_RESET_RESULT, flush=True)


def _sf(v: Any, default: float = 0.0) -> float:
    try:
        x = core.safe_float(v)
        return float(x) if x is not None else default
    except Exception:
        try: return float(v)
        except Exception: return default


def _ss(v: Any) -> str:
    try: return core.safe_str(v)
    except Exception: return "" if v is None else str(v)


def _dt(v: Any):
    try: return core.parse_dt(v)
    except Exception: return None


def _week_key(dt: datetime) -> str:
    iso = dt.isocalendar(); return f"{iso.year}-W{iso.week:02d}"


def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _aggregate_live_accounting() -> Dict[str, Any]:
    snap = core.metals_xau_live_accounting_snapshot() or {}
    txs=[]; links=[]
    with core.get_conn() as conn:
        try: txs=[dict(r) for r in conn.execute("SELECT * FROM metals_xau_live_broker_transactions ORDER BY transaction_time").fetchall()]
        except Exception: pass
        try: links=[dict(r) for r in conn.execute("SELECT * FROM metals_xau_live_trade_links WHERE status<>'OPEN' AND closed_at_utc IS NOT NULL AND closed_at_utc<>'' ORDER BY closed_at_utc").fetchall()]
        except Exception: pass
    def bucket(): return defaultdict(lambda:{"net_realised":0.0,"trade_pl":0.0,"financing":0.0,"realised_r":0.0,"closed":0,"wins":0,"losses":0})
    weeks=bucket(); months=bucket()
    for r in txs:
        d=_dt(r.get("transaction_time"))
        if not d: continue
        if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
        for b,k in ((weeks,_week_key(d)),(months,_month_key(d))):
            b[k]["net_realised"]+=_sf(r.get("net_realized_gbp")); b[k]["trade_pl"]+=_sf(r.get("pl_gbp")); b[k]["financing"]+=_sf(r.get("financing_gbp"))
    for r in links:
        d=_dt(r.get("closed_at_utc"))
        if not d: continue
        if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
        pnl=_sf(r.get("realized_pl")); risk=_sf(r.get("estimated_risk_amount")) or _sf(r.get("requested_risk_amount")); rr=pnl/risk if risk>0 else 0.0
        for b,k in ((weeks,_week_key(d)),(months,_month_key(d))):
            b[k]["closed"]+=1; b[k]["wins"]+=1 if pnl>0 else 0; b[k]["losses"]+=1 if pnl<0 else 0; b[k]["realised_r"]+=rr
    return {"ok":bool(snap.get("ok",True)),"scope":"LIVE_XAU_LONG_SHORT_ONLY","current_open_upl_gbp":_sf(snap.get("current_open_upl_gbp")),"periods":snap.get("periods") or {},"weekly":[{"period":k,**v} for k,v in sorted(weeks.items())[-8:]],"monthly":[{"period":k,**v} for k,v in sorted(months.items())[-8:]],"time_utc":datetime.now(timezone.utc).isoformat()}


def _live_trade_observability() -> Dict[str, Any]:
    """Read-only per-trade manager audit. Never sends, changes or closes orders."""
    broker=core.metals_xau_live_broker_snapshot(include_account_summary=False) or {}
    broker_by_id={_ss(t.get("id")):t for t in broker.get("owned_open_trades") or []}
    rows=[]
    with core.get_conn() as conn:
        try: links=[dict(r) for r in conn.execute("SELECT * FROM metals_xau_live_trade_links WHERE status='OPEN' ORDER BY id").fetchall()]
        except Exception: links=[]
    now=datetime.now(timezone.utc)
    for link in links:
        tid=_ss(link.get("broker_trade_id") or link.get("trade_id") or link.get("oanda_trade_id")); bt=broker_by_id.get(tid,{})
        side=_ss(link.get("side")).lower() or ("short" if _sf(bt.get("currentUnits"))<0 else "long")
        opened=_dt(link.get("opened_at_utc") or link.get("open_time") or bt.get("openTime")); age_h=None
        if opened:
            if opened.tzinfo is None: opened=opened.replace(tzinfo=timezone.utc)
            age_h=max(0.0,(now-opened).total_seconds()/3600.0)
        metrics={}
        try: metrics=core._metals_xau_live_trade_metrics(link, bt) or {}
        except Exception as exc: metrics={"metrics_error":f"{type(exc).__name__}: {exc}"}
        policy=_ss(link.get("active_exit_policy") or link.get("exit_policy"))
        rows.append({"trade_id":tid,"side":side,"age_hours":age_h,"mature_48h":bool(age_h is not None and age_h>=48.0),"policy":policy,"entry_price":_sf(link.get("entry_price") or bt.get("price")),"current_price":_sf(metrics.get("current_price") or metrics.get("price")),"current_r":_sf(metrics.get("current_r")),"mfe_r":_sf(metrics.get("mfe_r")),"mae_r":_sf(metrics.get("mae_r")),"broker_unrealized_pl":_sf(bt.get("unrealizedPL")),"manager_metrics":metrics})
    shorts=[r for r in rows if r["side"]=="short"]; mature=[r for r in shorts if r["mature_48h"]]
    return {"ok":bool(broker.get("ok",True)),"scope":"READ_ONLY_LIVE_XAU_MANAGER_AUDIT","execution_mutated":False,"open_count":len(rows),"short_count":len(shorts),"mature_short_count":len(mature),"mature_short_with_positive_mfe":sum(1 for r in mature if r["mfe_r"]>0),"trades":rows,"time_utc":now.isoformat()}


@core.app.get("/api/live-xau-accounting-full")
def live_xau_accounting_full():
    try: return _aggregate_live_accounting()
    except Exception as exc: return {"ok":False,"scope":"LIVE_XAU_LONG_SHORT_ONLY","error":f"{type(exc).__name__}: {exc}"}

@core.app.get("/api/live-xau-protection-v2")
def live_xau_protection_v2():
    data=dict(base.live_xau_basket_manager() or {}); data["strict_reset_result"]=STRICT_RESET_RESULT; data["scope"]="LIVE_XAU_LONG_SHORT_ONLY"; return data

@core.app.get("/api/live-xau-manager-audit")
def live_xau_manager_audit():
    try: return _live_trade_observability()
    except Exception as exc: return {"ok":False,"scope":"READ_ONLY_LIVE_XAU_MANAGER_AUDIT","execution_mutated":False,"error":f"{type(exc).__name__}: {exc}"}


_base_rewrite=analysis._rewrite_dashboard_version
_OBS_UI=r'''
<style id="pep-metals-observability-style">.pep-obs{margin:10px 0}.pep-obs table{width:100%;border-collapse:collapse;font-size:.82rem}.pep-obs th,.pep-obs td{padding:6px;border-bottom:1px solid rgba(255,255,255,.08);text-align:left}.pep-obs-note{opacity:.75}</style>
<script id="pep-metals-observability-script">
(function(){
 function n(x,d){var v=Number(x);return Number.isFinite(v)?v.toFixed(d==null?2:d):'—';}
 function apply(){var host=document.getElementById('pep-live-xau-ops-v2')||document.getElementById('pep-live-basket-manager');if(!host)return false;if(document.getElementById('pep-live-xau-manager-audit-ui'))return true;var d=document.createElement('details');d.id='pep-live-xau-manager-audit-ui';d.className='pep-obs';d.innerHTML='<summary>Live XAU Trade Manager Audit — read only</summary><div class="pep-obs-body"><div class="pep-obs-note">Loading per-trade manager state…</div></div>';host.appendChild(d);d.open=false;fetch('/api/live-xau-manager-audit',{cache:'no-store'}).then(function(r){return r.json();}).then(function(x){var b=d.querySelector('.pep-obs-body');if(!x||!x.ok){b.innerHTML='<div>Manager audit unavailable.</div>';return;}var h='<div class="pep-obs-note">Observability only — no execution changes. Mature XAU SHORT: '+x.mature_short_count+'; with positive MFE: '+x.mature_short_with_positive_mfe+'.</div><table><thead><tr><th>ID</th><th>Side</th><th>Age</th><th>Policy</th><th>Current R</th><th>MFE</th><th>MAE</th><th>Open P/L</th></tr></thead><tbody>';(x.trades||[]).forEach(function(t){h+='<tr><td>'+t.trade_id+'</td><td>'+String(t.side).toUpperCase()+'</td><td>'+n(t.age_hours,1)+'h</td><td>'+(t.policy||'—')+'</td><td>'+n(t.current_r,2)+'R</td><td>'+n(t.mfe_r,2)+'R</td><td>'+n(t.mae_r,2)+'R</td><td>£'+n(t.broker_unrealized_pl,2)+'</td></tr>';});b.innerHTML=h+'</tbody></table>';}).catch(function(){d.querySelector('.pep-obs-body').innerHTML='<div>Manager audit unavailable.</div>';});return true;}
 var tries=0,t=setInterval(function(){tries++;if(apply()||tries>40)clearInterval(t);},250);apply();
})();
</script>
'''
def _rewrite_dashboard_version(body:bytes,content_type:str)->bytes:
    body=_base_rewrite(body,content_type)
    if "text/html" not in (content_type or "").lower(): return body
    try:
        text=body.decode("utf-8")
        if "pep-metals-observability-script" not in text: text=text.replace("</body>",_OBS_UI+"\n</body>",1) if "</body>" in text else text+_OBS_UI
        return text.encode("utf-8")
    except Exception: return body
analysis._rewrite_dashboard_version=_rewrite_dashboard_version
app=analysis.app
