"""Read-only, broker/ledger-linked XAU LONG and SHORT open positions.

All financial marks and live trade identities come from OANDA's GET openTrades
snapshot. Historical manager MFE/MAE and review state come from the XAU LIVE
ledger. This module NEVER routes or changes an order.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Dict, List, Optional

VERSION = "xau_live_open_positions_mfe_manager_v1_2026_11_25"
_ACTIVE = {"OPEN", "OPEN_UNVERIFIED_STOP", "CLOSE_RETRY"}


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or v == "" or isinstance(v, bool):
            return None
        n = float(v)
        return n if isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None


def snapshot(core: Any) -> Dict[str, Any]:
    """Report all broker-open XAU trades, identifying any unlinked positions."""
    broker = core.metals_xau_live_broker_snapshot(include_account_summary=False) or {}
    if broker.get("ok") is not True:
        return {"ok": False, "reason": "live_xau_broker_open_trades_unavailable",
                "trades": [], "count": None, "broker_order_writes": False}

    # OANDA's original owned_open_trades population was LONG-only. Use the
    # instrument-wide broker population and enforce exact trade-link ownership,
    # so promoted live XAU shorts are not accidentally hidden.
    all_trades = broker.get("all_xau_trades")
    if not isinstance(all_trades, list):
        return {"ok": False, "reason": "xau_broker_all_sides_unavailable",
                "trades": [], "count": None, "broker_order_writes": False}

    with core.get_conn() as conn:
        links = [dict(r) for r in conn.execute(
            "SELECT broker_trade_id,side,status,entry_price,stop_price,"
            "current_stop_price,estimated_risk_amount,requested_risk_amount,"
            "active_exit_policy,manager_current_r,manager_high_water_r,"
            "manager_mfe_r,manager_mae_r,manager_last_review_candles,"
            "manager_last_review_signal_id,manager_last_decision,manager_last_reason "
            "FROM metals_xau_live_trade_links "
            "WHERE status IN ('OPEN','OPEN_UNVERIFIED_STOP','CLOSE_RETRY')"
        ).fetchall()]
        latest = conn.execute(
            "SELECT MAX(id) AS id FROM raw_signals "
            "WHERE UPPER(pair) IN ('XAUUSD','XAU')"
        ).fetchone()
    latest_id = int(dict(latest)["id"]) if latest and dict(latest).get("id") is not None else None
    by_id = {str(r.get("broker_trade_id")): r for r in links if r.get("broker_trade_id")}

    positions: List[Dict[str, Any]] = []
    for t in all_trades:
        if str(t.get("instrument") or "").upper() != "XAU_USD":
            continue
        tid = str(t.get("id") or "")
        units = _num(t.get("currentUnits"))
        side = "short" if units is not None and units < 0 else "long" if units is not None and units > 0 else ""
        local = by_id.get(tid) or {}
        linked = bool(tid and local and side
                      and str(local.get("side") or "").lower() == side
                      and str(local.get("status") or "").upper() in _ACTIVE)
        upl = _num(t.get("unrealizedPL"))
        risk = (_num(local.get("estimated_risk_amount")) or
                _num(local.get("requested_risk_amount"))) if linked else None
        now_r = upl / risk if upl is not None and risk is not None and risk > 0 else None
        mfe = _num(local.get("manager_mfe_r")) if linked else None
        mae = _num(local.get("manager_mae_r")) if linked else None
        hwm = _num(local.get("manager_high_water_r")) if linked else None
        # The ledger's MFE is the reviewed manager peak, not necessarily the
        # latest intrahour broker peak. These quantities are informative only.
        peak = max([0.0] + [v for v in (mfe, hwm) if v is not None]) if linked and (mfe is not None or hwm is not None) else None
        giveback = max(0., peak - now_r) if peak is not None and peak > 0 and now_r is not None else None
        policy = str(local.get("active_exit_policy") or "") if linked else ""
        is_mfe25 = side == "short" and "MFE" in policy.upper() and "25" in policy
        review_id = local.get("manager_last_review_signal_id") if linked else None
        review_id = int(review_id) if review_id is not None else None
        hold = local.get("manager_last_review_candles") if linked else None
        hold = int(hold) if hold is not None else None
        review = ("UNLINKED" if not linked else "NO_REVIEW" if review_id is None else
                  "CURRENT" if latest_id is not None and review_id == latest_id else
                  "PRIOR_SIGNAL" if latest_id is not None else "UNVERIFIED_RECENCY")
        positions.append({
            "id": tid, "instrument": "XAU_USD", "side": side,
            "units": units, "open_time": str(t.get("openTime") or ""),
            "price": _num(t.get("price")), "unrealized_pl": upl,
            "broker_trade_id": tid,
            "tracked": linked,
            "recorded_stop_price": (_num(local.get("current_stop_price")) or _num(local.get("stop_price"))) if linked else None,
            "manager": policy if linked else "",
            "manager_mfe_r": mfe, "manager_mae_r": mae,
            "manager_current_r": _num(local.get("manager_current_r")) if linked else None,
            "current_r_approx": round(now_r, 4) if now_r is not None else None,
            "mfe_giveback_r_approx": round(giveback, 4) if giveback is not None else None,
            "mfe_giveback_pct_approx": round(giveback / peak * 100, 2) if giveback is not None and peak else None,
            "mfe25_floor_r": round(mfe * .75, 4) if is_mfe25 and mfe is not None and mfe > 0 else None,
            "manager_review_candles": hold,
            "manager_review_signal_id": review_id,
            "manager_review_status": review,
            "manager_last_decision": str(local.get("manager_last_decision") or "") if linked else "",
            "manager_last_reason": str(local.get("manager_last_reason") or "") if linked else "",
            "manager_mature": hold is not None and hold >= 48,
            "tracking": "BROKER LINKED" if linked else "UNLINKED — CHECK",
        })
    visible = {p["id"] for p in positions if p["tracked"]}
    local_ids = {str(r.get("broker_trade_id")) for r in links if r.get("broker_trade_id")}
    return {
        "ok": True, "version": VERSION, "scope": "BROKER_XAU_ALL_SIDES_LIVE_LEDGER_JOIN",
        "count": len(positions), "trades": positions,
        "tracked_count": len(visible),
        "untracked_broker_count": sum(not p["tracked"] for p in positions),
        "orphan_local_count": len(local_ids - visible),
        "broker_link_reconciled": len(positions) == len(visible) == len(local_ids),
        "mature_manager_trade_count": sum(p["manager_mature"] for p in positions),
        "mature_manager_current_count": sum(p["manager_mature"] and p["manager_review_status"] == "CURRENT" for p in positions),
        "mature_manager_stale_count": sum(p["manager_mature"] and p["manager_review_status"] != "CURRENT" for p in positions),
        "latest_xau_signal_id": latest_id,
        "broker_order_writes": False,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
