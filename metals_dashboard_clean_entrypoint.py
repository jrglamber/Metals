"""Metals production entrypoint with standard LIVE vs DEMO dashboard layout.

Presentation-only layer on top of metals_unified_entrypoint. Trading/execution
logic is unchanged. The dashboard follows the standard portfolio layout:
- Open Trades / Positions = LIVE XAU only
- Broker / OANDA / Accounting = one section containing LIVE XAU and DEMO / RESEARCH subsections
- all expandable sections collapsed by default
"""
from __future__ import annotations

from typing import Any, Dict

import metals_unified_entrypoint as base

analysis = base.analysis
core = analysis.core


def _scalar(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return None


@core.app.get("/api/live-xau-open-positions")
def live_xau_open_positions() -> Dict[str, Any]:
    try:
        snap = core.metals_xau_live_broker_snapshot() or {}
        trades = []
        for t in snap.get("owned_open_trades") or []:
            units = float(core.safe_float(t.get("currentUnits")) or 0.0)
            trades.append({
                "id": core.safe_str(t.get("id")),
                "instrument": core.safe_str(t.get("instrument")),
                "side": "short" if units < 0 else "long",
                "units": units,
                "open_time": core.safe_str(t.get("openTime")),
                "price": core.safe_str(t.get("price")),
                "unrealized_pl": core.safe_float(t.get("unrealizedPL")),
            })
        return {"ok": bool(snap.get("ok", True)), "scope": "LIVE_XAU_ONLY", "count": len(trades), "trades": trades}
    except Exception as exc:
        return {"ok": False, "scope": "LIVE_XAU_ONLY", "count": 0, "trades": [], "error": f"{type(exc).__name__}: {exc}"}


@core.app.get("/api/live-xau-broker-accounting")
def live_xau_broker_accounting() -> Dict[str, Any]:
    try:
        snap = core.metals_xau_live_broker_snapshot() or {}
        wanted = ("nav", "balance", "unrealized", "unrealised", "realized", "realised", "margin", "pl", "pnl", "open_count", "trade_count", "position_count")
        metrics: Dict[str, Any] = {}

        def collect(prefix: str, obj: Any, depth: int = 0) -> None:
            if depth > 2 or len(metrics) >= 30:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    key = str(k)
                    lk = key.lower()
                    path = f"{prefix}.{key}" if prefix else key
                    scalar = _scalar(v)
                    if scalar is not None and any(w in lk for w in wanted):
                        metrics[path] = scalar
                    elif isinstance(v, dict):
                        collect(path, v, depth + 1)

        collect("", snap)
        return {"ok": bool(snap.get("ok", True)), "scope": "LIVE_XAU_ONLY", "owned_open_count": len(snap.get("owned_open_trades") or []), "metrics": metrics}
    except Exception as exc:
        return {"ok": False, "scope": "LIVE_XAU_ONLY", "owned_open_count": 0, "metrics": {}, "error": f"{type(exc).__name__}: {exc}"}


_base_rewrite = analysis._rewrite_dashboard_version

_STANDARD_LAYOUT = r'''
<style id="pep-standard-metals-layout-style">
  #pep-live-section-label, #pep-demo-section-label { display:none !important; }
  #pep-live-open-positions, #pep-broker-accounting-combined { margin:12px 0; }
  #pep-live-open-positions .pep-body, #pep-broker-accounting-combined .pep-body { padding:0 14px 14px; }
  #pep-live-open-positions table, #pep-broker-accounting-combined table { width:100%; border-collapse:collapse; font-size:.88rem; }
  #pep-live-open-positions th, #pep-live-open-positions td,
  #pep-broker-accounting-combined th, #pep-broker-accounting-combined td { padding:7px 8px; border-bottom:1px solid rgba(255,255,255,.08); text-align:left; }
  .pep-empty { opacity:.72; padding:8px 0 2px; }
  .pep-nested { margin:10px 0; border:1px solid rgba(255,255,255,.12); border-radius:10px; overflow:hidden; }
  .pep-nested > summary { cursor:pointer; padding:11px 12px; font-weight:750; }
  .pep-nested-body { padding:0 12px 12px; }
</style>
<script id="pep-standard-metals-layout-script">
(function(){
  function norm(s){ return (s||'').replace(/\s+/g,' ').trim(); }
  function allEls(){ return Array.prototype.slice.call(document.querySelectorAll('body *')); }
  function nearestWidget(el){
    var p=el;
    for(var i=0;i<8 && p;i++,p=p.parentElement){
      if(!p) break;
      var tag=(p.tagName||'').toLowerCase();
      var cls=String(p.className||'').toLowerCase();
      if(tag==='details'||tag==='section'||/card|panel|block|widget|section/.test(cls)) return p;
    }
    return el;
  }
  function findExact(labels, excludeSelector){
    var els=allEls();
    for(var i=0;i<els.length;i++){
      if(excludeSelector && els[i].closest && els[i].closest(excludeSelector)) continue;
      var t=norm(els[i].textContent).toLowerCase();
      for(var j=0;j<labels.length;j++) if(t===labels[j]) return els[i];
    }
    return null;
  }
  function collapseAll(root){ (root||document).querySelectorAll('details').forEach(function(d){ d.open=false; }); }

  function findDemoOpenWidget(){
    var h=findExact(['demo open trades / positions','open trades / positions','open trades/positions'],'#pep-live-open-positions');
    return h ? nearestWidget(h) : null;
  }
  function findDemoAccountingWidget(){
    var h=findExact(['demo broker / oanda / accounting','broker / oanda / accounting'],'#pep-broker-accounting-combined');
    return h ? nearestWidget(h) : null;
  }

  function makeLiveOpen(){
    var d=document.getElementById('pep-live-open-positions');
    if(d) return d;
    d=document.createElement('details');
    d.id='pep-live-open-positions';
    d.innerHTML='<summary>Open Trades / Positions</summary><div class="pep-body"><div class="pep-empty">Loading live XAU positions…</div></div>';
    fetch('/api/live-xau-open-positions',{cache:'no-store'}).then(function(r){return r.json();}).then(function(data){
      var body=d.querySelector('.pep-body'), rows=(data&&data.trades)||[];
      if(!rows.length){ body.innerHTML='<div class="pep-empty">No live XAU positions open.</div>'; return; }
      var html='<table><thead><tr><th>Instrument</th><th>Side</th><th>Units</th><th>Entry</th><th>Unrealised P/L</th><th>Opened</th></tr></thead><tbody>';
      rows.forEach(function(x){ html+='<tr><td>'+(x.instrument||'XAU_USD')+'</td><td>'+String(x.side||'').toUpperCase()+'</td><td>'+x.units+'</td><td>'+(x.price||'')+'</td><td>'+(x.unrealized_pl==null?'':x.unrealized_pl)+'</td><td>'+(x.open_time||'')+'</td></tr>'; });
      body.innerHTML=html+'</tbody></table>';
    }).catch(function(){ var body=d.querySelector('.pep-body'); if(body) body.innerHTML='<div class="pep-empty">Live XAU position view unavailable.</div>'; });
    d.open=false;
    return d;
  }

  function buildCombinedBroker(demoOpen,demoAccounting){
    var outer=document.getElementById('pep-broker-accounting-combined');
    if(outer) return outer;
    outer=document.createElement('details');
    outer.id='pep-broker-accounting-combined';
    outer.innerHTML='<summary>Broker / OANDA / Accounting</summary><div class="pep-body"></div>';
    var body=outer.querySelector('.pep-body');

    var live=document.createElement('details');
    live.className='pep-nested';
    live.innerHTML='<summary>LIVE — XAU LONG + XAU SHORT</summary><div class="pep-nested-body"><div class="pep-empty">Loading live broker/accounting summary…</div></div>';
    body.appendChild(live);
    fetch('/api/live-xau-broker-accounting',{cache:'no-store'}).then(function(r){return r.json();}).then(function(data){
      var b=live.querySelector('.pep-nested-body'), metrics=(data&&data.metrics)||{}, keys=Object.keys(metrics);
      var html='<div class="pep-empty">Live owned open trades: '+((data&&data.owned_open_count)||0)+'</div>';
      if(keys.length){ html+='<table><tbody>'; keys.forEach(function(k){ html+='<tr><th>'+k+'</th><td>'+metrics[k]+'</td></tr>'; }); html+='</tbody></table>'; }
      b.innerHTML=html;
    }).catch(function(){ var b=live.querySelector('.pep-nested-body'); if(b) b.innerHTML='<div class="pep-empty">Live broker/accounting view unavailable.</div>'; });

    var demo=document.createElement('details');
    demo.className='pep-nested';
    demo.innerHTML='<summary>DEMO / RESEARCH — XAG + legacy practice positions</summary><div class="pep-nested-body"></div>';
    var db=demo.querySelector('.pep-nested-body');
    if(demoOpen){
      var candidates=demoOpen.querySelectorAll ? demoOpen.querySelectorAll('summary,h1,h2,h3,h4,h5,strong,b,div,span') : [];
      for(var i=0;i<candidates.length;i++){
        var t=norm(candidates[i].textContent).toLowerCase();
        if(t==='open trades / positions'||t==='open trades/positions'||t==='demo open trades / positions'){ candidates[i].textContent='Demo Open Trades / Positions'; break; }
      }
      db.appendChild(demoOpen);
    }
    if(demoAccounting){
      var cands=demoAccounting.querySelectorAll ? demoAccounting.querySelectorAll('summary,h1,h2,h3,h4,h5,strong,b,div,span') : [];
      for(var j=0;j<cands.length;j++){
        var tt=norm(cands[j].textContent).toLowerCase();
        if(tt==='broker / oanda / accounting'||tt==='demo broker / oanda / accounting'){ cands[j].textContent='Demo Broker / OANDA / Accounting'; break; }
      }
      db.appendChild(demoAccounting);
    }
    body.appendChild(demo);
    outer.open=false; live.open=false; demo.open=false;
    return outer;
  }

  function apply(){
    if(document.documentElement.getAttribute('data-pep-standard-metals')==='1') return true;
    var demoOpen=findDemoOpenWidget();
    var demoAccounting=findDemoAccountingWidget();
    if(!demoOpen || !demoAccounting) return false;

    // Use the old demo open-trades location as the standard LIVE open-trades location.
    var openParent=demoOpen.parentNode, openNext=demoOpen.nextSibling;
    var liveOpen=makeLiveOpen();
    if(openParent) openParent.insertBefore(liveOpen,openNext);

    // Use the old accounting location for one standard combined accounting section.
    var acctParent=demoAccounting.parentNode, acctNext=demoAccounting.nextSibling;
    var combined=buildCombinedBroker(demoOpen,demoAccounting);
    if(acctParent) acctParent.insertBefore(combined,acctNext);

    collapseAll(document);
    document.documentElement.setAttribute('data-pep-standard-metals','1');
    return true;
  }

  var attempts=0;
  var timer=setInterval(function(){ attempts++; if(apply()||attempts>40) clearInterval(timer); },250);
  var observer=new MutationObserver(function(ms){ ms.forEach(function(m){ m.addedNodes.forEach(function(n){ if(n&&n.nodeType===1) collapseAll(n); }); }); });
  observer.observe(document.documentElement,{childList:true,subtree:true});
  apply();
})();
</script>
'''


def _rewrite_dashboard_version(body: bytes, content_type: str) -> bytes:
    body = _base_rewrite(body, content_type)
    if "text/html" not in (content_type or "").lower():
        return body
    try:
        text = body.decode("utf-8")
        if "pep-standard-metals-layout-script" not in text:
            if "</body>" in text:
                text = text.replace("</body>", _STANDARD_LAYOUT + "\n</body>", 1)
            else:
                text += _STANDARD_LAYOUT
        return text.encode("utf-8")
    except Exception:
        return body


analysis._rewrite_dashboard_version = _rewrite_dashboard_version
app = analysis.app
