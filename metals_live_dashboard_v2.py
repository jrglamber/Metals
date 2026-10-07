"""Metals dashboard v2 — strict LIVE XAU vs DEMO separation.

This presentation/runtime layer sits on top of metals_live_basket_entrypoint:
- LIVE = XAU LONG + XAU SHORT only.
- DEMO = XAG LONG/SHORT plus legacy practice history/positions.
- live accounting is rebuilt from metals_xau_live_* tables/account only.
- live basket HWM/harvest is rebuilt from metals_xau_live_* state only.
- old demo basket/protection widgets remain available, but only under DEMO.
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
        try:
            return float(v)
        except Exception:
            return default


def _ss(v: Any) -> str:
    try:
        return core.safe_str(v)
    except Exception:
        return "" if v is None else str(v)


def _dt(v: Any):
    try:
        return core.parse_dt(v)
    except Exception:
        return None


def _week_key(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _aggregate_live_accounting() -> Dict[str, Any]:
    snap = core.metals_xau_live_accounting_snapshot() or {}
    txs = []
    links = []
    with core.get_conn() as conn:
        try:
            txs = [dict(r) for r in conn.execute(
                "SELECT * FROM metals_xau_live_broker_transactions ORDER BY transaction_time"
            ).fetchall()]
        except Exception:
            txs = []
        try:
            links = [dict(r) for r in conn.execute(
                "SELECT * FROM metals_xau_live_trade_links WHERE status<>'OPEN' AND closed_at_utc IS NOT NULL AND closed_at_utc<>'' ORDER BY closed_at_utc"
            ).fetchall()]
        except Exception:
            links = []

    weeks: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "net_realised": 0.0, "trade_pl": 0.0, "financing": 0.0,
        "realised_r": 0.0, "closed": 0, "wins": 0, "losses": 0,
    })
    months: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "net_realised": 0.0, "trade_pl": 0.0, "financing": 0.0,
        "realised_r": 0.0, "closed": 0, "wins": 0, "losses": 0,
    })

    for r in txs:
        d = _dt(r.get("transaction_time"))
        if not d:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        for bucket, key in ((weeks, _week_key(d)), (months, _month_key(d))):
            bucket[key]["net_realised"] += _sf(r.get("net_realized_gbp"))
            bucket[key]["trade_pl"] += _sf(r.get("pl_gbp"))
            bucket[key]["financing"] += _sf(r.get("financing_gbp"))

    for r in links:
        d = _dt(r.get("closed_at_utc"))
        if not d:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        pnl = _sf(r.get("realized_pl"))
        risk = _sf(r.get("estimated_risk_amount")) or _sf(r.get("requested_risk_amount"))
        rr = pnl / risk if risk > 0 else 0.0
        for bucket, key in ((weeks, _week_key(d)), (months, _month_key(d))):
            bucket[key]["closed"] += 1
            bucket[key]["wins"] += 1 if pnl > 0 else 0
            bucket[key]["losses"] += 1 if pnl < 0 else 0
            bucket[key]["realised_r"] += rr

    week_rows = [{"period": k, **v} for k, v in sorted(weeks.items())[-8:]]
    month_rows = [{"period": k, **v} for k, v in sorted(months.items())[-8:]]
    return {
        "ok": bool(snap.get("ok", True)),
        "scope": "LIVE_XAU_LONG_SHORT_ONLY",
        "current_open_upl_gbp": _sf(snap.get("current_open_upl_gbp")),
        "periods": snap.get("periods") or {},
        "weekly": week_rows,
        "monthly": month_rows,
        "time_utc": datetime.now(timezone.utc).isoformat(),
    }


@core.app.get("/api/live-xau-accounting-full")
def live_xau_accounting_full() -> Dict[str, Any]:
    try:
        return _aggregate_live_accounting()
    except Exception as exc:
        return {"ok": False, "scope": "LIVE_XAU_LONG_SHORT_ONLY", "error": f"{type(exc).__name__}: {exc}"}


@core.app.get("/api/live-xau-protection-v2")
def live_xau_protection_v2() -> Dict[str, Any]:
    data = dict(base.live_xau_basket_manager() or {})
    data["strict_reset_result"] = STRICT_RESET_RESULT
    data["scope"] = "LIVE_XAU_LONG_SHORT_ONLY"
    return data


_base_rewrite = analysis._rewrite_dashboard_version

_STRICT_UI = r'''
<style id="pep-metals-strict-v2-style">
  .pep-v2-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:9px;margin:10px 0}
  .pep-v2-card{border:1px solid rgba(255,255,255,.11);border-radius:9px;padding:9px 10px}
  .pep-v2-k{opacity:.68;font-size:.74rem;text-transform:uppercase;letter-spacing:.04em}
  .pep-v2-v{font-size:1.05rem;font-weight:760;margin-top:3px}
  .pep-v2-table{width:100%;border-collapse:collapse;font-size:.84rem;margin-top:9px}
  .pep-v2-table th,.pep-v2-table td{padding:6px 7px;border-bottom:1px solid rgba(255,255,255,.08);text-align:left}
  .pep-v2-note{opacity:.78;margin:8px 0}
  .pep-v2-live{border-left:4px solid rgba(70,210,130,.65)}
  .pep-v2-demo{border-left:4px solid rgba(230,170,65,.65)}
</style>
<script id="pep-metals-strict-v2-script">
(function(){
  function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
  function money(x){var n=Number(x||0);return (n<0?'-':'')+'£'+Math.abs(n).toFixed(2);}
  function num(x,d){var n=Number(x||0);return n.toFixed(d==null?2:d);}
  function card(k,v){return '<div class="pep-v2-card"><div class="pep-v2-k">'+k+'</div><div class="pep-v2-v">'+v+'</div></div>';}
  function summaries(){return Array.prototype.slice.call(document.querySelectorAll('details > summary'));}
  function findSummaryContains(txt){txt=txt.toLowerCase();return summaries().find(function(s){return norm(s.textContent).toLowerCase().indexOf(txt)!==-1;});}
  function detailsForText(txt){var s=findSummaryContains(txt);return s?s.parentElement:null;}
  function combined(){return document.getElementById('pep-broker-accounting-combined')||detailsForText('broker / oanda / accounting');}
  function nestedLive(){var c=combined();if(!c)return null;var ns=c.querySelectorAll('.pep-nested');return ns.length?ns[0]:null;}
  function nestedDemo(){var c=combined();if(!c)return null;var ns=c.querySelectorAll('.pep-nested');return ns.length?ns[ns.length-1]:null;}
  function bodyOf(d){return d?(d.querySelector('.pep-nested-body')||d.querySelector('.pep-body')||d):null;}

  function removeStalePilot(){
    Array.prototype.slice.call(document.querySelectorAll('body *')).forEach(function(el){
      var t=norm(el.textContent).toLowerCase();
      if(t==='xau long — live pilot'||t==='xau long - live pilot'||t.indexOf('xau long — live pilot. only xau_usd long is live')===0){
        var p=el.closest('details,section,.card,.panel,.section');
        if(p && p.id!=='pep-live-xau-ops-v2') p.style.display='none';
      }
    });
  }

  function renderLiveAccounting(){
    var live=nestedLive();if(!live)return false;
    var s=live.querySelector('summary');if(s)s.textContent='XAU — LIVE';
    var b=bodyOf(live);if(!b)return false;
    if(!document.getElementById('pep-live-xau-ops-v2')){
      b.innerHTML='<div id="pep-live-xau-ops-v2" class="pep-v2-live"><div class="pep-v2-note"><strong>LIVE XAU LONG + XAU SHORT.</strong> XAG is excluded and remains demo/research.</div><div id="pep-live-xau-accounting-v2">Loading live XAU accounting…</div><div id="pep-live-xau-protection-v2-ui">Loading live XAU basket protection…</div></div>';
    }
    fetch('/api/live-xau-accounting-full',{cache:'no-store'}).then(function(r){return r.json();}).then(function(x){
      var host=document.getElementById('pep-live-xau-accounting-v2');if(!host)return;
      if(!x||!x.ok){host.innerHTML='<div>Live accounting unavailable.</div>';return;}
      var p=x.periods||{},w=p.week||{},m=p.month||{},a=p.all_time||{};
      var html='<h3>Live Weekly / Monthly Profit Accounting</h3><div class="pep-v2-grid">';
      html+=card('This Week Realised',money(w.net_realized_gbp));html+=card('This Month Realised',money(m.net_realized_gbp));
      html+=card('All-Time Live Realised',money(a.net_realized_gbp));html+=card('Current Live Open P/L',money(x.current_open_upl_gbp));
      html+='</div>';
      function table(title,rows){var h='<h4>'+title+'</h4><table class="pep-v2-table"><thead><tr><th>Period</th><th>Net Realised</th><th>Trade P/L</th><th>Financing</th><th>Realised R</th><th>Closed</th><th>W/L</th></tr></thead><tbody>';
        (rows||[]).forEach(function(r){h+='<tr><td>'+r.period+'</td><td>'+money(r.net_realised)+'</td><td>'+money(r.trade_pl)+'</td><td>'+money(r.financing)+'</td><td>'+num(r.realised_r,2)+'R</td><td>'+r.closed+'</td><td>'+r.wins+'/'+r.losses+'</td></tr>';});return h+'</tbody></table>';}
      html+=table('Recent Live Weekly Accounting',x.weekly);html+=table('Recent Live Monthly Accounting',x.monthly);host.innerHTML=html;
    });
    fetch('/api/live-xau-protection-v2',{cache:'no-store'}).then(function(r){return r.json();}).then(function(x){
      var host=document.getElementById('pep-live-xau-protection-v2-ui');if(!host)return;
      if(!x||!x.ok){host.innerHTML='<div>Live protection state unavailable.</div>';return;}
      var html='<h3>Live Basket Manager / Profit Protection</h3><div class="pep-v2-note"><strong>LIVE XAU ONLY.</strong> HWM and harvest stages are isolated from all demo history.</div><div class="pep-v2-grid">';
      html+=card('Open XAU',x.open_count);html+=card('Long / Short',x.long_count+' / '+x.short_count);html+=card('Current R',num(x.current_r,2)+'R');
      html+=card('Live HWM',num(x.high_water_r,2)+'R');html+=card('Giveback',num(x.giveback_r,2)+'R · '+num(x.giveback_pct,1)+'%');
      html+=card('Manager',x.manager_enabled?'ON':'OFF');html+=card('Profit Protection',x.profit_protection_execution_enabled?'ON':'OFF');
      html+='</div><table class="pep-v2-table"><tbody><tr><th>Active live cycle</th><td>'+(x.active_cycle_id||'—')+'</td></tr><tr><th>Reset at</th><td>'+(x.reset_at||'—')+'</td></tr><tr><th>Reconciliation</th><td>linked '+((x.reconciliation&&x.reconciliation.linked_r_count)||0)+' · unlinked '+((x.reconciliation&&x.reconciliation.unlinked_broker_count)||0)+'</td></tr></tbody></table>';
      var stages=(x.recent_protection_stages||[]).filter(function(s){return !x.active_cycle_id||s.cycle_id===x.active_cycle_id;});
      if(stages.length){html+='<h4>Current Live Harvest Ladder</h4><table class="pep-v2-table"><thead><tr><th>Level</th><th>Status</th><th>Bank</th><th>Updated</th></tr></thead><tbody>';stages.forEach(function(s){html+='<tr><td>'+s.threshold_r+'R</td><td>'+s.status+'</td><td>'+Math.round(Number(s.bank_fraction||0)*100)+'%</td><td>'+(s.updated_at_utc||'')+'</td></tr>';});html+='</tbody></table>';}
      host.innerHTML=html;
    });
    live.open=false;return true;
  }

  function moveAllDemoProtection(){
    var demo=nestedDemo();if(!demo)return false;var db=bodyOf(demo);if(!db)return false;
    var wrap=document.getElementById('pep-demo-protection-v2');
    if(!wrap){wrap=document.createElement('details');wrap.id='pep-demo-protection-v2';wrap.className='pep-v2-demo';wrap.innerHTML='<summary>Demo Basket Manager / Profit Protection</summary><div class="pep-demo-protection-body"></div>';db.appendChild(wrap);wrap.open=false;}
    var target=wrap.querySelector('.pep-demo-protection-body');
    summaries().forEach(function(s){var t=norm(s.textContent).toLowerCase(),d=s.parentElement;if(!d||d===wrap||d.closest('#pep-live-xau-ops-v2'))return;
      if(t.indexOf('basket manager / profit protection')!==-1||t.indexOf('profit harvesting / metals protection')!==-1||t.indexOf('metals basket manager')!==-1){target.appendChild(d);d.open=false;}}
    );
    return true;
  }

  function fixWords(){
    document.body.innerHTML=document.body.innerHTML
      .replace(/XAU LONG — LIVE PILOT/g,'XAU — LIVE')
      .replace(/XAU LONG - LIVE PILOT/g,'XAU — LIVE')
      .replace(/Only XAU_USD LONG is live\. XAU SHORT and XAG LONG\/SHORT remain practice\./g,'XAU LONG and XAU SHORT are live. XAG LONG/SHORT remain demo/research.')
      .replace(/Open XAU LONG Live Trades/g,'Open XAU Live Trades');
  }

  function collapse(){document.querySelectorAll('details').forEach(function(d){d.open=false;});}
  function apply(){removeStalePilot();renderLiveAccounting();moveAllDemoProtection();collapse();}
  var n=0,t=setInterval(function(){n++;apply();if(n>35)clearInterval(t);},300);apply();
})();
</script>
'''


def _rewrite_dashboard_version(body: bytes, content_type: str) -> bytes:
    body = _base_rewrite(body, content_type)
    if "text/html" not in (content_type or "").lower():
        return body
    try:
        text = body.decode("utf-8")
        replacements = (
            ("XAU LONG — LIVE PILOT", "XAU — LIVE"),
            ("XAU LONG - LIVE PILOT", "XAU — LIVE"),
            ("Only XAU_USD LONG is live. XAU SHORT and XAG LONG/SHORT remain practice.", "XAU LONG and XAU SHORT are live. XAG LONG/SHORT remain demo/research."),
            ("Open XAU LONG Live Trades", "Open XAU Live Trades"),
        )
        for old, new in replacements:
            text = text.replace(old, new)
        if "pep-metals-strict-v2-script" not in text:
            text = text.replace("</body>", _STRICT_UI + "\n</body>", 1) if "</body>" in text else text + _STRICT_UI
        return text.encode("utf-8")
    except Exception:
        return body

analysis._rewrite_dashboard_version = _rewrite_dashboard_version
