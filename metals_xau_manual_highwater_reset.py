"""Atomic XAU-only broker high-water + harvest-cycle rebase for manual campaign reset.

A selected-direction reset is never allowed with opposite-side live XAU
positions, or while harvest orders are pending. Everything historic stays
auditable. No calls that place, close or modify OANDA trades.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List

VERSION = "metals_xau_manual_hwm_rebase_v1_2026_11_12"
_HWM_LOCK = threading.RLock()
_INSTALLED_HWM = False


def _f(value: Any):
    if value is None or value == "":
        return None
    try:
        out = float(value)
        from math import isfinite
        return out if isfinite(out) else None
    except (ValueError, TypeError):
        return None


def install_hwm_lock(core: Any) -> None:
    """Serialize the intrahour HWM sampler with operator HWM resets."""
    global _INSTALLED_HWM
    if _INSTALLED_HWM:
        return
    original = getattr(core, "_metals_xau_live_highwater_state", None)
    if not callable(original):
        raise RuntimeError("XAU live HWM reader not available; refusing reset installation")

    def guarded_hwm(*args, **kwargs):
        with _HWM_LOCK:
            return original(*args, **kwargs)

    core._metals_xau_live_highwater_state = guarded_hwm
    _INSTALLED_HWM = True


def baseline(core: Any, broker: Dict[str, Any], open_rows: List[Dict[str, Any]], side: str) -> Dict[str, Any]:
    """Fresh OANDA P&L plus confirmed database risk; no guessed values."""
    if side not in ("long", "short") or broker.get("ok") is not True:
        return {"ok": False, "reason": "broker_or_side_invalid"}
    trades = list(broker.get("all_xau_trades") or [])
    bids = [str(t.get("id") or "") for t in trades]
    lids = [str(r.get("broker_trade_id") or "") for r in open_rows]
    if (len(trades) != len(open_rows) or
        len(set(bids)) != len(bids) or len(set(lids)) != len(lids) or
        not all(bids) or not all(lids) or set(bids) != set(lids)):
        return {"ok": False, "reason": "xau_broker_link_reconciliation_not_exact"}
    if not 1 <= len(trades) <= 2:
        return {"ok": False, "reason": "xau_open_exposure_must_be_one_or_two"}
    by_id = {str(t["id"]): t for t in trades}
    current_gbp = 0.0
    current_r = 0.0
    for row in open_rows:
        trade = by_id[str(row["broker_trade_id"])]
        units = _f(trade.get("currentUnits"))
        direction = "long" if units is not None and units > 0 else "short" if units is not None and units < 0 else "flat"
        if direction != side or str(row.get("side") or "").lower() != side:
            return {"ok": False, "reason": "xau_opposite_direction_open_hwm_is_shared"}
        pnl = _f(trade.get("unrealizedPL"))
        risk = _f(row.get("estimated_risk_amount"))
        if risk is None or risk <= 0:
            risk = _f(row.get("requested_risk_amount"))
        if pnl is None or risk is None or risk <= 0:
            return {"ok": False, "reason": "xau_fresh_broker_pl_or_trade_risk_unavailable"}
        current_gbp += pnl
        current_r += pnl/risk
    # The live XAU HWM convention floors the displayed high-water at zero;
    # a negative basket does not become a negative peak on reset.
    return {
        "ok": True, "open_count": len(trades),
        "current_gbp": round(current_gbp, 6),
        "current_r": round(current_r, 8),
        "new_hwm_gbp": max(0.0, round(current_gbp, 6)),
        "new_hwm_r": max(0.0, round(current_r, 8)),
        "broker_trade_ids": bids,
        "side": side,
        "broker_read_ok": True, "no_broker_orders": True,
    }


def pending_harvest_guard(conn: Any, cycle_id: str) -> Dict[str, Any]:
    """Refuse to orphan an in-progress partial harvest or queued broker close."""
    stages = [dict(r) for r in conn.execute(
        "SELECT status, threshold_r, executed_bank_gbp FROM metals_xau_live_harvest_stages "
        "WHERE cycle_id=?", (cycle_id,)
    ).fetchall()]
    part = [
        st for st in stages
        if str(st.get("status") or "").upper() in {"PARTIAL_RETRY","PARTIAL_TARGET","CLOSE_RETRY","EXECUTING"}
        or (
            (_f(st.get("executed_bank_gbp")) or 0.0) > 0
            and str(st.get("status") or "").upper() != "EXECUTED"
        )
    ]
    queue = conn.execute(
        "SELECT COUNT(*) AS n FROM metals_xau_live_action_queue "
        "WHERE action='CLOSE' AND (reason LIKE 'LIVE_XAU_HARVEST_%' OR reason LIKE '%HARVEST%') "
        "AND UPPER(status) NOT IN ('CLOSED','EXECUTED','DONE','CANCELLED','FAILED_FINAL','COMPLETE')"
    ).fetchone()
    inflight = int(queue["n"] or 0) if queue is not None else 0
    return {"ok": not part and inflight == 0,
            "partial_stages": len(part), "pending_harvest_closes": inflight}


def perform_rebase(core: Any, side: str, *, align_only: bool, expected_cycle_id: str,
                   expected_open_ids: List[str], manual_reset_at: str = "") -> Dict[str, Any]:
    """One transaction for HWM/cycle/cursor/audit; no trade- or stop-order writes.

    Must be called with HWM_LOCK and XAU HARVEST_LOCK both held. A fresh
    second broker snapshot inside those locks must match the preview.
    """
    now_dt = datetime.now(timezone.utc)
    observed = now_dt.isoformat()
    fresh_broker = core.metals_xau_live_broker_snapshot(include_account_summary=False)
    with core.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT broker_trade_id,side,estimated_risk_amount,requested_risk_amount "
            "FROM metals_xau_live_trade_links WHERE status='OPEN' ORDER BY id"
        ).fetchall()]
    proof = baseline(core, fresh_broker, rows, side)
    if not proof.get("ok"):
        return proof
    if set(proof["broker_trade_ids"]) != set(expected_open_ids):
        return {"ok": False, "reason": "xau_positions_changed_since_preview"}

    with core.get_conn() as conn:
        getter = core._metals_xau_live_runtime_get
        setter = core._metals_xau_live_runtime_set
        active = str(getter(conn, "active_harvest_cycle_id", "") or "")
        if active != expected_cycle_id or not active:
            return {"ok": False, "reason": "xau_harvest_cycle_changed_since_preview"}
        pending = pending_harvest_guard(conn, active)
        if not pending["ok"]:
            return {"ok": False, "reason": "xau_harvest_in_progress", "harvest": pending}
        previous = {
            "cycle_id": active,
            "gbp": _f(getter(conn, "broker_hwm_gbp", "0")) or 0,
            "r": _f(getter(conn, "broker_hwm_r", "0")) or 0,
            "seen_at": str(getter(conn, "broker_hwm_seen_at", "") or ""),
            "harvest_cursor_r": _f(getter(conn, "harvest_last_seen_hwm_r", "0")) or 0,
        }
        if align_only:
            last = str(getter(conn, "xau_adaptive_manual_campaign_"+side+"_reset_at", "") or "")
            last_cycle = str(getter(conn, "xau_adaptive_manual_campaign_"+side+"_cycle_id", "") or "")
            aligned = str(getter(conn, "xau_adaptive_manual_campaign_"+side+"_hwm_aligned_at", "") or "")
            if last != manual_reset_at or last_cycle != active or aligned:
                return {"ok": False, "reason": "previous_manual_reset_no_longer_eligible_for_alignment"}
        new_cycle = "XAU_LIVE_" + now_dt.strftime("%Y%m%dT%H%M%S%fZ")
        # Archive only unfinished, completely unexecuted stages. Executed
        # harvest evidence stays immutable in the old cycle.
        conn.execute(
            """UPDATE metals_xau_live_harvest_stages SET
               updated_at_utc=?,status='MANUAL_CAMPAIGN_ARCHIVED',
               reason=COALESCE(reason,'') || ' | archived with manual XAU HWM reset'
               WHERE cycle_id=? AND status NOT IN
               ('EXECUTED','EXPIRED_FLAT','LEGACY_PASSED_UNBANKED',
                'MANUAL_CAMPAIGN_ARCHIVED')""",
            (observed, active),
        )
        setter(conn, "broker_hwm_open_count", proof["open_count"])
        setter(conn, "broker_hwm_gbp", proof["new_hwm_gbp"])
        setter(conn, "broker_hwm_r", proof["new_hwm_r"])
        setter(conn, "broker_hwm_seen_at", observed)
        setter(conn, "active_harvest_cycle_id", new_cycle)
        setter(conn, "harvest_last_seen_hwm_r", proof["new_hwm_r"])
        setter(conn, "harvest_ever_initialized", "1")
        setter(conn, "xau_adaptive_manual_campaign_"+side+"_hwm_aligned_cycle_id", new_cycle)
        setter(conn, "xau_adaptive_manual_campaign_"+side+"_hwm_aligned_at", observed)
        if not align_only:
            setter(conn, "xau_adaptive_manual_campaign_"+side+"_cycle_id", new_cycle)
            setter(conn, "xau_adaptive_manual_campaign_"+side+"_reset_at", observed)
        conn.execute(
            """INSERT INTO metals_xau_live_hwm_events(
                created_at_utc,observed_at_utc,event_type,open_count,current_gbp,
                current_r,high_water_gbp,high_water_r,source,raw_json)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (observed, observed, "MANUAL_CAMPAIGN_HWM_REBASE",
             proof["open_count"], proof["current_gbp"], proof["current_r"],
             proof["new_hwm_gbp"], proof["new_hwm_r"],
             "manual_xau_adaptive_campaign_reset",
             json.dumps({"prior": previous, "new_cycle_id":new_cycle,
                         "side":side,"align_only":align_only,
                         "original_manual_reset_at":manual_reset_at,
                         "broker_open_count":proof["open_count"],
                         "no_broker_orders":True,
                         "individual_trade_hwm_and_stops_unchanged":True,
                         "previous_harvest_execution_preserved":True},default=str)),
        )
        conn.execute(
            """INSERT INTO metals_xau_live_execution_audit
                (created_at_utc,raw_signal_id,action,status,message,raw_json)
               VALUES(?,?,?,?,?,?)""",
            (observed, None, "manual_xau_hwm_cycle_rebase", "CONFIRMED",
             "Manual XAU "+side+" basket HWM rebased; old harvest stage history retained",
             json.dumps({"side":side,"previous":previous,
                         "new_cycle_id":new_cycle,
                         "broker_baseline":proof,
                         "align_only":align_only,
                         "no_broker_orders":True},default=str)),
        )
        conn.commit()
    return {"ok": True,"version":VERSION,"side":side,
            "alignment_only":align_only,"new_cycle_id":new_cycle,
            "reset_at_utc":observed,"previous":previous,
            "baseline":proof,"next_harvest_threshold_r":30,
            "open_positions_and_stops_unchanged":True,
            "previous_executed_harvest_preserved":True,
            "no_broker_orders":True}
