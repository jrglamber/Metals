"""Operator XAU manual reset v2: atomic stacking, HWM and harvest boundary.

Replaces only the EXISTING manual reset POST handler, leaving trading, broker
orders, individual stops/MFE, and the separate practice-family HWM untouched.
Includes an explicit one-time alignment for pre-v2 manual resets.
"""
from __future__ import annotations

import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from fastapi import Header, HTTPException, Request

import metals_adaptive_stacking_override as adaptive
import metals_xau_manual_campaign_reset as old
import metals_xau_manual_highwater_reset as hwm

VERSION = "metals_xau_manual_campaign_hwm_sync_v2_2026_11_12"
ROUTE = "/api/xau/adaptive-campaign-reset/reset"
_INSTALLED = False


def preview(core: Any, side: str) -> Dict[str, Any]:
    old_check = old._preview(core, side)
    rows = adaptive._open_links(core)
    broker = core.metals_xau_live_broker_snapshot(include_account_summary=False)
    proof = hwm.baseline(core, broker, rows, side)
    cycle = str(old_check.get("live_harvest_cycle_id") or "")
    with core.get_conn() as conn:
        last_at = str(core._metals_xau_live_runtime_get(conn, old._key(side, "reset_at"), "") or "")
        last_cycle = str(core._metals_xau_live_runtime_get(conn, old._key(side, "cycle_id"), "") or "")
        aligned_at = str(core._metals_xau_live_runtime_get(conn, old._key(side, "hwm_aligned_at"), "") or "")
        harvest = hwm.pending_harvest_guard(conn, cycle) if cycle else {"ok": False}
    reset_epoch = adaptive._parse_dt(last_at)
    count = int(old_check.get("current_campaign_entry_count") or 0)
    align_allowed = bool(
        old_check.get("broker_links_exact") and proof.get("ok") and harvest.get("ok")
        and cycle and last_cycle == cycle and reset_epoch is not None
        and timedelta(0) <= datetime.now(timezone.utc)-reset_epoch <= timedelta(hours=24)
        # A few new entries can occur between the operator's original
        # stacking reset and this HWM alignment. Preserve those entries in
        # the adaptive counter rather than granting a second free allowance.
        and not aligned_at and count <= 2
    )
    if not proof.get("ok"):
        extra = [proof.get("reason") or "xau_highwater_not_reconcilable"]
    elif not harvest.get("ok"):
        extra = ["xau_harvest_has_pending_closes_or_partial_stages"]
    else:
        extra = []
    can_reset = bool(old_check.get("allowed") and not extra)
    return {
        **old_check, "allowed":can_reset,
        "alignment_allowed":align_allowed,"latest_reset_at":last_at if align_allowed else "",
        "baseline":proof if proof.get("ok") else None,
        "extra_blockers":extra,
        "new_harvest_checkpoint_r":30,
        "highwater_reset_included":True,"shared_hwm_needs_same_side_only":True,
        "scope":"XAU_LIVE_ADAPTIVE_ENTRY_COUNT_HIGHWATER_HARVEST",
        "version":VERSION
    }


def install(core: Any, app: Any) -> Dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"installed":True,"version":VERSION}
    hwm.install_hwm_lock(core)

    # The harvest cycle changes at a combined HWM reset. The count must stay
    # anchored to the OPERATOR's prior stacking reset time, even for a
    # later one-time HWM alignment. Otherwise new entries since that reset
    # are accidentally forgotten and the free 20-entry allowance expands.
    previous_count = adaptive._campaign_entry_count
    def campaign_count_with_aligned_epoch(c: Any, side: str, open_rows: list) -> Dict[str, Any]:
        value = dict(previous_count(c, side, open_rows))
        cycle = str(value.get("cycle_id") or "")
        if not cycle or side not in ("short", "long"):
            return value
        with c.get_conn() as conn:
            aligned_cycle = str(c._metals_xau_live_runtime_get(
                conn, old._key(side, "hwm_aligned_cycle_id"), "") or "")
            reset_at = str(c._metals_xau_live_runtime_get(
                conn, old._key(side, "reset_at"), "") or "")
        start = adaptive._parse_dt(reset_at)
        if aligned_cycle == cycle and start is not None:
            value["count"] = old._count_after_epoch(c,side,cycle,start)
            value["manual_reset_active"] = True
            value["manual_reset_at"] = reset_at
            value["effective_campaign_start"] = reset_at
            value["aligned_hwm_cycle_id"] = cycle
        return value
    adaptive._campaign_entry_count = campaign_count_with_aligned_epoch

    def get_preview(side: str = "short") -> Dict[str, Any]:
        side = side.strip().lower()
        if side not in ("short","long"):
            raise HTTPException(status_code=400,detail="Invalid XAU direction")
        return preview(core,side)

    async def reset(request: Request, x_webhook_secret: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        expected = str(getattr(core,"WEBHOOK_SECRET","") or "")
        if not expected or not x_webhook_secret or not hmac.compare_digest(str(x_webhook_secret),expected):
            raise HTTPException(status_code=401,detail="Invalid WEBHOOK_SECRET")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400,detail="Invalid JSON")
        if not isinstance(body,dict):
            raise HTTPException(status_code=400,detail="Invalid JSON object")
        side = str(body.get("side") or "").strip().lower()
        align_only = body.get("align_previous_hwm_only") is True
        if side not in ("short","long") or body.get("confirm")!=old.RESET_CONFIRM:
            raise HTTPException(status_code=400,detail="Explicit confirmation and direction required")
        if body.get("acknowledge_recent_results") is not True:
            raise HTTPException(status_code=400,detail="Acknowledge recent realised results first")
        harvest_lock = getattr(core,"_METALS_XAU_LIVE_HARVEST_LOCK",None)
        if harvest_lock is None or not harvest_lock.acquire(blocking=False):
            raise HTTPException(status_code=409,detail="XAU harvest worker busy; no reset applied")
        try:
            with hwm._HWM_LOCK:
                check = preview(core,side)
                eligible = check.get("alignment_allowed") if align_only else check.get("allowed")
                if not eligible:
                    raise HTTPException(status_code=409,detail={
                        "reason":"xau_reset_not_eligible","alignment_only":align_only,
                        "blockers":check.get("blocking_reasons",[])+check.get("extra_blockers",[]),
                        "alignment_allowed":check.get("alignment_allowed"),
                    })
                ids=[str(r.get("broker_trade_id") or "") for r in adaptive._open_links(core)]
                outcome = hwm.perform_rebase(
                    core,side,align_only=align_only,
                    expected_cycle_id=check["live_harvest_cycle_id"],
                    expected_open_ids=ids,
                    manual_reset_at=check.get("latest_reset_at") or "",
                )
                if not outcome.get("ok"):
                    raise HTTPException(status_code=409,detail=outcome)
                if not align_only:
                    # The atomic HWM transaction has already recorded the
                    # new adaptive epoch and archived the old cycle.
                    with core.get_conn() as conn:
                        stamp=outcome["reset_at_utc"]
                        conn.execute(
                            "INSERT INTO metals_xau_live_execution_audit "
                            "(created_at_utc,raw_signal_id,action,status,message,raw_json) "
                            "VALUES(?,?,?,?,?,?)",
                            (stamp,None,"manual_adaptive_campaign_reset","CONFIRMED",
                             "Manual XAU "+side+" stacking/HWM/harvest cycle reset",
                             json.dumps({
                                 "reset_scope":VERSION,
                                 "previous_campaign_entries":check["current_campaign_entry_count"],
                                 "old_hwm_r":outcome["previous"]["r"],
                                 "new_hwm_r":outcome["baseline"]["new_hwm_r"],
                                 "old_cycle":outcome["previous"]["cycle_id"],
                                 "new_cycle":outcome["new_cycle_id"],
                                 "no_broker_orders":True,
                             },default=str)),
                        )
                        conn.commit()
                after=adaptive._campaign_entry_count(core,side,adaptive._open_links(core))
        finally:
            harvest_lock.release()
        return {
            "ok":True,"status":("PREVIOUS_XAU_HWM_ALIGNED" if align_only else "XAU_CAMPAIGN_HWM_RESET"),
            "side":side,"old_count":check["current_campaign_entry_count"],
            "new_count":after.get("count"),"old_hwm_r":outcome["previous"]["r"],
            "new_hwm_r":outcome["baseline"]["new_hwm_r"],
            "old_hwm_gbp":outcome["previous"]["gbp"],
            "new_hwm_gbp":outcome["baseline"]["new_hwm_gbp"],
            "new_cycle_id":outcome["new_cycle_id"],"next_harvest_r":30,
            "previous_executed_harvest_preserved":True,
            "broker_orders_sent":False,"existing_trade_stops_and_mfe_unchanged":True,
        }

    # Front-route shadowing, ahead of the old POST, with no route deletion:
    # legacy dashboards/links still use the same URI but now invoke v2.
    app.add_api_route(ROUTE,reset,methods=["POST"],include_in_schema=False)
    route=next((r for r in reversed(app.router.routes)
                if getattr(r,"path","")==ROUTE and getattr(r,"endpoint",None) is reset),None)
    if route is None:
        raise RuntimeError("XAU combined reset route registration failed")
    app.router.routes.remove(route);app.router.routes.insert(0,route)
    app.add_api_route("/api/xau/adaptive-campaign-reset/v2/preview",get_preview,methods=["GET"],include_in_schema=False)
    pv=next((r for r in reversed(app.router.routes)
             if getattr(r,"path","")=="/api/xau/adaptive-campaign-reset/v2/preview"),None)
    if pv is None:
        raise RuntimeError("XAU combined reset preview route registration failed")
    app.router.routes.remove(pv);app.router.routes.insert(0,pv)
    old.DASHBOARD_SNIPPET = DASHBOARD_SNIPPET
    _INSTALLED=True
    return {"installed":True,"version":VERSION,"route_count":2,
            "hwm_and_harvest_reset":True,"retroactive_align_available":True,
            "broker_order_writes":False,"practice_hwm_untouched":True}


DASHBOARD_SNIPPET = r"""
<details id="pep-xau-campaign-reset" style="margin:10px 0">
 <summary>Manual XAU Campaign Reset — stacking + high-water</summary>
 <div class="section-note">
 Start a fresh adaptive entry allowance, reset the <strong>live XAU basket high-water</strong> to the
 current OANDA P&amp;L baseline, and restart the <strong>30R XAU harvest ladder</strong>.
 Old HWM and banked harvest history are archived. Existing trades, stops,
 individual MFE/exit managers, realised P&amp;L, and practice/XAG HWM stay unchanged.
 Only 1–2 open positions in the selected direction, with exact OANDA reconciliation.
 </div>
 <div style="padding:12px">
 <label for="pep-xau-reset-side">Direction</label>
 <select id="pep-xau-reset-side" style="background:#161b22;color:#f3f4f6;border:1px solid #57606a;border-radius:7px;padding:8px">
 <option value="short">XAU short</option><option value="long">XAU long</option>
 </select>
 <button type="button" id="pep-xau-reset-refresh">Check eligibility</button>
 <div id="pep-xau-reset-preview" class="section-note" style="margin-top:10px">Check live XAU eligibility.</div>
 <button type="button" id="pep-xau-reset-apply" disabled style="padding:10px;margin-top:10px;background:#263e2b;color:#ecffed;border:1px solid #417a48;border-radius:7px">Reset campaign + high-water</button>
 <button type="button" id="pep-xau-reset-align" disabled style="padding:10px;margin-top:10px;background:#24283e;color:#f1ecff;border:1px solid #6668a1;border-radius:7px">Align previous reset's high-water</button>
 <div id="pep-xau-reset-result" class="section-note" style="margin-top:10px;display:none"></div>
 </div>
</details>
<script>
(function(){
 const host=document.getElementById('pep-xau-campaign-reset');
 if(!host||host.dataset.wired)return;host.dataset.wired='1';
 const side=document.getElementById('pep-xau-reset-side');
 const check=document.getElementById('pep-xau-reset-preview');
 const apply=document.getElementById('pep-xau-reset-apply');
 const align=document.getElementById('pep-xau-reset-align');
 const result=document.getElementById('pep-xau-reset-result');
 let last=null;
 function show(el,txt){el.textContent=txt;el.style.display='block';el.style.whiteSpace='pre-wrap';}
 async function refresh(){
   apply.disabled=true;align.disabled=true;last=null;
   show(check,'Reading live OANDA positions, high-water and harvest safety…');
   try{
     const r=await fetch('/api/xau/adaptive-campaign-reset/v2/preview?side='+encodeURIComponent(side.value),{cache:'no-store'});
     const d=await r.json();
     if(!r.ok)throw Error(d.detail||'Preview failed');
     last=d;
     const base=d.baseline||{}, recent=d.recent_closed||{};
     let msg='Entries since last reset: '+d.current_campaign_entry_count+
       ' | Open live XAU: '+d.open_xau_total+' | Exact reconciliation: '+(d.broker_links_exact?'YES':'NO')+
       '.\nRebase HWM to '+(base.ok?'£'+Number(base.new_hwm_gbp).toFixed(2)+' / '+Number(base.new_hwm_r).toFixed(2)+'R':'UNAVAILABLE')+
       '. New XAU harvest from 30R.\nLast '+recent.days+' days known realised '+Number(recent.realized_r_known||0).toFixed(2)+'R'+(recent.complete?'':' (INCOMPLETE)')+'.\n';
     if(recent.warning)msg+='WARNING: recent losses or incomplete realised data.\n';
     if(d.allowed)msg+='New combined reset available.\n';
     if(d.alignment_allowed)msg+='Previously completed stacking reset detected: one-time HWM alignment AVAILABLE.\n';
     if(!d.allowed&&!d.alignment_allowed)msg+='RESET BLOCKED: '+(d.blocking_reasons||[]).concat(d.extra_blockers||[]).join(', ');
     show(check,msg);apply.disabled=!d.allowed;align.disabled=!d.alignment_allowed;
   }catch(e){show(check,'Preview unavailable: '+(e.message||e));}
 }
 async function submit(alignOnly){
   if(!last || !(alignOnly?last.alignment_allowed:last.allowed))return;
   const msg=(alignOnly?'Align high-water for the manual XAU reset you already completed?':
     'Start a fresh XAU campaign with new high-water and harvest cycle?')+
     '\n\nPrevious HWM will be archived, new HWM is current OANDA P&L (0 minimum).'+
     '\nNew harvest checkpoints begin at 30R. Historical banked P&L remains.'+
     '\nExisting broker trades, stops and individual MFE/exit history stay unchanged.\nProceed?';
   if(!window.confirm(msg))return;
   const secret=window.prompt('Enter WEBHOOK_SECRET to authorize this XAU cycle reset:');
   if(!secret)return;
   apply.disabled=true;align.disabled=true;show(result,'Checking broker exposure and saving the new cycle…');
   try{
     const r=await fetch('/api/xau/adaptive-campaign-reset/reset',{
       method:'POST',headers:{'content-type':'application/json','x-webhook-secret':secret},
       body:JSON.stringify({side:side.value,confirm:'RESET_XAU_ADAPTIVE_CAMPAIGN',
         acknowledge_recent_results:true,align_previous_hwm_only:alignOnly}),cache:'no-store'
     });
     const d=await r.json();
     if(!r.ok||!d.ok)throw Error(JSON.stringify(d.detail||d));
     show(result,'XAU '+side.value.toUpperCase()+' reset confirmed. HWM '+
       Number(d.old_hwm_r).toFixed(2)+'R → '+Number(d.new_hwm_r).toFixed(2)+
       'R; entry count '+d.old_count+' → '+d.new_count+
       '; next harvest 30R. Broker positions and stops unchanged.');
     await refresh();
   }catch(e){show(result,'RESET NOT APPLIED: '+(e.message||e));await refresh();}
 }
 side.addEventListener('change',refresh);
 document.getElementById('pep-xau-reset-refresh').addEventListener('click',refresh);
 host.addEventListener('toggle',()=>{if(host.open)refresh()});
 apply.addEventListener('click',()=>submit(false));
 align.addEventListener('click',()=>submit(true));
})();
</script>
""";
