"""Metals live/practice exit-policy override.

Production strategy:
- XAUUSD LONG: 48 hourly candles minimum hold + 2x ATR chandelier
- XAUUSD SHORT: 48 hourly candles minimum hold + MFE25 giveback manager
- existing emergency broker stop and basket harvesting remain authoritative
- XAG long/short remain CURRENT_MANAGER/research-only

Important execution boundary:
The core live-XAU broker lane is historically XAU-LONG-only and explicitly
rejects shorts before order construction. This module promotes the XAU short
manager/policy without bypassing that broker guard. A live short order must not
be enabled until the downstream signed-units and short emergency-stop path is
verified side-safe.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


POLICY = "ATR2_CHANDELIER"
POLICY_VERSION = "metals_xau_long_atr2_48h_v1_2026_09_29"
MIN_HOLD = 48
ATR_MULTIPLIER = 2.0

XAU_SHORT_POLICY = "MFE_GIVEBACK_25"
XAU_SHORT_POLICY_VERSION = "metals_xau_short_mfe25_48h_v1_2026_10_06"
XAU_SHORT_MIN_HOLD = 48
XAU_SHORT_GIVEBACK_FRACTION = 0.25


def _num(core: Any, value: Any) -> Optional[float]:
    try:
        return core.safe_float(value)
    except Exception:
        try:
            return float(value) if value is not None else None
        except Exception:
            return None


def _path(core: Any, link: Dict[str, Any]):
    anchor = int(_num(core, link.get("entry_signal_id")) or _num(core, link.get("raw_signal_id")) or 0)
    try:
        with core.get_conn() as conn:
            return list(core._metals_demo_path(conn, "XAUUSD", anchor, limit=2000))
    except Exception:
        return []


def _atr_pct(core: Any, path: list) -> Optional[float]:
    if not path:
        return None
    latest = path[-1]
    try:
        raw = json.loads(core.safe_str(latest.get("raw_json")) or "{}")
    except Exception:
        raw = {}
    for key in ("exec_atr_pct", "execution_atr_pct", "atr_pct"):
        v = _num(core, raw.get(key))
        if v is not None and v > 0:
            return v
    rows = path[-15:]
    trs = []
    prev_close = None
    for row in rows:
        h = _num(core, row.get("exec_high"))
        lo = _num(core, row.get("exec_low"))
        close = _num(core, row.get("exec_close"))
        if h is None or lo is None or close is None:
            continue
        tr = h - lo
        if prev_close is not None:
            tr = max(tr, abs(h - prev_close), abs(lo - prev_close))
        trs.append(tr)
        prev_close = close
    close = _num(core, latest.get("exec_close"))
    if not trs or close is None or close <= 0:
        return None
    return (sum(trs) / len(trs)) / close * 100.0


def _trail(core: Any, link: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
    path = _path(core, link)
    if len(path) < 2:
        return {"ok": False, "reason": "insufficient_xau_path"}
    entry = _num(core, link.get("entry_price"))
    current = _num(core, metrics.get("current_price"))
    if entry is None or current is None or entry <= 0:
        return {"ok": False, "reason": "missing_entry_or_current"}
    previous = path[:-1]
    highs = [_num(core, x.get("exec_high")) for x in previous]
    highs = [x for x in highs if x is not None]
    prev_close = _num(core, previous[-1].get("exec_close")) if previous else entry
    latest_low = _num(core, path[-1].get("exec_low"))
    atr_pct = _atr_pct(core, path)
    if not highs or prev_close is None or atr_pct is None:
        return {"ok": False, "reason": "atr_unavailable"}
    prev_high = max(highs)
    hard_stop = _num(core, link.get("stop_price"))
    if hard_stop is None:
        sl_pct = _num(core, getattr(core, "METALS_XAU_LIVE_SL_PCT", 2.21)) or 2.21
        hard_stop = entry * (1.0 - sl_pct / 100.0)
    trail = max(hard_stop, prev_high - (prev_close * atr_pct / 100.0 * ATR_MULTIPLIER))
    sl_pct = _num(core, getattr(core, "METALS_XAU_LIVE_SL_PCT", 2.21)) or 2.21
    trail_r = (trail / entry - 1.0) / (sl_pct / 100.0)
    return {"ok": True, "trail_price": trail, "trail_r": trail_r, "atr_pct": atr_pct,
            "previous_high": prev_high, "latest_low": latest_low,
            "current_price": current, "hard_stop": hard_stop}


def _old_mfe50(core: Any, metrics: Dict[str, Any]) -> Dict[str, Any]:
    h = int(metrics.get("hold_candles") or 0)
    r = _num(core, metrics.get("current_r"))
    mfe = max(0.0, _num(core, metrics.get("mfe_r")) or 0.0)
    phase = core._metals_demo_phase(h)
    if h < 48:
        return {"decision": "HOLD_MIN_48", "phase": phase, "active_exit_policy": "MFE_GIVEBACK_50", "reason": "Legacy XAU MFE50 policy locked before 48h."}
    floor = mfe * 0.5
    if mfe > 0 and r is not None and r <= floor:
        return {"decision": "CLOSE_MFE50_GIVEBACK", "phase": phase, "active_exit_policy": "MFE_GIVEBACK_50", "mfe_r": mfe, "mfe_floor_r": floor, "reason": "Legacy XAU MFE50 giveback floor."}
    return {"decision": "EXTEND", "phase": phase, "active_exit_policy": "MFE_GIVEBACK_50", "mfe_r": mfe, "mfe_floor_r": floor if mfe > 0 else None, "reason": "Legacy XAU MFE50 policy retained for pre-cutover trade."}


def _xau_short_mfe25(core: Any, metrics: Dict[str, Any]) -> Dict[str, Any]:
    """48h minimum, then close after a 25% giveback from MFE.

    A 25% giveback means retaining 75% of the best R achieved. This mirrors the
    forward-shadow MFE_GIVEBACK_25 definition used in the research export.
    """
    h = int(metrics.get("hold_candles") or 0)
    r = _num(core, metrics.get("current_r"))
    mfe = max(0.0, _num(core, metrics.get("mfe_r")) or 0.0)
    phase = core._metals_demo_phase(h)
    if h < XAU_SHORT_MIN_HOLD:
        return {
            "decision": "HOLD_MIN_48",
            "phase": phase,
            "active_exit_policy": XAU_SHORT_POLICY,
            "mfe_r": mfe,
            "reason": "XAU SHORT MFE25: normal exit locked before 48h; emergency broker stop remains active.",
        }
    floor = mfe * (1.0 - XAU_SHORT_GIVEBACK_FRACTION)
    if mfe > 0 and r is not None and r <= floor:
        return {
            "decision": "CLOSE_MFE25_GIVEBACK",
            "phase": phase,
            "active_exit_policy": XAU_SHORT_POLICY,
            "mfe_r": mfe,
            "mfe_floor_r": floor,
            "giveback_fraction": XAU_SHORT_GIVEBACK_FRACTION,
            "reason": "XAU SHORT MFE25 giveback floor reached after 48h.",
        }
    return {
        "decision": "EXTEND",
        "phase": phase,
        "active_exit_policy": XAU_SHORT_POLICY,
        "mfe_r": mfe,
        "mfe_floor_r": floor if mfe > 0 else None,
        "giveback_fraction": XAU_SHORT_GIVEBACK_FRACTION,
        "reason": "XAU SHORT MFE25 active: retain 75% of MFE and review next hourly signal.",
    }


def _xau_short_stop_candidate(core: Any, link: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Translate the MFE25 R-floor into a tightening short-side broker stop.

    This is inert until the core broker lane supports live shorts; it is kept
    here so the promoted policy has a side-correct protection implementation
    ready rather than reusing the long ATR stop formula.
    """
    h = int(metrics.get("hold_candles") or 0)
    if h < XAU_SHORT_MIN_HOLD:
        return {"eligible": False, "reason": "xau_short_mfe25_pre48"}
    mfe = max(0.0, _num(core, metrics.get("mfe_r")) or 0.0)
    current = _num(core, metrics.get("current_price"))
    entry = _num(core, link.get("entry_price"))
    if mfe <= 0 or current is None or entry is None or entry <= 0:
        return {"eligible": False, "reason": "xau_short_mfe25_missing_metrics"}
    floor_r = mfe * (1.0 - XAU_SHORT_GIVEBACK_FRACTION)
    initial_stop = _num(core, link.get("stop_price"))
    if initial_stop is not None and initial_stop > entry:
        risk_price = initial_stop - entry
    else:
        sl_pct = _num(core, getattr(core, "METALS_XAU_LIVE_SL_PCT", 2.21)) or 2.21
        risk_price = entry * sl_pct / 100.0
    stop_price = entry - floor_r * risk_price
    previous = _num(core, link.get("current_stop_price")) or initial_stop
    step = entry * (_num(core, getattr(core, "METALS_DEMO_MANAGER_MIN_STOP_STEP_PCT", 0.02)) or 0.02) / 100.0
    if stop_price <= current:
        return {"eligible": False, "reason": "mfe25_short_stop_at_or_below_current_price", "stop_price": stop_price, "mfe_floor_r": floor_r}
    if previous is not None and stop_price >= previous - step:
        return {"eligible": False, "reason": "mfe25_short_would_not_tighten_enough", "stop_price": stop_price, "mfe_floor_r": floor_r}
    return {
        "eligible": True,
        "stop_price": stop_price,
        "mfe_r": mfe,
        "mfe_floor_r": floor_r,
        "giveback_fraction": XAU_SHORT_GIVEBACK_FRACTION,
        "reason": f"XAU SHORT MFE25 protected stop {stop_price:.3f} retaining 75% of MFE.",
    }


def install(core: Any) -> Dict[str, Any]:
    original_stop = core._metals_xau_live_stop_candidate
    original_status = core.metals_exit_policy_status
    original_live_config = getattr(core, "metals_xau_live_config_status", None)

    core.METALS_XAU_LONG_MFE50_POLICY = POLICY
    core.METALS_XAU_LONG_MFE50_POLICY_VERSION = POLICY_VERSION
    core.METALS_XAU_LONG_MFE50_MIN_HOLD_CANDLES = MIN_HOLD
    core.METALS_XAU_LONG_MFE50_GIVEBACK_FRACTION = 0.50
    core.METALS_XAU_SHORT_ACTIVE_POLICY = XAU_SHORT_POLICY
    core.METALS_XAU_SHORT_ACTIVE_POLICY_VERSION = XAU_SHORT_POLICY_VERSION
    core.METALS_XAU_SHORT_MIN_HOLD_CANDLES = XAU_SHORT_MIN_HOLD
    core.METALS_XAU_SHORT_GIVEBACK_FRACTION = XAU_SHORT_GIVEBACK_FRACTION

    def new_policy(asset: str, side: str) -> Dict[str, str]:
        a = core._metals_demo_asset(asset)
        d = core._metals_demo_side(side)
        if a == "XAUUSD" and d == "long" and getattr(core, "METALS_XAU_LONG_MFE50_ACTIVE_ENABLED", True):
            return {"policy": POLICY, "version": POLICY_VERSION}
        if a == "XAUUSD" and d == "short":
            return {"policy": XAU_SHORT_POLICY, "version": XAU_SHORT_POLICY_VERSION}
        return {"policy": "CURRENT_MANAGER", "version": core.METALS_DEMO_MANAGER_VERSION}

    def decision(metrics: Dict[str, Any], link: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        policy = core._metals_demo_link_exit_policy(link)
        if policy == XAU_SHORT_POLICY:
            return _xau_short_mfe25(core, metrics)
        if policy == "MFE_GIVEBACK_50":
            return _old_mfe50(core, metrics)
        if policy != POLICY:
            d = core._metals_demo_current_manager_decision(metrics)
            d["active_exit_policy"] = "CURRENT_MANAGER"
            return d
        h = int(metrics.get("hold_candles") or 0)
        phase = core._metals_demo_phase(h)
        if h < MIN_HOLD:
            return {"decision": "HOLD_MIN_48", "phase": phase, "active_exit_policy": POLICY, "reason": "XAU ATR2 policy: normal exit locked before 48h; emergency broker stop remains active."}
        t = _trail(core, link or {}, metrics)
        if t.get("ok") and t.get("latest_low") is not None and t["latest_low"] <= t["trail_price"]:
            return {"decision": "CLOSE_ATR2_CHANDELIER", "phase": phase, "active_exit_policy": POLICY, **t, "reason": f"XAU ATR2 trail hit at {t['trail_price']:.3f}; ATR={t['atr_pct']:.4f}%."}
        return {"decision": "EXTEND", "phase": phase, "active_exit_policy": POLICY, **t, "reason": "XAU ATR2 active: retain the 2×ATR trail and review next hourly signal."}

    def stop_candidate(link: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        policy = core.safe_str(link.get("active_exit_policy")).upper()
        side = core.safe_str(link.get("side")).lower()
        if policy == XAU_SHORT_POLICY and side == "short":
            return _xau_short_stop_candidate(core, link, metrics)
        if policy != POLICY:
            return original_stop(link, metrics)
        h = int(metrics.get("hold_candles") or 0)
        if h < MIN_HOLD:
            return {"eligible": False, "reason": "xau_atr2_pre48"}
        t = _trail(core, link, metrics)
        if not t.get("ok"):
            return {"eligible": False, "reason": t.get("reason")}
        current = _num(core, metrics.get("current_price"))
        previous = _num(core, link.get("current_stop_price")) or _num(core, link.get("stop_price"))
        step = (_num(core, link.get("entry_price")) or 0.0) * (_num(core, getattr(core, "METALS_DEMO_MANAGER_MIN_STOP_STEP_PCT", 0.02)) or 0.02) / 100.0
        if current is None or t["trail_price"] >= current:
            return {"eligible": False, "reason": "atr2_trail_at_or_above_current_price", **t}
        if previous is not None and t["trail_price"] <= previous + step:
            return {"eligible": False, "reason": "atr2_would_not_tighten_enough", **t}
        return {"eligible": True, "stop_price": t["trail_price"], "reason": f"LIVE XAU ATR2 trail {t['trail_price']:.3f} ({t['atr_pct']:.4f}% ATR)", **t}

    def status() -> Dict[str, Any]:
        d = original_status()
        active = d.setdefault("active_execution", {})
        active.update({
            "XAUUSD_LONG_new_trades": POLICY,
            "XAUUSD_SHORT_new_trades": XAU_SHORT_POLICY,
            "xau_long_atr2_min_hold_candles": MIN_HOLD,
            "xau_long_atr2_multiplier": ATR_MULTIPLIER,
            "xau_short_mfe25_min_hold_candles": XAU_SHORT_MIN_HOLD,
            "xau_short_mfe25_giveback_fraction": XAU_SHORT_GIVEBACK_FRACTION,
            "xau_short_broker_execution": "BLOCKED_BY_EXISTING_LONG_ONLY_LIVE_GUARD_PENDING_SIDE_SAFE_PROMOTION",
            "hard_sl_remains_active": True,
            "basket_defence_remains_active": True,
        })
        sh = d.setdefault("forward_shadow", {})
        sh["XAUUSD_LONG"] = ["ATR2_CHANDELIER", "MFE_GIVEBACK_25", "MFE_GIVEBACK_50", "MFE_GIVEBACK_75", "FIXED_120H"]
        sh["XAGUSD_LONG"] = ["MFE_GIVEBACK_25", "MFE_GIVEBACK_50", "MFE_GIVEBACK_75", "ATR2_CHANDELIER"]
        sh["XAUUSD_SHORT"] = ["MFE_GIVEBACK_50", "ATR2_CHANDELIER"]
        sh["XAGUSD_SHORT"] = ["MFE_GIVEBACK_25", "MFE_GIVEBACK_50", "ATR2_CHANDELIER"]
        d["promotion_note"] = "XAU SHORT MFE25 is the active policy for new XAU short practice/research-manager trades; broker entry guard remains intentionally intact until the long-only order path is made side-safe."
        return d

    def live_config_status() -> Dict[str, Any]:
        d = original_live_config() if callable(original_live_config) else {}
        d["xau_short_policy"] = XAU_SHORT_POLICY
        d["xau_short_policy_version"] = XAU_SHORT_POLICY_VERSION
        d["xau_short_min_hold_candles"] = XAU_SHORT_MIN_HOLD
        d["xau_short_mfe_giveback_fraction"] = XAU_SHORT_GIVEBACK_FRACTION
        d["xau_short_policy_promoted"] = True
        d["xau_short_broker_orders_allowed"] = False
        d["xau_short_broker_block_reason"] = "core_live_xau_lane_is_long_only_and_requires_side-safe_order/stop promotion"
        return d

    core._metals_demo_new_trade_exit_policy = new_policy
    core._metals_demo_decision = decision
    core._metals_xau_live_stop_candidate = stop_candidate
    core.metals_exit_policy_status = status
    if callable(original_live_config):
        core.metals_xau_live_config_status = live_config_status
    core.METALS_EXIT_SHADOW_EXECUTION_AUTHORITY = False
    core.METALS_EXIT_SHADOW_VERSION = "metals_exit_shadow_v3_xau_short_mfe25_promoted_2026_10_06"
    return {
        "installed": True,
        "xau_long_policy": POLICY,
        "xau_long_version": POLICY_VERSION,
        "xau_short_policy": XAU_SHORT_POLICY,
        "xau_short_version": XAU_SHORT_POLICY_VERSION,
        "xau_short_min_hold_candles": XAU_SHORT_MIN_HOLD,
        "xau_short_giveback_fraction": XAU_SHORT_GIVEBACK_FRACTION,
        "xau_short_broker_orders_allowed": False,
        "xag_lanes_unchanged": True,
    }
