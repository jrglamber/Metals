"""Read-only broker-authoritative LIVE XAG open positions for Metals dashboard.

The legacy Open Trades section is XAU-only. XAG SHORT has a separate durable
ledger and must be explicitly shown rather than conflated with practice XAG.
This view sends OANDA GET requests only and never changes trades or risk.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List, Optional

import xag_short_live_override as live

VERSION = "metals_xag_open_positions_v2_mfe_giveback_visibility_2026_11_25"
_ACTIVE = {"OPEN", "OPEN_UNVERIFIED_STOP", "CLOSE_RETRY"}


def _float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        from math import isfinite
        value = float(value)
        return value if isfinite(value) else None
    except (ValueError, TypeError):
        return None


def snapshot(core: Any) -> Dict[str, Any]:
    """Current open broker trades, joined by broker ID to XAG LIVE ledger."""
    observed_now = datetime.now(timezone.utc)
    observed_at = observed_now.isoformat()
    broker = live._open_trades(core)  # GET /openTrades; no order-capable function.
    if broker.get("ok") is not True:
        return {
            "ok": False, "version": VERSION, "observed_at_utc": observed_at,
            "reason": str(broker.get("error") or "oanda_live_xag_snapshot_unavailable"),
            "broker_writes": False, "positions": [],
            "count": None, "unrealized_gbp": None, "unrealized_complete": False,
        }
    with core.get_conn() as conn:
        tracked = [dict(r) for r in conn.execute(
            "SELECT id, raw_signal_id, broker_trade_id, side, status, "
            "current_stop_price, stop_price, active_exit_policy, "
            "manager_last_decision, manager_high_water_r, estimated_risk_amount, "
            "manager_mfe_r, manager_mae_r, manager_last_review_candles, "
            "manager_last_review_signal_id, manager_last_reason "
            "FROM metals_xag_live_trade_links "
            "WHERE status IN ('OPEN','OPEN_UNVERIFIED_STOP','CLOSE_RETRY')"
        ).fetchall()]
        last_signal = conn.execute(
            "SELECT MAX(id) AS id FROM raw_signals "
            "WHERE UPPER(pair) IN ('XAGUSD','XAG')"
        ).fetchone()
    latest_signal_id = int(dict(last_signal)["id"]) if last_signal and dict(last_signal).get("id") is not None else None
    by_id = {str(r["broker_trade_id"]): r for r in tracked
             if r.get("broker_trade_id")}
    broker_trades = list(broker.get("xag") or [])
    positions: List[Dict[str, Any]] = []
    for t in broker_trades:
        bid = str(t.get("id") or "")
        units = _float(t.get("currentUnits"))
        pnl = _float(t.get("unrealizedPL"))
        entry = _float(t.get("price"))
        local = by_id.get(bid) or {}
        matched = bool(local and str(local.get("status") or "").upper() in _ACTIVE)
        same_side = bool(matched and units is not None and
                         (("short" if units < 0 else "long" if units > 0 else "flat") ==
                          str(local.get("side") or "").lower()))
        linked = matched and same_side
        side = "SHORT" if units is not None and units < 0 else (
            "LONG" if units is not None and units > 0 else "UNKNOWN")
        stop = _float(local.get("current_stop_price"))
        if stop is None:
            stop = _float(local.get("stop_price"))
        risk = _float(local.get("estimated_risk_amount")) if linked else None
        mfe = _float(local.get("manager_mfe_r")) if linked else None
        mae = _float(local.get("manager_mae_r")) if linked else None
        high_water = _float(local.get("manager_high_water_r")) if linked else None
        # Manager R uses OHLC price history, whereas this is a LIVE OANDA
        # mark in GBP divided by the original GBP risk. FX/spread differences
        # mean it is indicative, never an execution trigger.
        current_r_approx = (pnl / risk if pnl is not None and risk is not None
                            and risk > 0 else None)
        peak_r = (max(0.0, *[v for v in (mfe, high_water) if v is not None])
                  if mfe is not None or high_water is not None else None)
        giveback_r_approx = (max(0.0, peak_r - current_r_approx)
                             if peak_r is not None and current_r_approx is not None
                             and peak_r > 0 else None)
        giveback_pct_approx = (100.0 * giveback_r_approx / peak_r
                               if giveback_r_approx is not None and peak_r > 0 else None)
        last_review = local.get("manager_last_review_signal_id") if linked else None
        last_review_id = int(last_review) if last_review is not None else None
        reviewed_candles = local.get("manager_last_review_candles") if linked else None
        reviewed_candles = int(reviewed_candles) if reviewed_candles is not None else None
        review_status = (
            "UNLINKED" if not linked else
            "NO_REVIEW" if last_review_id is None else
            "CURRENT" if latest_signal_id is not None and last_review_id == latest_signal_id else
            "PRIOR_SIGNAL" if latest_signal_id is not None else "UNVERIFIED_RECENCY"
        )
        positions.append({
            "asset": "XAG_USD", "side": side, "units": units,
            "broker_trade_id": bid, "entry_price": entry,
            "unrealized_gbp": pnl, "opened_at_utc": str(t.get("openTime") or ""),
            "age_hours": _age_hours(t.get("openTime"), observed_now),
            "broker_stop_order_id": str(t.get("stopLossOrderID") or ""),
            "recorded_stop_price": stop,
            "recorded_stop_source": "LIVE_XAG_LINK" if local else None,
            "tracked": linked, "local_status": str(local.get("status") or ""),
            "manager": str(local.get("active_exit_policy") or "") if linked else "",
            "last_manager_decision": str(local.get("manager_last_decision") or "") if linked else "",
            "manager_high_water_r": high_water,
            "manager_mfe_r": mfe, "manager_mae_r": mae,
            "current_r_approx": round(current_r_approx, 4) if current_r_approx is not None else None,
            "mfe_giveback_r_approx": round(giveback_r_approx, 4) if giveback_r_approx is not None else None,
            "mfe_giveback_pct_approx": round(giveback_pct_approx, 2) if giveback_pct_approx is not None else None,
            "mfe25_floor_r": round(peak_r * 0.75, 4) if peak_r is not None and peak_r > 0 else None,
            "manager_review_candles": reviewed_candles,
            "manager_review_signal_id": last_review_id,
            "manager_review_status": review_status,
            "manager_mature": reviewed_candles is not None and reviewed_candles >= 48,
            "manager_last_reason": str(local.get("manager_last_reason") or "") if linked else "",
            "risk_gbp": risk,
            "raw_signal_id": local.get("raw_signal_id") if linked else None,
        })
    linked_ids = {str(x["broker_trade_id"]) for x in positions if x["tracked"]}
    local_ids = [str(r["broker_trade_id"]) for r in tracked if r.get("broker_trade_id")]
    matched_exactly = (
        len(broker_trades) == len(tracked)
        and len(local_ids) == len(tracked)
        and len(local_ids) == len(set(local_ids))
        and all(x["tracked"] for x in positions)
        and set(local_ids) == linked_ids
    )
    complete = all(x["unrealized_gbp"] is not None for x in positions)
    total = round(sum(x["unrealized_gbp"] for x in positions), 4) if complete else None
    return {
        "ok": True, "version": VERSION, "observed_at_utc": observed_at,
        "source": "LIVE_OANDA_OPEN_TRADES_PLUS_XAG_LIVE_LINKS",
        "account_currency": "GBP",
        "positions": positions, "count": len(positions),
        "short_count": sum(x["side"] == "SHORT" for x in positions),
        "long_count": sum(x["side"] == "LONG" for x in positions),
        "tracked_count": sum(x["tracked"] for x in positions),
        "latest_xag_signal_id": latest_signal_id,
        "mature_manager_trade_count": sum(x["manager_mature"] for x in positions),
        "mature_manager_current_count": sum(
            x["manager_mature"] and x["manager_review_status"] == "CURRENT"
            for x in positions),
        "mature_manager_stale_count": sum(
            x["manager_mature"] and x["manager_review_status"] != "CURRENT"
            for x in positions),
        "untracked_broker_count": sum(not x["tracked"] for x in positions),
        "orphan_local_count": len([x for x in local_ids if x not in {
            str(y["broker_trade_id"]) for y in positions}]),
        "broker_link_reconciled": matched_exactly,
        "unrealized_gbp": total,
        "unrealized_complete": complete,
        "broker_writes": False, "practice_positions_excluded": True,
        "realized_pnl_included": False,
    }


def _age_hours(opened_at: Any, now: datetime) -> Optional[int]:
    raw = str(opened_at or "").strip()
    if not raw:
        return None
    try:
        opened = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return max(0, int((now - opened.astimezone(timezone.utc)).total_seconds() / 3600.0))
    except (TypeError, ValueError):
        return None


def _fmt(value: Any, decimals: int = 3, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{float(value):,.{decimals}f}{suffix}"


def render(core: Any) -> str:
    """Drop-in HTML for existing /dashboard/section/open-trades."""
    try:
        state = snapshot(core)
    except Exception as exc:
        state = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not state.get("ok"):
        reason = escape(str(state.get("reason") or "unknown"))
        return (
            '<div id="pep-live-xag-open-positions" class="section-note warn">'
            '<strong>LIVE XAG SHORT positions: broker read unavailable.</strong> '
            'Cannot verify current positions or P&amp;L. '
            'This is NOT a report of zero open trades. '
            'Reason: '+reason+'.</div>'
        )
    status = "EXACT" if state["broker_link_reconciled"] else "MISMATCH — CHECK EXECUTION HEALTH"
    pnl = ("£"+_fmt(state["unrealized_gbp"],2)
           if state["unrealized_complete"] else "INCOMPLETE")
    summary = (
        '<div id="pep-live-xag-open-positions" class="section-note">'
        '<strong>LIVE — XAG SHORT positions (real-money OANDA)</strong><div>'
        'Open <strong>'+str(state["count"])+'</strong>'
        ' (short '+str(state["short_count"])+', long '+str(state["long_count"])+'); '
        'tracked <strong>'+str(state["tracked_count"])+'</strong>; '
        'reconciliation <strong>'+escape(status)+'</strong>; '
        'broker unrealised P&amp;L <strong>'+escape(pnl)+'</strong> (GBP).'
        '</div><div>These are separate from LIVE XAU positions below. '
        'Legacy top tiles and “Live owned open trades” currently cover XAU only. '
        'Practice XAG positions are excluded. Stop prices and manager fields are recorded '
        'in the XAG live ledger; live P&amp;L, entries and open counts come from OANDA.</div></div>'
    )
    if not state["positions"]:
        return summary+'<div class="section-note">No LIVE XAG trades currently open in OANDA.</div>'
    rows = []
    for x in state["positions"]:
        tracking = "TRACKED" if x["tracked"] else "UNLINKED — CHECK"
        css = "pos" if x["tracked"] else "warn"
        pnlcell = "£"+_fmt(x["unrealized_gbp"],2) if x["unrealized_gbp"] is not None else "—"
        giveback = (
            _fmt(x["mfe_giveback_r_approx"],2,"R") +
            " (" + _fmt(x["mfe_giveback_pct_approx"],1,"%") + ")"
            if x["mfe_giveback_r_approx"] is not None else "—"
        )
        reviewed = (
            (_fmt(x["manager_review_candles"],0,"c") if x["manager_review_candles"] is not None else "—") +
            " / " + x["manager_review_status"]
        )
        rows.append(
            '<tr>'
            '<td><strong>'+escape(x["asset"])+'</strong></td>'
            '<td>'+escape(x["side"])+'</td>'
            '<td>'+_fmt(x["units"],1)+'</td>'
            '<td>'+_fmt(x["entry_price"],3)+'</td>'
            '<td>'+escape(pnlcell)+'</td>'
            '<td>'+escape(x["broker_trade_id"])+'</td>'
            '<td>'+escape(x["opened_at_utc"])+'</td>'
            '<td>'+_fmt(x["age_hours"], 0, "h")+'</td>'
            '<td>'+_fmt(x["recorded_stop_price"],3)+'</td>'
            '<td>'+escape(x["manager"] or "—")+'</td>'
            '<td>'+_fmt(x["manager_mfe_r"],2,"R")+'</td>'
            '<td>'+_fmt(x["manager_mae_r"],2,"R")+'</td>'
            '<td>'+_fmt(x["current_r_approx"],2,"R")+'</td>'
            '<td>'+escape(giveback)+'</td>'
            '<td>'+_fmt(x["mfe25_floor_r"],2,"R")+'</td>'
            '<td>'+escape(reviewed)+'</td>'
            '<td>'+escape(x["last_manager_decision"] or "—")+'</td>'
            '<td class="'+css+'"><strong>'+escape(tracking)+'</strong></td>'
            '</tr>'
        )
    return summary+(
        '<div class="table-scroll"><table><thead><tr>'
        '<th>Instrument</th><th>Side</th><th>Units</th><th>Entry</th>'
        '<th>Unrealised P/L (GBP)</th><th>OANDA trade ID</th><th>Opened (UTC)</th><th>Trade Age</th>'
        '<th>Recorded SL</th><th>Live manager</th><th>MFE (R)</th><th>MAE (R)</th>'
        '<th>Now (R~)</th><th>Giveback from MFE~</th><th>75% MFE floor</th>'
        '<th>Last review</th><th>Last decision</th><th>Tracking</th>'
        '</tr></thead><tbody>'+"".join(rows)+'</tbody></table></div>'
        '<div class="section-note small">LIVE XAG SHORT broker snapshot at '+
        escape(state["observed_at_utc"])+'. '
        'Figures are unrealised only; do not add practice positions or assume '
        'the historical realised XAG ledger has been verified. '
        'MFE/MAE are recorded at the last completed-candle manager review; '
        'Now R~ is OANDA unrealised GBP / initial estimated GBP risk and '
        'giveback ~ is indicative only. Exact MFE25 exits use original hourly '
        'manager metrics, not these display estimates. '
        'Review: c = completed hourly candles, CURRENT = latest XAG signal.</div>'
    )
