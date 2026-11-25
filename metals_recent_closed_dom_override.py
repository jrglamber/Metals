"""Unified LIVE recently closed Metals panel (read-only XAU long/short + XAG short).

Maintains the historical /api/live-xau-recently-closed GET alias for existing
consumers, while the dashboard uses the honest combined feed name.
"""
from __future__ import annotations
from typing import Any,Dict,List
import metals_live_recent_closed_combined as combined

VERSION = "metals_recent_closed_dom_combined_live_xau_xag_v3_2026_11_25"
ROUTE = "/api/live-metals-recently-closed"
LEGACY_ROUTE = "/api/live-xau-recently-closed"

def _rows(core:Any,limit:int=30)->List[Dict[str,Any]]:
    return combined.rows(core,limit)

UI = r"""
<style id="pep-live-recent-closed-combined-style">
#pep-live-recent-closed-toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:10px 16px}
#pep-live-recent-closed-toolbar select{background:#16232d;color:inherit;border:1px solid #50606b;padding:6px 10px;border-radius:7px}
#pep-live-recent-closed-note{font-size:.82rem;opacity:.8;padding:10px 16px}
</style>
<script id="pep-live-recent-closed-authoritative">
(function(){
  function norm(s){return String(s||'').replace(/\s+/g,' ').trim().toLowerCase()}
  function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,function(ch){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','"':'&quot;',"'":'&#39;'}[ch]})}
  function number(v,d){var n=Number(v);return v==null||v===''||!isFinite(n)?'—':n.toFixed(d)}
  function money(v){var n=Number(v);return v==null||v===''||!isFinite(n)?'—':(n<0?'-':'')+'£'+Math.abs(n).toFixed(2)}
  function find(){
    var panels=[].slice.call(document.querySelectorAll('details'));
    for(var i=0;i<panels.length;i++){
      var s=panels[i].querySelector(':scope > summary')||panels[i].querySelector('summary');
      if(s&&['recently closed metals trades','live metals — recently closed','live metals - recently closed'].indexOf(norm(s.textContent))>=0)return panels[i];
    }
    return null;
  }
  function render(body,data,filter){
    var h='<div id="pep-live-recent-closed-note"><strong>LIVE XAU LONG / SHORT + XAG SHORT</strong> · broker-linked closed trades only. Demo excluded. '+
      'Trade P/L is from the closed-trade ledger and may differ from verified net cash after fees/financing.</div>';
    if(!data||!data.ok){body.innerHTML=h+'<div id="pep-live-recent-closed-note">Live closure feed unavailable; no demo data substituted.</div>';return}
    var all=data.rows||[], rows=all.filter(function(r){return !filter||filter==='ALL'||r.asset===filter});
    h+='<div id="pep-live-recent-closed-toolbar"><label for="pep-closed-asset-filter">Asset</label>'+
      '<select id="pep-closed-asset-filter"><option value="ALL">All live ('+all.length+')</option>'+
      '<option value="XAUUSD">Gold ('+all.filter(function(r){return r.asset==='XAUUSD'}).length+')</option>'+
      '<option value="XAGUSD">Silver short ('+all.filter(function(r){return r.asset==='XAGUSD'}).length+')</option></select></div>';
    if(!rows.length){body.innerHTML=h+'<div id="pep-live-recent-closed-note">No closed trades for this selection.</div>';var sel=body.querySelector('select');if(sel){sel.value=filter||'ALL';sel.addEventListener('change',function(){render(body,data,sel.value)})}return}
    h+='<div class="table-scroll"><table style="width:100%;min-width:1000px"><thead><tr>'+
      '<th>Closed (UTC)</th><th>Asset</th><th>Side</th><th>OANDA ID</th><th>Entry</th><th>Exit</th>'+
      '<th>Units</th><th>Trade P/L*</th><th>Approx R</th><th>Close reason</th></tr></thead><tbody>';
    rows.forEach(function(r){
      var ar=Number(r.approx_r), rlabel=r.approx_r==null?'—':((ar>=0?'+':'')+ar.toFixed(2)+'R');
      h+='<tr><td>'+esc(r.closed_at_utc||'—')+'</td><td><strong>'+esc(r.asset||'—')+'</strong></td>'+
        '<td>'+esc(r.side||'—')+'</td><td>'+esc(r.broker_trade_id||'—')+'</td>'+
        '<td>'+esc(number(r.entry_price,3))+'</td><td>'+esc(number(r.exit_price,3))+'</td>'+
        '<td>'+esc(number(r.units,1))+'</td><td>'+esc(money(r.realized_pl))+'</td>'+
        '<td>'+esc(rlabel)+'</td><td>'+esc(r.close_reason||r.close_status||'closed')+'</td></tr>';
    });
    h+='</tbody></table></div>';
    body.innerHTML=h;
    var sel=body.querySelector('#pep-closed-asset-filter');
    if(sel){sel.value=filter||'ALL';sel.addEventListener('change',function(){render(body,data,sel.value)})}
  }
  function apply(){
    var panel=find();if(!panel)return false;
    if(panel.getAttribute('data-pep-live-recent')==='combined')return true;
    var cloned=document.createElement('details');
    [].slice.call(panel.attributes||[]).forEach(function(a){cloned.setAttribute(a.name,a.value)});
    cloned.setAttribute('data-pep-live-recent','combined');
    cloned.open=panel.open;
    var label=document.createElement('summary');label.textContent='LIVE Metals — Recently Closed';cloned.appendChild(label);
    var body=document.createElement('div');body.innerHTML='<div id="pep-live-recent-closed-note">Loading LIVE XAU and XAG SHORT closures…</div>';cloned.appendChild(body);
    panel.replaceWith(cloned);
    var currentFilter='ALL',loading=false;
    async function refresh(){
      if(loading)return;loading=true;
      var choice=body.querySelector('#pep-closed-asset-filter');
      if(choice)currentFilter=choice.value;
      try{
        var response=await fetch('/api/live-metals-recently-closed?limit=50',{cache:'no-store'});
        if(!response.ok)throw Error('HTTP '+response.status);
        render(body,await response.json(),currentFilter);
      }catch(error){render(body,{ok:false,rows:[]},currentFilter)}
      finally{loading=false}
    }
    cloned.addEventListener('toggle',function(){if(cloned.open)refresh()});
    setInterval(function(){if(cloned.open&&cloned.isConnected)refresh()},60000);
    refresh();
    return true;
  }
  var attempts=0,t=setInterval(function(){attempts++;if(apply()||attempts>80)clearInterval(t)},250);apply();
})();
</script>
"""

def install(core: Any, app:Any, analysis:Any)->Dict[str,Any]:
    def endpoint(limit:int=30):
        try:
            return combined.snapshot(core,limit)
        except Exception as exc:
            return {"ok":False,"scope":"LIVE_XAU_LONG_SHORT_AND_XAG_SHORT",
                    "demo_research_excluded":True,"rows":[],
                    "error_type":type(exc).__name__}

    # Public /api traffic resolves to core FastAPI. Both the combined route
    # and legacy alias return the same safe rows for existing consumers.
    served=core.app
    replaced=0
    for path in (ROUTE,LEGACY_ROUTE):
        prior=[r for r in list(served.router.routes) if getattr(r,"path",None)==path]
        for route in prior:
            served.router.routes.remove(route);replaced+=1
        served.add_api_route(path,endpoint,methods=["GET"])

    base=analysis._rewrite_dashboard_version
    def rewrite(body:bytes,content_type:str)->bytes:
        body=base(body,content_type)
        if "text/html" not in (content_type or "").lower():
            return body
        try:
            html=body.decode("utf-8")
            if 'pep-live-recent-closed-authoritative' not in html:
                html=html.replace("</body>",UI+"\n</body>",1) if "</body>" in html else html+UI
            return html.encode("utf-8")
        except Exception:
            return body
    analysis._rewrite_dashboard_version=rewrite
    status={"installed":True,"version":VERSION,"route":ROUTE,"legacy_alias":LEGACY_ROUTE,
            "route_target":"core.app","replaced_old_routes":replaced,
            "scope":"LIVE_XAU_LONG_SHORT_AND_XAG_SHORT",
            "demo_research_excluded":True,"execution_logic_changed":False}
    try: status["sample_count"]=len(_rows(core,30))
    except Exception as exc:status["sample_error_type"]=type(exc).__name__
    return status
