"""Presentation-only authoritative Recently Closed Metals panel."""
from __future__ import annotations
from typing import Any, Dict, List

VERSION = "metals_recent_closed_dom_v2_served_core_route_2026_10_14"
ROUTE = "/api/live-xau-recently-closed"


def _sf(v):
    try: return float(v) if v not in (None, "") else None
    except Exception: return None


def _rows(core: Any, limit: int = 30) -> List[Dict[str, Any]]:
    lim = max(1, min(int(limit or 30), 100))
    with core.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM metals_xau_live_trade_links
               WHERE UPPER(COALESCE(status,'')) <> 'OPEN'
                 AND COALESCE(CAST(broker_trade_id AS TEXT),'') <> ''
                 AND COALESCE(CAST(closed_at_utc AS TEXT),'') <> ''
               ORDER BY closed_at_utc DESC, id DESC LIMIT ?""",
            (lim,),
        ).fetchall()
    out=[]
    for raw in rows:
        r=dict(raw)
        risk=_sf(r.get("estimated_risk_amount")) or _sf(r.get("requested_risk_amount"))
        pl=_sf(r.get("realized_pl"))
        out.append({
            "id":r.get("id"), "closed_at_utc":r.get("closed_at_utc"),
            "asset":"XAUUSD", "side":str(r.get("side") or "").upper(),
            "broker_trade_id":str(r.get("broker_trade_id") or ""),
            "entry_price":_sf(r.get("entry_price")),
            "exit_price":_sf(r.get("closed_price") or r.get("close_price") or r.get("exit_price") or r.get("last_known_price")),
            "units":r.get("filled_units") or r.get("requested_units") or r.get("units"),
            "realized_pl":pl, "approx_r":(pl/risk if pl is not None and risk else None),
            "close_reason":str(r.get("close_reason") or r.get("status") or "closed"),
        })
    return out


UI = r'''
<script id="pep-live-recent-closed-authoritative">
(function(){
function n(s){return String(s||'').replace(/\s+/g,' ').trim().toLowerCase()}
function e(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function m(v){var x=Number(v);return Number.isFinite(x)?(x<0?'-':'')+'£'+Math.abs(x).toFixed(2):'—'}
function f(v,d){var x=Number(v);return Number.isFinite(x)?x.toFixed(d):'—'}
function rr(v){var x=Number(v);return Number.isFinite(x)?(x>=0?'+':'')+x.toFixed(2)+'R':'—'}
function find(){var ds=[].slice.call(document.querySelectorAll('details'));for(var i=0;i<ds.length;i++){var s=ds[i].querySelector(':scope > summary')||ds[i].querySelector('summary');if(s&&n(s.textContent)==='recently closed metals trades')return ds[i]}return null}
function render(b,x){var h='<div style="padding:14px 24px;border-bottom:1px solid rgba(255,255,255,.1)"><strong>LIVE XAU ONLY.</strong> Broker-confirmed closures from the dedicated live XAU ledger. Demo/research trades are excluded.</div>';var a=(x&&x.rows)||[];if(!x||!x.ok){b.innerHTML=h+'<div style="padding:14px 24px">Live closure feed unavailable; demo data has not been substituted.</div>';return}if(!a.length){b.innerHTML=h+'<div style="padding:14px 24px">No recently closed live XAU trades.</div>';return}h+='<div style="overflow-x:auto"><table style="width:100%;min-width:980px"><thead><tr><th>Closed UTC</th><th>Asset</th><th>Side</th><th>Broker ID</th><th>Entry</th><th>Exit</th><th>Units</th><th>Realised</th><th>Approx R</th><th>Reason</th></tr></thead><tbody>';a.forEach(function(r){h+='<tr><td>'+e(r.closed_at_utc||'—')+'</td><td><strong>XAUUSD</strong></td><td><strong>'+e(r.side||'—')+'</strong></td><td>'+e(r.broker_trade_id||'—')+'</td><td>'+f(r.entry_price,3)+'</td><td>'+f(r.exit_price,3)+'</td><td>'+e(r.units==null?'—':r.units)+'</td><td><strong>'+m(r.realized_pl)+'</strong></td><td>'+rr(r.approx_r)+'</td><td>'+e(r.close_reason||'closed')+'</td></tr>'});b.innerHTML=h+'</tbody></table></div>'}
function apply(){var old=find();if(!old)return false;if(old.getAttribute('data-pep-live-recent')==='1')return true;var fresh=document.createElement('details');[].slice.call(old.attributes||[]).forEach(function(a){fresh.setAttribute(a.name,a.value)});fresh.setAttribute('data-pep-live-recent','1');fresh.open=old.open;var s=document.createElement('summary');s.textContent='Recently Closed Metals Trades';fresh.appendChild(s);var b=document.createElement('div');b.innerHTML='<div style="padding:14px 24px">Loading LIVE XAU closures…</div>';fresh.appendChild(b);old.replaceWith(fresh);fetch('''+repr(ROUTE)+r'''+'?limit=30',{cache:'no-store'}).then(function(r){return r.json()}).then(function(x){render(b,x)}).catch(function(){render(b,{ok:false,rows:[]})});return true}
var i=0,t=setInterval(function(){i++;if(apply()||i>80)clearInterval(t)},250);apply();
})();
</script>
'''


def install(core: Any, app: Any, analysis: Any) -> Dict[str, Any]:
    def endpoint(limit: int = 30):
        try: return {"ok":True,"scope":"LIVE_XAU_ONLY","demo_research_excluded":True,"rows":_rows(core,limit)}
        except Exception as exc: return {"ok":False,"scope":"LIVE_XAU_ONLY","demo_research_excluded":True,"rows":[],"error":f"{type(exc).__name__}: {exc}"}

    # Public /api/* traffic is served by the core FastAPI app. The dashboard HTML
    # is rewritten through the analysis wrapper, but registering the JSON route on
    # that wrapper produces a 404 at the public service boundary.
    served_app = core.app
    old_routes = [r for r in list(served_app.router.routes) if getattr(r, "path", None) == ROUTE]
    for route in old_routes:
        served_app.router.routes.remove(route)
    served_app.add_api_route(ROUTE, endpoint, methods=["GET"])

    base=analysis._rewrite_dashboard_version
    def rewrite(body: bytes, content_type: str) -> bytes:
        body=base(body,content_type)
        if "text/html" not in (content_type or "").lower(): return body
        try:
            text=body.decode("utf-8")
            if "pep-live-recent-closed-authoritative" not in text:
                text=text.replace("</body>",UI+"\n</body>",1) if "</body>" in text else text+UI
            return text.encode("utf-8")
        except Exception: return body
    analysis._rewrite_dashboard_version=rewrite
    status={"installed":True,"version":VERSION,"route":ROUTE,"route_target":"core.app","replaced_old_routes":len(old_routes),"scope":"LIVE_XAU_ONLY","demo_research_excluded":True,"execution_logic_changed":False}
    try: status["sample_count"]=len(_rows(core,30))
    except Exception as exc: status["sample_error"]=f"{type(exc).__name__}: {exc}"
    return status
