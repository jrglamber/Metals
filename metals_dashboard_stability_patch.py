"""Presentation-only Metals dashboard stability + full XAU ladder view.

Keeps the stable one-shot dashboard behaviour and adds a read-only, serialised
XAU cash-banking ladder so the Metals front end mirrors the richer BCO/Indices
protection sections. Trading, broker and protection execution logic are untouched.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PATCH_VERSION = "metals_dashboard_stability_v2_2026_10_08"
_LADDER_ROUTE = "/api/live-xau-harvest-ladder-view"


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _si(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _row_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _pick(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _level(row: Dict[str, Any]) -> Optional[float]:
    value = _pick(row, "threshold_r", "level_r", "trigger_r", "harvest_level_r", "stage_r")
    if value is None:
        return None
    try:
        return round(float(value), 8)
    except Exception:
        return None


def _same_level(a: Optional[float], b: Optional[float]) -> bool:
    return a is not None and b is not None and abs(a - b) < 1e-7


def _pct(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        x = float(value)
    except Exception:
        return None
    if abs(x) <= 1.0000001:
        x *= 100.0
    return x


def _cash(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _trade_ids(value: Any) -> List[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, dict):
        return [str(v) for v in value.values() if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    text = text.strip("[]")
    parts = [p.strip().strip("'\"") for p in text.replace(";", ",").split(",")]
    return [p for p in parts if p]


def install(analysis: Any) -> Dict[str, Any]:
    core = analysis.core

    # Read-only dashboard endpoint. It serialises the configured ladder even when
    # no stage has armed yet, then overlays the current-cycle persisted stage/event
    # records. It never calls OANDA and has no execution authority.
    existing_paths = {getattr(r, "path", None) for r in getattr(core.app, "routes", [])}
    if _LADDER_ROUTE not in existing_paths:
        @core.app.get(_LADDER_ROUTE)
        def live_xau_harvest_ladder_view() -> Dict[str, Any]:
            try:
                first_trigger = _sf(getattr(core, "METALS_XAU_LIVE_HARVEST_FIRST_TRIGGER_R", 0.0))
                next_trigger = _sf(getattr(core, "METALS_XAU_LIVE_HARVEST_NEXT_TRIGGER_R", 0.0))
                first_fraction = _sf(getattr(core, "METALS_XAU_LIVE_HARVEST_FIRST_BANK_FRACTION", 0.0))
                next_fraction = _sf(getattr(core, "METALS_XAU_LIVE_HARVEST_NEXT_BANK_FRACTION", first_fraction))
                max_stages = _si(getattr(core, "METALS_XAU_LIVE_HARVEST_MAX_STAGES", 0))

                with core.get_conn() as conn:
                    active_cycle = str(core._metals_xau_live_runtime_get(conn, "active_harvest_cycle_id", "") or "")
                    stage_rows = [_row_dict(r) for r in conn.execute(
                        "SELECT * FROM metals_xau_live_harvest_stages ORDER BY id DESC LIMIT 200"
                    ).fetchall()]
                    event_rows = [_row_dict(r) for r in conn.execute(
                        "SELECT * FROM metals_xau_live_harvest_events ORDER BY id DESC LIMIT 200"
                    ).fetchall()]

                # Prefer rows from the active live cycle when the schema carries a cycle id.
                current_stages = [
                    r for r in stage_rows
                    if not active_cycle or not r.get("cycle_id") or str(r.get("cycle_id")) == active_cycle
                ]
                current_events = [
                    r for r in event_rows
                    if not active_cycle or not r.get("cycle_id") or str(r.get("cycle_id")) == active_cycle
                ]

                observed_levels = sorted({x for x in (_level(r) for r in current_stages) if x is not None})
                configured_levels: List[float] = []
                if first_trigger > 0 and max_stages > 0:
                    configured_levels = [
                        round(first_trigger + (i * next_trigger), 8)
                        for i in range(max_stages)
                        if first_trigger + (i * next_trigger) > 0
                    ]
                levels = sorted(set(configured_levels + observed_levels))

                ladder: List[Dict[str, Any]] = []
                for idx, lvl in enumerate(levels):
                    stage = next((r for r in current_stages if _same_level(_level(r), lvl)), {})
                    event = next((r for r in current_events if _same_level(_level(r), lvl)), {})

                    configured_fraction = first_fraction if idx == 0 else next_fraction
                    bank_pct = _pct(_pick(stage, "bank_fraction", "bank_pct", "bank_percent"))
                    if bank_pct is None:
                        bank_pct = _pct(configured_fraction)

                    target_cash = _cash(_pick(
                        stage,
                        "target_at_trigger_gbp", "target_bank_gbp", "target_gbp",
                        "target_bank_pnl_gbp", "frozen_target_gbp", "bank_target_gbp",
                    ))
                    if target_cash is None:
                        target_cash = _cash(_pick(
                            event,
                            "target_at_trigger_gbp", "target_bank_gbp", "target_gbp",
                            "target_bank_pnl_gbp", "frozen_target_gbp", "bank_target_gbp",
                        ))

                    actual_cash = _cash(_pick(
                        event,
                        "actual_gbp_banked", "actual_banked_gbp", "banked_gbp",
                        "actual_cash_banked_gbp", "net_realized_gbp", "net_realised_gbp",
                        "realized_gbp", "realised_gbp",
                    ))
                    if actual_cash is None:
                        actual_cash = _cash(_pick(
                            stage,
                            "actual_gbp_banked", "actual_banked_gbp", "banked_gbp",
                            "actual_cash_banked_gbp", "net_realized_gbp", "net_realised_gbp",
                        ))

                    actual_pct = _pct(_pick(
                        event,
                        "actually_banked_fraction", "actual_banked_fraction", "banked_fraction",
                        "actual_bank_fraction", "actual_banked_pct", "banked_pct",
                    ))
                    if actual_pct is None:
                        actual_pct = _pct(_pick(
                            stage,
                            "actually_banked_fraction", "actual_banked_fraction", "banked_fraction",
                            "actual_bank_fraction", "actual_banked_pct", "banked_pct",
                        ))

                    executed_at = _pick(
                        event,
                        "executed_at_utc", "executed_at", "execution_time_utc", "closed_at_utc",
                        "created_at_utc", "updated_at_utc",
                    ) or _pick(stage, "executed_at_utc", "executed_at")

                    ids = _trade_ids(_pick(
                        event,
                        "trade_ids", "broker_trade_ids", "selected_trade_ids", "closed_trade_ids",
                    ))
                    if not ids:
                        ids = _trade_ids(_pick(stage, "trade_ids", "broker_trade_ids", "selected_trade_ids"))

                    status = str(_pick(stage, "status") or "NOT_ARMED")
                    terminalish = status.upper() in {
                        "EXECUTED", "COMPLETED", "COMPLETE", "CONSUMED", "BANKED", "DONE"
                    }
                    if actual_pct is not None:
                        actually_banked: Any = round(actual_pct, 4)
                    elif actual_cash is not None or terminalish:
                        actually_banked = round(bank_pct or 0.0, 4)
                    else:
                        actually_banked = None

                    ladder.append({
                        "serial": idx + 1,
                        "level_r": lvl,
                        "status": status,
                        "bank_pct": bank_pct,
                        "target_at_trigger_gbp": target_cash,
                        "actually_banked_pct": actually_banked,
                        "actual_gbp_banked": actual_cash,
                        "executed_at": executed_at,
                        "trade_ids": ids,
                    })

                return {
                    "ok": True,
                    "scope": "LIVE_XAU_ONLY",
                    "active_cycle_id": active_cycle,
                    "ladder": ladder,
                    "configured": {
                        "first_trigger_r": first_trigger,
                        "next_trigger_r": next_trigger,
                        "max_stages": max_stages,
                        "first_bank_fraction": first_fraction,
                        "next_bank_fraction": next_fraction,
                    },
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "scope": "LIVE_XAU_ONLY",
                    "ladder": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }

    original = analysis._rewrite_dashboard_version

    ladder_ui = r'''
<style id="pep-metals-full-ladder-style">
  #pep-xau-cash-banking-ladder{margin:24px 0 4px}
  #pep-xau-cash-banking-ladder h2{margin:0 0 14px;font-size:1.55rem}
  #pep-xau-cash-banking-ladder .pep-ladder-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid rgba(255,255,255,.10);border-radius:8px}
  #pep-xau-cash-banking-ladder table{width:100%;min-width:860px;border-collapse:collapse;font-size:.92rem;margin:0}
  #pep-xau-cash-banking-ladder th,#pep-xau-cash-banking-ladder td{padding:10px 12px;border-bottom:1px solid rgba(255,255,255,.11);text-align:left;vertical-align:top;white-space:nowrap}
  #pep-xau-cash-banking-ladder th{font-weight:750;background:rgba(0,0,0,.16)}
  #pep-xau-cash-banking-ladder tr:last-child td{border-bottom:0}
  #pep-xau-cash-banking-ladder .pep-ladder-note{opacity:.76;font-size:.82rem;line-height:1.45;margin:10px 0 0}
  #pep-xau-cash-banking-ladder .pep-ladder-empty{padding:12px;opacity:.78}
</style>
<script id="pep-metals-full-ladder-script">
(function(){
  var fetched=false;
  function money(v){if(v===null||v===undefined||v==='')return 'n/a';var n=Number(v);if(!isFinite(n))return 'n/a';return (n<0?'-':'')+'£'+Math.abs(n).toFixed(2);}
  function pct(v){if(v===null||v===undefined||v==='')return '—';var n=Number(v);if(!isFinite(n))return '—';return n.toFixed(Math.abs(n-Math.round(n))<0.001?0:1)+'%';}
  function level(v){var n=Number(v);return isFinite(n)?n.toFixed(Math.abs(n-Math.round(n))<0.001?0:1)+'R':'—';}
  function text(v,wait){return (v===null||v===undefined||v==='')?(wait||'—'):String(v);}
  function hideLegacyStageTable(panel){
    Array.prototype.slice.call(panel.querySelectorAll('table')).forEach(function(t){
      var h=t.querySelector('th');
      if(h && /protection stage/i.test(h.textContent||'')) t.style.display='none';
    });
  }
  function render(panel,data){
    hideLegacyStageTable(panel);
    var old=document.getElementById('pep-xau-cash-banking-ladder'); if(old) old.remove();
    var host=document.createElement('div'); host.id='pep-xau-cash-banking-ladder';
    var rows=(data&&data.ladder)||[];
    var html='<h2>XAU Cash-Banking Ladder</h2>';
    if(!data||!data.ok){
      html+='<div class="pep-ladder-empty">Live XAU ladder state unavailable.</div>';
    }else if(!rows.length){
      html+='<div class="pep-ladder-empty">No configured live XAU cash-banking stages were returned.</div>';
    }else{
      html+='<div class="pep-ladder-scroll"><table><thead><tr><th>Level</th><th>Status</th><th>Bank %</th><th>Target at Trigger</th><th>Actually Banked</th><th>Actual £ Banked</th><th>Executed At</th><th>Trade IDs</th></tr></thead><tbody>';
      rows.forEach(function(r){
        var ids=(r.trade_ids||[]); var waiting=String(r.status||'').toUpperCase()==='NOT_ARMED';
        html+='<tr><td>'+level(r.level_r)+'</td><td>'+text(r.status,'NOT_ARMED')+'</td><td>'+pct(r.bank_pct)+'</td><td>'+(r.target_at_trigger_gbp==null?'—':money(r.target_at_trigger_gbp))+'</td><td>'+(r.actually_banked_pct==null?(waiting?'—':'waiting'):pct(r.actually_banked_pct))+'</td><td>'+(r.actual_gbp_banked==null?'n/a':money(r.actual_gbp_banked))+'</td><td>'+text(r.executed_at,'—')+'</td><td>'+(ids.length?ids.join(', '):(waiting?'waiting':'—'))+'</td></tr>';
      });
      html+='</tbody></table></div>';
      html+='<div class="pep-ladder-note"><strong>Actual £ Banked</strong> is populated from persisted live XAU harvest execution data. Untriggered stages remain visible so the complete configured ladder can be audited at a glance.</div>';
    }
    host.innerHTML=html;
    var body=panel.querySelector('.pep-body')||panel;
    body.appendChild(host);
  }
  function tryStart(){
    if(fetched)return true;
    var panel=document.getElementById('pep-live-basket-manager');
    if(!panel)return false;
    var body=panel.querySelector('.pep-body');
    if(!body || !body.querySelector('.pep-grid'))return false;
    fetched=true;
    fetch('/api/live-xau-harvest-ladder-view',{cache:'no-store'}).then(function(r){return r.json();}).then(function(x){render(panel,x);}).catch(function(){render(panel,{ok:false,ladder:[]});});
    return true;
  }
  var tries=0,t=setInterval(function(){tries++;if(tryStart()||tries>=60)clearInterval(t);},100);
  tryStart();
})();
</script>
'''

    final_placement = r'''
<script id="pep-metals-stable-protection-position">
(function(){
  var attempts=0;
  var timer=setInterval(function(){
    attempts++;
    var live=document.getElementById('pep-live-basket-manager');
    var broker=document.getElementById('pep-broker-accounting-combined');
    if(live && broker && broker.parentNode){
      var summary=live.querySelector(':scope > summary') || live.querySelector('summary');
      if(summary) summary.textContent='Basket Manager / Profit Protection';
      if(broker.nextSibling!==live) broker.parentNode.insertBefore(live,broker.nextSibling);
      live.open=false;
      var duplicate=document.getElementById('pep-live-xau-protection-v2-ui');
      if(duplicate) duplicate.style.display='none';
      clearInterval(timer);
    } else if(attempts>=50){
      clearInterval(timer);
    }
  },100);
})();
</script>
'''

    def stable_rewrite(body: bytes, content_type: str) -> bytes:
        body = original(body, content_type)
        if "text/html" not in (content_type or "").lower():
            return body
        try:
            text = body.decode("utf-8")

            text = text.replace(
                "function apply(){ renderLive(); moveLegacyPracticeBasketWidgets(); }",
                "var pepLiveBasketRendered=false; function apply(){ if(!pepLiveBasketRendered){ pepLiveBasketRendered=true; renderLive(); } moveLegacyPracticeBasketWidgets(); }",
            )
            text = text.replace(
                "if(!d||d===wrap||d.closest('#pep-live-xau-ops-v2'))return;",
                "if(!d||d===wrap||d.id==='pep-live-basket-manager'||d.closest('#pep-live-xau-ops-v2'))return;",
            )
            text = text.replace(
                "function apply(){removeStalePilot();renderLiveAccounting();moveAllDemoProtection();collapse();}",
                "var pepStrictRendered=false; function apply(){removeStalePilot();if(!pepStrictRendered){pepStrictRendered=renderLiveAccounting();}moveAllDemoProtection();collapse();}",
            )

            if "pep-metals-stable-protection-position" not in text:
                if "</body>" in text:
                    text = text.replace("</body>", final_placement + "\n</body>", 1)
                else:
                    text += final_placement
            if "pep-metals-full-ladder-script" not in text:
                if "</body>" in text:
                    text = text.replace("</body>", ladder_ui + "\n</body>", 1)
                else:
                    text += ladder_ui
            return text.encode("utf-8")
        except Exception:
            return body

    analysis._rewrite_dashboard_version = stable_rewrite
    status = {
        "ok": True,
        "version": PATCH_VERSION,
        "live_basket_fetch_once": True,
        "strict_accounting_fetch_once": True,
        "live_protection_excluded_from_demo_reparent": True,
        "top_level_order": "broker_then_basket_protection",
        "full_serialised_xau_ladder": True,
        "ladder_route_read_only": True,
        "execution_logic_changed": False,
    }
    print("METALS_DASHBOARD_STABILITY", status, flush=True)
    return status
