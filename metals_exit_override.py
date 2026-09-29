"""Live XAU-long exit-policy override.

Strategy:
- XAUUSD LONG only
- 48 hourly candles minimum hold
- 2x ATR trailing protection after maturity
- existing emergency broker stop and basket harvesting remain authoritative
- XAG long/short and XAU short remain CURRENT_MANAGER/research-only
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


POLICY = "ATR2_CHANDELIER"
POLICY_VERSION = "metals_xau_long_atr2_48h_v1_2026_09_29"
MIN_HOLD = 48
ATR_MULTIPLIER = 2.0


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


def install(core: Any) -> Dict[str, Any]:
    original_stop = core._metals_xau_live_stop_candidate
    original_status = core.metals_exit_policy_status

    core.METALS_XAU_LONG_MFE50_POLICY = POLICY
    core.METALS_XAU_LONG_MFE50_POLICY_VERSION = POLICY_VERSION
    core.METALS_XAU_LONG_MFE50_MIN_HOLD_CANDLES = MIN_HOLD
    core.METALS_XAU_LONG_MFE50_GIVEBACK_FRACTION = 0.50

    def new_policy(asset: str, side: str) -> Dict[str, str]:
        a = core._metals_demo_asset(asset)
        d = core._metals_demo_side(side)
        if a == "XAUUSD" and d == "long" and getattr(core, "METALS_XAU_LONG_MFE50_ACTIVE_ENABLED", True):
            return {"policy": POLICY, "version": POLICY_VERSION}
        return {"policy": "CURRENT_MANAGER", "version": core.METALS_DEMO_MANAGER_VERSION}

    def decision(metrics: Dict[str, Any], link: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        policy = core._metals_demo_link_exit_policy(link)
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
        active.update({"XAUUSD_LONG_new_trades": POLICY, "xau_long_atr2_min_hold_candles": MIN_HOLD,
                       "xau_long_atr2_multiplier": ATR_MULTIPLIER, "hard_sl_remains_active": True,
                       "basket_defence_remains_active": True})
        sh = d.setdefault("forward_shadow", {})
        sh["XAUUSD_LONG"] = ["ATR2_CHANDELIER", "MFE_GIVEBACK_25", "MFE_GIVEBACK_50", "MFE_GIVEBACK_75", "FIXED_120H"]
        sh["XAGUSD_LONG"] = ["MFE_GIVEBACK_25", "MFE_GIVEBACK_50", "MFE_GIVEBACK_75", "ATR2_CHANDELIER"]
        sh["XAUUSD_SHORT"] = ["MFE_GIVEBACK_25", "MFE_GIVEBACK_50", "ATR2_CHANDELIER"]
        sh["XAGUSD_SHORT"] = ["MFE_GIVEBACK_25", "MFE_GIVEBACK_50", "ATR2_CHANDELIER"]
        return d

    core._metals_demo_new_trade_exit_policy = new_policy
    core._metals_demo_decision = decision
    core._metals_xau_live_stop_candidate = stop_candidate
    core.metals_exit_policy_status = status
    core.METALS_EXIT_SHADOW_EXECUTION_AUTHORITY = False
    core.METALS_EXIT_SHADOW_VERSION = "metals_exit_shadow_v2_xau_atr2_48h_2026_09_29"
    return {"installed": True, "policy": POLICY, "version": POLICY_VERSION, "min_hold_candles": MIN_HOLD,
            "atr_multiplier": ATR_MULTIPLIER, "practice_lanes_unchanged": True}
