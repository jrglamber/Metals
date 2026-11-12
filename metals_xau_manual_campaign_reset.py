"""Manual, direction-specific XAU adaptive-stacking campaign reset.

A separate operator decision boundary. Only the adaptive entry counter changes:
the live XAU broker orders, open positions, stops, harvest cycle, HWM, trade
ages, exits, and actual historical P&L stay untouched. Fail closed if live
OANDA trade IDs and durable XAU links do not exactly reconcile.
"""
from __future__ import annotations

import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import Header, HTTPException, Request

import metals_adaptive_stacking_override as adaptive

VERSION = "metals_xau_manual_adaptive_campaign_reset_v1_2026_11_12"
MAX_OPEN_TOTAL = 2
RECENT_DAYS = 7
RESET_CONFIRM = "RESET_XAU_ADAPTIVE_CAMPAIGN"
RESET_PREFIX = "xau_adaptive_manual_campaign_"
_INSTALLED = False


def _key(side: str, suffix: str) -> str:
    return RESET_PREFIX + side + "_" + suffix


def _effective_anchor(core: Any, side: str, cycle_id: str, original_start: Optional[datetime]):
    """A manual reset is valid only for its original live harvest cycle."""
    if not cycle_id or original_start is None:
        return original_start, "", False
    with core.get_conn() as conn:
        stored_cycle = core._metals_xau_live_runtime_get(conn, _key(side, "cycle_id"), "")
        stored_at = core._metals_xau_live_runtime_get(conn, _key(side, "reset_at"), "")
    parsed = adaptive._parse_dt(stored_at)
    if stored_cycle == cycle_id and parsed is not None and parsed > original_start:
        return parsed, stored_at, True
    return original_start, "", False


def _count_after_epoch(core: Any, side: str, cycle_id: str, start: datetime) -> int:
    with core.get_conn() as conn:
        rows = conn.execute(
            "SELECT created_at_utc FROM metals_xau_live_trade_links "
            "WHERE LOWER(side)=? ORDER BY id", (side,)
        ).fetchall()
    return sum(
        1 for row in rows
        if (adaptive._parse_dt(row["created_at_utc"]) or datetime.min.replace(tzinfo=timezone.utc)) >= start
    )


def _recent_closed(core: Any, side: str) -> Dict[str, Any]:
    """Seven-day realised performance, link-level and explicitly non-authoritative."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=RECENT_DAYS)
    with core.get_conn() as conn:
        rows = [
            dict(r) for r in conn.execute(
                "SELECT id, side, closed_at_utc, realized_pl, estimated_risk_amount, "
                "requested_risk_amount FROM metals_xau_live_trade_links "
                "WHERE LOWER(side)=? AND UPPER(status) NOT IN ('OPEN', 'LINKED') "
                "AND closed_at_utc IS NOT NULL ORDER BY id DESC LIMIT 500",
                (side,)
            ).fetchall()
        ]
    relevant = [r for r in rows if (adaptive._parse_dt(r["closed_at_utc"]) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
    known_r, unknown, losing, known_cash = 0.0, 0, 0, 0.0
    for row in relevant:
        pnl = adaptive._safe_float(row.get("realized_pl"))
        risk = adaptive._risk_amount(row)
        if pnl is None or risk is None or risk <= 0:
            unknown += 1
            continue
        known_r += pnl / risk
        known_cash += pnl
        losing += int(pnl < 0)
    complete = (unknown == 0 and len(rows) < 500)
    return {
        "days": RECENT_DAYS,
        "closed_count": len(relevant),
        "known_count": len(relevant) - unknown,
        "losing_count": losing,
        "unknown_count": unknown,
        "realized_r_known": round(known_r, 3),
        "realized_cash_known_gbp": round(known_cash, 2),
        "complete": complete,
        "warning": bool(unknown or len(rows) >= 500 or known_r < 0),
        "substantial_loss": bool(known_r <= -2.0),
        "note": "Link-level estimates; incomplete amounts are not treated as zero.",
    }


def _preview(core: Any, side: str) -> Dict[str, Any]:
    if side not in ("long", "short"):
        raise HTTPException(status_code=400, detail="side must be long or short")
    core.init_db()
    cycle_id, cycle_start = adaptive._active_cycle(core)
    current_rows = adaptive._open_links(core)
    # Important: owned_open_trades on some historical builds contains LONG
    # trades only. all_xau_trades includes BOTH signed long and short positions.
    broker = core.metals_xau_live_broker_snapshot(include_account_summary=False)
    ok = broker.get("ok") is True
    all_trades = list(broker.get("all_xau_trades") or []) if ok else []
    live_ids = [str(t.get("id") or "") for t in all_trades]
    local_ids = [str(t.get("broker_trade_id") or "") for t in current_rows]
    exact = (
        ok and bool(cycle_id and cycle_start)
        and len(live_ids) == len(local_ids)
        and len(set(live_ids)) == len(live_ids)
        and len(set(local_ids)) == len(local_ids)
        and all(live_ids) and all(local_ids)
        and set(live_ids) == set(local_ids)
    )
    sides_match = True
    if exact:
        broker_by_id = {str(t.get("id")): t for t in all_trades}
        for row in current_rows:
            trade = broker_by_id[str(row["broker_trade_id"])]
            signed_units = adaptive._safe_float(trade.get("currentUnits"))
            reported_side = str(row.get("side") or "").lower()
            if signed_units is None or signed_units == 0 or (
                ("long" if signed_units > 0 else "short") != reported_side
            ):
                sides_match = False
                break
    exact = exact and sides_match
    current = adaptive._campaign_entry_count(core, side, current_rows)
    current_count = int(current.get("count") or 0)
    recent = _recent_closed(core, side)
    reasons = []
    if not ok:
        reasons.append("live_oanda_xau_snapshot_unavailable")
    if not cycle_id or cycle_start is None:
        reasons.append("live_xau_harvest_cycle_unavailable")
    if not exact:
        reasons.append("xau_broker_link_reconciliation_not_exact")
    if len(all_trades) > MAX_OPEN_TOTAL:
        reasons.append("xau_exposure_above_two_open_trades")
    if len(all_trades) == 0:
        reasons.append("basket_already_flat_no_manual_reset_needed")
    if current_count < adaptive.NORMAL_ALLOWANCE:
        reasons.append("adaptive_campaign_below_20_entries_no_reset_needed")
    return {
        "ok": bool(ok), "allowed": not reasons, "side": side,
        "current_campaign_entry_count": current_count,
        "normal_entry_allowance": adaptive.NORMAL_ALLOWANCE,
        "open_xau_total": len(all_trades), "open_xau_links": len(current_rows),
        "open_xau_same_side": sum(str(r.get("side") or "").lower() == side for r in current_rows),
        "broker_links_exact": bool(exact),
        "live_harvest_cycle_id": cycle_id,
        "manual_reset_active": bool(current.get("manual_reset_active")),
        "manual_reset_at": current.get("manual_reset_at") or "",
        "recent_closed": recent,
        "blocking_reasons": reasons,
        "version": VERSION,
        "scope": "XAU_ADAPTIVE_STACKING_ENTRY_ORDINAL_ONLY",
        "broker_writes": False,
        "harvest_and_highwater_unchanged": True,
    }


def install(core: Any, app: Any) -> Dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"installed": True, "version": VERSION, "route_count": 2}

    original_count = adaptive._campaign_entry_count

    def count_since_operator_boundary(c: Any, side: str, open_rows: list) -> Dict[str, Any]:
        base = dict(original_count(c, side, open_rows))
        cycle_id = str(base.get("cycle_id") or "")
        cycle_start = adaptive._parse_dt(base.get("cycle_start"))
        start, boundary_at, enabled = _effective_anchor(c, side, cycle_id, cycle_start)
        if not enabled or start is None:
            base["manual_reset_active"] = False
            base["manual_reset_at"] = ""
            return base
        base["count_before_manual_reset"] = base.get("count")
        base["count"] = _count_after_epoch(c, side, cycle_id, start)
        base["manual_reset_active"] = True
        base["manual_reset_at"] = boundary_at
        base["effective_campaign_start"] = start.isoformat()
        base["exact"] = True
        return base

    adaptive._campaign_entry_count = count_since_operator_boundary

    def preview(side: str = "short") -> Dict[str, Any]:
        return _preview(core, side.strip().lower())

    async def reset(request: Request, x_webhook_secret: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        expected = str(getattr(core, "WEBHOOK_SECRET", "") or "")
        provided = str(x_webhook_secret or "")
        if not expected or not provided or not hmac.compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail="Invalid WEBHOOK_SECRET")
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Invalid reset payload")
        side = str(body.get("side") or "").strip().lower()
        if body.get("confirm") != RESET_CONFIRM or side not in ("long", "short"):
            raise HTTPException(status_code=400, detail="Explicit confirmation and side required")
        if body.get("acknowledge_recent_results") is not True:
            raise HTTPException(status_code=400, detail="Review and acknowledge recent realised performance first")
        check = _preview(core, side)  # Fresh broker snapshot; no stale browser count trusted.
        if not check.get("allowed"):
            raise HTTPException(status_code=409, detail={
                "reason": "xau_adaptive_manual_reset_blocked",
                "blocking_reasons": check["blocking_reasons"],
                "preview": check,
            })
        when = core.now_utc_iso()
        cycle_id = check["live_harvest_cycle_id"]
        # Only dedicated manual stacking boundary keys are changed.
        with core.get_conn() as conn:
            # Detect a different live harvest cycle during the action.
            still = core._metals_xau_live_runtime_get(conn, "active_harvest_cycle_id", "")
            if still != cycle_id:
                raise HTTPException(status_code=409, detail="Live XAU harvest cycle changed during reset; retry preview")
            core._metals_xau_live_runtime_set(conn, _key(side, "cycle_id"), cycle_id)
            core._metals_xau_live_runtime_set(conn, _key(side, "reset_at"), when)
            audit = {
                "version": VERSION, "side": side, "time_utc": when,
                "prior_campaign_count": check["current_campaign_entry_count"],
                "open_xau_total": check["open_xau_total"],
                "open_xau_same_side": check["open_xau_same_side"],
                "live_harvest_cycle_id": cycle_id,
                "recent_closed": check["recent_closed"],
                "broker_links_exact": True, "broker_writes": False,
                "harvest_highwater_stops_unchanged": True,
            }
            conn.execute(
                "INSERT INTO metals_xau_live_execution_audit "
                "(created_at_utc, raw_signal_id, action, status, message, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (when, None, "manual_adaptive_campaign_reset", "CONFIRMED",
                 "Manual XAU " + side + " adaptive stacking entry-counter reset",
                 json.dumps(audit, default=str)),
            )
            conn.commit()
        after = adaptive._campaign_entry_count(core, side, adaptive._open_links(core))
        return {
            "ok": True, "status": "XAU_ADAPTIVE_CAMPAIGN_RESET",
            "side": side, "reset_at_utc": when,
            "prior_campaign_count": check["current_campaign_entry_count"],
            "new_campaign_count": after.get("count"),
            "new_campaign_manual_reset_active": after.get("manual_reset_active"),
            "open_xau_total": check["open_xau_total"],
            "live_harvest_cycle_id_unchanged": cycle_id,
            "historical_trades_and_realised_pnl_preserved": True,
            "broker_orders_sent": False, "stops_exits_hwm_harvest_unchanged": True,
            "audit": audit,
        }

    routes = app.router.routes
    registered = []
    for path, method, fn in (
        ("/api/xau/adaptive-campaign-reset/preview", "GET", preview),
        ("/api/xau/adaptive-campaign-reset/reset", "POST", reset),
    ):
        app.add_api_route(path, fn, methods=[method], include_in_schema=False)
        route = next((r for r in reversed(routes) if getattr(r, "path", "") == path
                      and getattr(r, "endpoint", None) is fn), None)
        if route is None:
            raise RuntimeError("XAU manual campaign reset route registration failed: " + path)
        routes.remove(route)
        routes.insert(0, route)  # Ahead of legacy catch-all.
        registered.append(path)
    _INSTALLED = True
    return {"installed": True, "version": VERSION, "route_count": len(registered),
            "routes": registered, "broker_writes": False,
            "entry_counter_reset_only": True, "confirmation_required": True}


# Appended only to the main Metals dashboard, NOT its existing family-cycle
# control. Uses existing dark-mode panel vocabulary. No secret saved in storage.
DASHBOARD_SNIPPET = r"""
<details id="pep-xau-campaign-reset" class="dashboard-group" style="margin:10px 0">
  <summary>Manual XAU Campaign Reset — stacking only</summary>
  <div class="section-note">
    Start a fresh XAU <strong>entry allowance</strong> for one direction after the
    previous campaign has mostly closed. This does <strong>not</strong> reset the
    Metals basket HWM, the live XAU harvest cycle, existing positions or stops.
    Available only with 1–2 total live XAU positions and exact OANDA reconciliation.
  </div>
  <div style="padding:12px">
    <label for="pep-xau-reset-side">Direction</label>
    <select id="pep-xau-reset-side" style="background:#161b22;color:#f3f4f6;border:1px solid #57606a;border-radius:7px;padding:8px">
      <option value="short">XAU short</option><option value="long">XAU long</option>
    </select>
    <button id="pep-xau-reset-refresh" type="button" style="padding:9px;background:#10263b;color:#e5f2ff;border:1px solid #3272a0;border-radius:7px">Check eligibility</button>
    <div id="pep-xau-reset-preview" class="section-note" style="margin-top:10px">Open this section to check current eligibility.</div>
    <button id="pep-xau-reset-apply" type="button" disabled style="padding:10px;margin-top:10px;border-radius:7px;background:#263e2b;color:#ecffed;border:1px solid #417a48;font-weight:700">Reset XAU stacking allowance</button>
    <div id="pep-xau-reset-result" class="section-note" style="display:none;margin-top:10px"></div>
  </div>
</details>
<script>
(function() {
 const host=document.getElementById('pep-xau-campaign-reset');
 if (!host || host.dataset.wired) return;
 host.dataset.wired='1';
 const side=document.getElementById('pep-xau-reset-side');
 const preview=document.getElementById('pep-xau-reset-preview');
 const apply=document.getElementById('pep-xau-reset-apply');
 const result=document.getElementById('pep-xau-reset-result');
 let last=null;
 function show(el,message) {el.textContent=message;el.style.display='block';}
 async function refresh() {
   apply.disabled=true; last=null; show(preview,'Checking live XAU positions and trade history…');
   try {
     const r=await fetch('/api/xau/adaptive-campaign-reset/preview?side='+encodeURIComponent(side.value),{cache:'no-store'});
     const d=await r.json();
     if (!r.ok) throw Error(d.detail||d.error||'Preview unavailable');
     last=d;
     const recent=d.recent_closed||{};
     let msg='Current '+side.value.toUpperCase()+' campaign entries: '+d.current_campaign_entry_count+
       ' | LIVE XAU open: '+d.open_xau_total+' (same side '+d.open_xau_same_side+')'+
       ' | Broker match: '+(d.broker_links_exact?'EXACT':'NOT EXACT')+'.\n'+
       'Last '+recent.days+' days: '+recent.closed_count+' closed, '+recent.losing_count+' known losers, '+
       (recent.complete?'':'INCOMPLETE realised evidence, ')+
       'known net '+Number(recent.realized_r_known||0).toFixed(2)+'R.\n';
     if(recent.warning)msg+='WARNING: Recent losses or incomplete realised evidence; review before resetting.\n';
     msg += d.allowed ? 'Eligible for explicit manual reset.' : 'RESET BLOCKED: '+(d.blocking_reasons||[]).join(', ');
     preview.textContent=msg;preview.style.whiteSpace='pre-wrap'; apply.disabled=!d.allowed;
   } catch(e) {show(preview,'Preview error: '+(e.message||e));}
 }
 side.addEventListener('change',refresh);
 document.getElementById('pep-xau-reset-refresh').addEventListener('click',refresh);
 host.addEventListener('toggle',function(){if(host.open)refresh()});
 apply.addEventListener('click',async function() {
   if(!last || !last.allowed || last.side!==side.value) return;
   const recent=last.recent_closed||{};
   const warning=recent.warning?'WARNING: Recent losses or incomplete realised evidence.\n':'';
   const prompt='Reset the XAU '+side.value.toUpperCase()+' adaptive stacking entry allowance?\n\n'+
     'Current entries '+last.current_campaign_entry_count+'; open XAU trades '+last.open_xau_total+'.\n'+
     'Recent realised known result: '+Number(recent.realized_r_known||0).toFixed(2)+'R.\n'+warning+
     'No OANDA trade, stop, HWM or harvest stage will be modified. Continue?';
   if(!window.confirm(prompt))return;
   const secret=window.prompt('Enter WEBHOOK_SECRET to authorize the XAU campaign reset:');
   if(!secret){show(result,'Cancelled: secret not supplied.');return;}
   apply.disabled=true;show(result,'Verifying live exposure and recording manual decision…');
   try {
     const r=await fetch('/api/xau/adaptive-campaign-reset/reset',{
       method:'POST',headers:{'content-type':'application/json','x-webhook-secret':secret},
       body:JSON.stringify({side:side.value,confirm:'RESET_XAU_ADAPTIVE_CAMPAIGN',acknowledge_recent_results:true}),
       cache:'no-store'
     });
     const d=await r.json();
     if(!r.ok || !d.ok) throw Error((typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d))||'Reset failed');
     show(result,'RESET RECORDED: XAU '+side.value.toUpperCase()+
       ' count '+d.prior_campaign_count+' → '+d.new_campaign_count+
       '. Live harvest cycle and broker trades unchanged. No orders sent.');
     await refresh();
   } catch(e){show(result,'RESET NOT APPLIED: '+(e.message||e));await refresh();}
 });
})();
</script>
""";
