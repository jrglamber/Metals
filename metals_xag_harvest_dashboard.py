"""Read-only XAG Cash-Banking Ladder next to XAU, from XAG-only stage records."""
DASHBOARD_SNIPPET = r"""
<style id="pep-xag-ladder-css">
#pep-xag-cash-ladder{margin:24px 0;padding:4px 0 12px;color:inherit}
#pep-xag-cash-ladder h2{font-size:clamp(1.5rem,5vw,2rem);margin:16px 0 20px;font-weight:750}
#pep-xag-cash-ladder .meta{display:grid;grid-template-columns:minmax(120px,.85fr) minmax(0,1.5fr);border-radius:10px;border:1px solid rgba(156,163,175,.23);overflow:hidden;margin:0 0 18px}
#pep-xag-cash-ladder .meta>*{padding:11px 13px;border-bottom:1px solid rgba(156,163,175,.18);overflow-wrap:anywhere}
#pep-xag-cash-ladder .meta>:nth-child(odd){font-weight:700;background:rgba(0,0,0,.14)}
#pep-xag-cash-ladder .meta>:nth-last-child(-n+2){border-bottom:0}
#pep-xag-cash-ladder .scroll{max-width:100%;overflow-x:auto;border:1px solid rgba(156,163,175,.3);border-radius:12px;-webkit-overflow-scrolling:touch}
#pep-xag-cash-ladder table{border-collapse:collapse;text-align:left;width:100%;min-width:610px;margin:0;font-size:.93rem}
#pep-xag-cash-ladder th,#pep-xag-cash-ladder td{padding:14px 12px;border-bottom:1px solid rgba(156,163,175,.2);white-space:nowrap}
#pep-xag-cash-ladder th{font-weight:750}
#pep-xag-cash-ladder tbody tr:last-child td{border-bottom:0}
#pep-xag-cash-ladder .note{opacity:.78;line-height:1.45;margin:15px 0 0}
#pep-xag-cash-ladder .warning{color:#fbbf24;font-weight:600}
#pep-xag-cash-ladder .done{color:#86efac}
</style>
<section id="pep-xag-cash-ladder" data-lane="LIVE_XAG_SHORT_ONLY" hidden>
  <h2>XAG Cash-Banking Ladder</h2>
  <div class="meta">
    <div>Active live cycle</div><div id="pep-xag-ladder-cycle">Loading…</div>
    <div>Basket R / HWM R</div><div id="pep-xag-ladder-r">Loading…</div>
    <div>Next level</div><div id="pep-xag-ladder-next">Loading…</div>
  </div>
  <p id="pep-xag-ladder-warning" class="warning" hidden></p>
  <div class="scroll"><table aria-label="XAG cash banking ladder">
    <thead><tr><th>Level</th><th>Status</th><th>Bank %</th><th>Target at Trigger</th><th>Actual £ Banked</th></tr></thead>
    <tbody id="pep-xag-ladder-body"><tr><td colspan="5">Loading live XAG harvest records…</td></tr></tbody>
  </table></div>
  <p class="note">Actual £ Banked is populated from persisted LIVE XAG harvest executions. Untriggered stages remain visible; XAG and XAU harvest cycles are independent. Whole-unit closes can exceed a stage target.</p>
</section>
<script id="pep-xag-ladder-js">
(function(){
  'use strict';
  if(window.__pepXagLadderInstalled)return;
  window.__pepXagLadderInstalled=true;
  var panel=document.getElementById('pep-xag-cash-ladder'),attached=false,busy=false;
  if(!panel)return;
  var byId=function(id){return document.getElementById(id);};
  function findXau(){
    var headings=document.querySelectorAll('h1,h2,h3,h4');
    for(var i=0;i<headings.length;i++)
      if((headings[i].textContent||'').trim().replace(/\s+/g,' ')==='XAU Cash-Banking Ladder')
        return headings[i];
    return null;
  }
  function attach(){
    if(attached)return true;
    var heading=findXau();if(!heading)return false;
    var parent=heading.parentElement,table=parent&&parent.querySelector('table');
    if(!table&&parent&&parent.parentElement)table=parent.parentElement.querySelector('table');
    var anchor=table?(table.closest('.table-wrap,.table-responsive,.table-container')||table):heading;
    if(table&&anchor.nextElementSibling&&
        (anchor.nextElementSibling.tagName==='P'||String(anchor.nextElementSibling.className||'').indexOf('note')>=0))
        anchor=anchor.nextElementSibling;
    if(!anchor.parentNode)return false;
    anchor.insertAdjacentElement('afterend',panel);
    panel.hidden=false;attached=true;
    refresh();setInterval(refresh,30000);return true;
  }
  function formatMoney(v){return v==null||!Number.isFinite(Number(v))?'—':'£'+Number(v).toFixed(2);}
  function formatR(v){return v==null||!Number.isFinite(Number(v))?'—':Number(v).toFixed(2)+'R';}
  function warning(message){var el=byId('pep-xag-ladder-warning');el.textContent=message;el.hidden=!message;}
  function cell(tr,value,className){
    var td=document.createElement('td');td.textContent=String(value);
    if(className)td.className=className;tr.appendChild(td);
  }
  function render(data){
    if(!data||data.ok!==true||data.scope!=='LIVE_XAG_SHORT_ONLY'||!data.cycle||!Array.isArray(data.stages)){
      warning('XAG harvest data unavailable. No figures have been inferred.');return;
    }
    var cycle=String(data.cycle.cycle_id||'');
    var stages=data.stages.filter(function(s){return String(s.cycle_id||'')===cycle;});
    var levels={},stageByLevel={};
    (data.ladder_r||[]).forEach(function(v){levels[Number(v)]=true;});
    stages.forEach(function(s){var v=Number(s.threshold_r);levels[v]=true;stageByLevel[v]=s;});
    var ordered=Object.keys(levels).map(Number).filter(Number.isFinite).sort(function(a,b){return a-b;});
    var tbody=byId('pep-xag-ladder-body');tbody.replaceChildren();
    ordered.forEach(function(level){
      var s=stageByLevel[level]||{},tr=document.createElement('tr');
      var pct=s.bank_fraction==null?(level<=80?20:25):Math.round(Number(s.bank_fraction)*100);
      var status=String(s.status||'NOT_ARMED');
      cell(tr,level+'R');cell(tr,status,status==='EXECUTED'?'done':'');
      cell(tr,pct+'%');cell(tr,s.id?formatMoney(s.target_bank_gbp):'—');
      cell(tr,s.id?formatMoney(s.executed_bank_gbp):'—');
      tbody.appendChild(tr);
    });
    byId('pep-xag-ladder-cycle').textContent=cycle||'Flat — no active XAG campaign';
    var tick=data.last_tick||{};
    byId('pep-xag-ladder-r').textContent=formatR(tick.current_r)+' / '+formatR(data.cycle.high_water_r);
    var executed=stages.filter(function(s){return s.status==='EXECUTED';});
    var next=executed.length?Math.max.apply(null,executed.map(function(s){return Number(s.threshold_r);})) + 50:30;
    byId('pep-xag-ladder-next').textContent=next+'R';
    warning(/BLOCKED|REVIEW_REQUIRED/.test(String(tick.status||''))?
      'Harvest requires review: '+String(tick.reason||tick.status):'');
  }
  function refresh(){
    if(!attached||busy)return;busy=true;
    fetch('/api/live-xag-harvest-ladder-view',{cache:'no-store',credentials:'same-origin'})
      .then(function(r){if(!r.ok)throw Error(String(r.status));return r.json();})
      .then(render).catch(function(){warning('XAG harvest status is temporarily unavailable.');})
      .finally(function(){busy=false;});
  }
  if(!attach()){
    var observer=new MutationObserver(function(){if(attach())observer.disconnect();});
    observer.observe(document.documentElement,{childList:true,subtree:true});
    setTimeout(function(){observer.disconnect();attach();},45000);
  }
})();
</script>
""