"""Broker-minimum-safe implementation of XAU post-32 half exposure.

The approved adaptive policy targets half normal exposure after campaign entry #32.
OANDA XAU currently enforces a 0.1-unit minimum, while the existing adaptive
implementation halves an already-minimum 0.1-unit order to 0.05.  That is not a
valid broker order.

When true half-size is below the broker minimum, this overlay preserves the same
~0.5 average overflow exposure by:
- keeping each executed overflow order at the valid normal broker size; and
- executing every other otherwise-qualified post-32 overflow opportunity.

The decision is persisted per active XAU cycle + side + raw signal so retries are
idempotent.  Funding (+2R), 4h short reconfirmation, individual exits, harvest,
and the 200-trade technical fail-safe remain owned by the existing adaptive
router. Existing positions are never resized.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, Tuple

import metals_adaptive_stacking_override as adaptive
import metals_strategy_governance_override as governance

VERSION = "metals_xau_overflow_broker_min_v1_2026_10_14"
STATE_KEY = "metals_xau_overflow_half_frequency_state_v1"
FULL_SIZE_THROUGH = 32
TARGET_EXPOSURE_MULTIPLIER = 0.50
_LOCK = threading.RLock()


def _f(v: Any, default: float | None = None) -> float | None:
    try:
        return float(v)
    except Exception:
        return default


def _half_preview(core: Any, side: str) -> Dict[str, Any]:
    token = adaptive._ENTRY_SIZE_MULTIPLIER.set(TARGET_EXPOSURE_MULTIPLIER)
    try:
        return dict(core._metals_xau_side_sizing_preview(side) or {})
    finally:
        adaptive._ENTRY_SIZE_MULTIPLIER.reset(token)


def _base_preview(core: Any, side: str) -> Dict[str, Any]:
    token = adaptive._ENTRY_SIZE_MULTIPLIER.set(1.0)
    try:
        return dict(core._metals_xau_side_sizing_preview(side) or {})
    finally:
        adaptive._ENTRY_SIZE_MULTIPLIER.reset(token)


def _minimum_binding(core: Any) -> Tuple[bool, Dict[str, Any]]:
    checks: Dict[str, Any] = {}
    needs_frequency = False
    for side in ("long", "short"):
        base = _base_preview(core, side)
        half = _half_preview(core, side)
        minimum = _f(base.get("minimum_trade_size"))
        base_units = abs(_f(base.get("units"), 0.0) or 0.0)
        half_units = abs(_f(half.get("units"), 0.0) or 0.0)
        invalid_half = bool(minimum is not None and minimum > 0 and half_units + 1e-12 < minimum)
        needs_frequency = needs_frequency or invalid_half
        checks[side] = {
            "base_units": base_units,
            "half_units_from_legacy_multiplier": half_units,
            "minimum_trade_size": minimum,
            "legacy_half_below_minimum": invalid_half,
            "base_executable": bool(base.get("executable", False)),
        }
    return needs_frequency, checks


def _load_state(core: Any) -> Dict[str, Any]:
    raw = str(core.get_broker_runtime_setting(STATE_KEY, "") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return dict(parsed) if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _save_state(core: Any, state: Dict[str, Any]) -> bool:
    payload = json.dumps(state, separators=(",", ":"), sort_keys=True)
    core.set_broker_runtime_setting(STATE_KEY, payload)
    check = str(core.get_broker_runtime_setting(STATE_KEY, "") or "").strip()
    try:
        return dict(json.loads(check)) == state
    except Exception:
        return False


def _opportunity_decision(core: Any, cycle_id: str, side: str, raw_signal_id: int) -> Dict[str, Any]:
    campaign_key = f"{cycle_id or 'NO_CYCLE'}|{side}"
    rid = str(int(raw_signal_id))
    with _LOCK:
        state = _load_state(core)
        if str(state.get("campaign_key") or "") != campaign_key:
            state = {"campaign_key": campaign_key, "counter": 0, "decisions": {}}
        decisions = dict(state.get("decisions") or {})
        if rid in decisions:
            d = dict(decisions[rid] or {})
            return {
                "ok": True,
                "reused": True,
                "campaign_key": campaign_key,
                "opportunity": int(d.get("opportunity") or 0),
                "allow": bool(d.get("allow")),
            }

        counter = int(state.get("counter") or 0) + 1
        # Allow the first qualified overflow opportunity, skip the second, etc.
        # Historical parity replay showed both parities near the intended 0.5-size
        # result; starting with ALLOW avoids unnecessarily delaying a strong trend.
        allow = (counter % 2) == 1
        decisions[rid] = {"opportunity": counter, "allow": allow}
        state = {"campaign_key": campaign_key, "counter": counter, "decisions": decisions}
        if not _save_state(core, state):
            return {"ok": False, "reason": "overflow_half_frequency_state_did_not_persist", "campaign_key": campaign_key}
        return {
            "ok": True,
            "reused": False,
            "campaign_key": campaign_key,
            "opportunity": counter,
            "allow": allow,
        }


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(core, "_METALS_XAU_BROKER_SAFE_OVERFLOW_INSTALLED", False):
        return dict(getattr(core, "_METALS_XAU_BROKER_SAFE_OVERFLOW_STATUS", {}))

    original_router = core.execute_metals_demo_candidate
    if not callable(original_router):
        raise RuntimeError("Broker-safe overflow install refused: adaptive router unavailable")
    if str(getattr(core, "METALS_XAU_ADAPTIVE_STACKING_VERSION", "") or "") != adaptive.VERSION:
        raise RuntimeError("Broker-safe overflow install refused: expected adaptive stacking version not active")

    frequency_mode, preview_checks = _minimum_binding(core)
    if not frequency_mode:
        status = {
            "installed": True,
            "version": VERSION,
            "mode": "TRUE_HALF_SIZE_VALID",
            "target_exposure_multiplier": TARGET_EXPOSURE_MULTIPLIER,
            "preview_checks": preview_checks,
            "router_wrapped": False,
            "existing_positions_resized": False,
        }
        core._METALS_XAU_BROKER_SAFE_OVERFLOW_INSTALLED = True
        core._METALS_XAU_BROKER_SAFE_OVERFLOW_STATUS = status
        return status

    # Prevent the inner adaptive router from producing sub-minimum units.  The
    # outer wrapper below supplies the 0.5 average exposure by opportunity
    # thinning instead, so accepted orders remain at normal valid size.
    previous_multiplier = float(adaptive.OVERFLOW_SIZE_MULTIPLIER)
    adaptive.OVERFLOW_SIZE_MULTIPLIER = 1.0

    def broker_safe_router(raw_signal_id: int, source: str = "signal_worker") -> Dict[str, Any]:
        try:
            c = adaptive._candidate(core, int(raw_signal_id))
        except Exception:
            return original_router(int(raw_signal_id), source=source)
        if not adaptive._is_live_xau_candidate(core, c):
            return original_router(int(raw_signal_id), source=source)
        side = str(c.get("side") or "").lower()

        # Idempotence after a successful execution remains owned by the live link.
        try:
            with core.get_conn() as conn:
                existing = conn.execute(
                    "SELECT id FROM metals_xau_live_trade_links WHERE raw_signal_id=? LIMIT 1",
                    (int(raw_signal_id),),
                ).fetchone()
            if existing:
                return original_router(int(raw_signal_id), source=source)
        except Exception:
            pass

        open_rows = adaptive._open_links(core)
        open_same = [r for r in open_rows if str(r.get("side") or "").lower() == side]
        open_opposite = [r for r in open_rows if str(r.get("side") or "").lower() not in {"", side}]
        if open_opposite:
            return original_router(int(raw_signal_id), source=source)

        campaign = adaptive._campaign_entry_count(core, side, open_rows)
        ordinal = int(campaign.get("count") or 0) + 1
        if ordinal <= FULL_SIZE_THROUGH:
            return original_router(int(raw_signal_id), source=source)

        # Do not consume an overflow opportunity while the existing adaptive
        # router would reject it on short-transition or +2R funding grounds.
        if side == "short":
            cooldown = governance._short_transition(core, "XAUUSD", int(raw_signal_id))
            if not cooldown.get("ok") or cooldown.get("cooldown_active"):
                return original_router(int(raw_signal_id), source=source)

        funding = adaptive._campaign_funding(core, side, open_rows)
        if not funding.get("ok") or float(funding.get("campaign_r") or 0.0) < float(adaptive.OVERFLOW_MIN_CAMPAIGN_R):
            return original_router(int(raw_signal_id), source=source)

        # If even the normal order itself is not executable, leave the decision
        # to the authoritative inner router without advancing the thinning state.
        normal_preview = _base_preview(core, side)
        if normal_preview.get("executable") is False:
            return original_router(int(raw_signal_id), source=source)

        cycle_id = str(campaign.get("cycle_id") or funding.get("cycle_id") or "")
        decision = _opportunity_decision(core, cycle_id, side, int(raw_signal_id))
        detail = {
            "version": VERSION,
            "mode": "HALF_FREQUENCY_FULL_VALID_SIZE",
            "entry_ordinal_if_executed": ordinal,
            "target_average_exposure_multiplier": TARGET_EXPOSURE_MULTIPLIER,
            "campaign": campaign,
            "campaign_funding": funding,
            "decision": decision,
            "normal_units": normal_preview.get("units"),
            "minimum_trade_size": normal_preview.get("minimum_trade_size"),
            "existing_trades_unchanged": True,
        }
        if not decision.get("ok"):
            adaptive._audit_block(core, raw_signal_id, "xau_overflow_half_frequency_state_unavailable", detail)
            return {"ok": True, "blocked": True, "asset": "XAUUSD", "side": side, "reason": "xau_overflow_half_frequency_state_unavailable", "broker_safe_overflow": detail}
        if not decision.get("allow"):
            adaptive._audit_block(core, raw_signal_id, "xau_overflow_half_frequency_skip", detail)
            return {"ok": True, "blocked": True, "asset": "XAUUSD", "side": side, "reason": "xau_overflow_half_frequency_skip", "broker_safe_overflow": detail}

        result = original_router(int(raw_signal_id), source=source)
        if isinstance(result, dict):
            result = dict(result)
            result["broker_safe_overflow"] = detail
        return result

    core.execute_metals_demo_candidate = broker_safe_router

    previous_demo_status = core.metals_demo_config_status
    def demo_status() -> Dict[str, Any]:
        d = dict(previous_demo_status() or {})
        stacking = dict(d.get("stacking_governance") or {})
        stacking.update({
            "xau_live_overflow_exposure_target": 0.50,
            "xau_live_overflow_execution_mode": "HALF_FREQUENCY_FULL_VALID_SIZE_WHILE_BROKER_MINIMUM_BINDS",
            "xau_live_overflow_true_half_units_invalid_at_install": True,
        })
        d["stacking_governance"] = stacking
        return d
    core.metals_demo_config_status = demo_status

    previous_governance_status = core.metals_strategy_governance_status
    def governance_status() -> Dict[str, Any]:
        d = dict(previous_governance_status() or {})
        row = dict(d.get("adaptive_xau_stacking") or {})
        row.update({
            "overflow_target_exposure_multiplier_after_32": 0.50,
            "overflow_broker_safe_mode": "HALF_FREQUENCY_FULL_VALID_SIZE",
            "overflow_reason": "true 0.5 unit multiplier falls below XAU broker minimum at current account sizing",
        })
        d["adaptive_xau_stacking"] = row
        return d
    core.metals_strategy_governance_status = governance_status

    # Replace the two adaptive status routes so operational inspection cannot
    # misreport the legacy physical 0.5-unit multiplier as the live mechanism.
    for path in ("/api/metals-strategy-governance", "/api/metals-adaptive-stacking"):
        for route in [r for r in list(app.router.routes) if getattr(r, "path", None) == path]:
            app.router.routes.remove(route)
        app.add_api_route(path, governance_status, methods=["GET"])

    checks = {
        "frequency_mode_required": frequency_mode,
        "inner_physical_multiplier_disabled": abs(float(adaptive.OVERFLOW_SIZE_MULTIPLIER) - 1.0) < 1e-12,
        "outer_router_installed": core.execute_metals_demo_candidate is broker_safe_router,
        "target_average_exposure_half": abs(TARGET_EXPOSURE_MULTIPLIER - 0.50) < 1e-12,
        "xau_long_atr2_unchanged": core._metals_demo_new_trade_exit_policy("XAUUSD", "long").get("policy") == "ATR2_CHANDELIER",
        "xau_short_mfe25_unchanged": str(getattr(core, "METALS_XAU_SHORT_ACTIVE_POLICY", "")) == "MFE_GIVEBACK_25",
    }
    if not all(checks.values()):
        adaptive.OVERFLOW_SIZE_MULTIPLIER = previous_multiplier
        core.execute_metals_demo_candidate = original_router
        raise RuntimeError(f"Broker-safe overflow startup self-test failed: {checks}")

    status = {
        "installed": True,
        "version": VERSION,
        "mode": "HALF_FREQUENCY_FULL_VALID_SIZE",
        "target_exposure_multiplier": TARGET_EXPOSURE_MULTIPLIER,
        "full_size_through_entry_ordinal": FULL_SIZE_THROUGH,
        "preview_checks": preview_checks,
        "legacy_physical_half_multiplier": previous_multiplier,
        "inner_execution_multiplier_now": float(adaptive.OVERFLOW_SIZE_MULTIPLIER),
        "existing_positions_resized": False,
        "startup_self_test": checks,
        "state_key": STATE_KEY,
    }
    core.METALS_XAU_OVERFLOW_BROKER_SAFE_VERSION = VERSION
    core.METALS_XAU_OVERFLOW_EXECUTION_MODE = "HALF_FREQUENCY_FULL_VALID_SIZE"
    core._METALS_XAU_BROKER_SAFE_OVERFLOW_INSTALLED = True
    core._METALS_XAU_BROKER_SAFE_OVERFLOW_STATUS = status

    @app.get("/analysis/live-xau-overflow-sizing")
    def live_xau_overflow_sizing() -> Dict[str, Any]:
        out = dict(status)
        out["current_base_preview_long"] = _base_preview(core, "long")
        out["current_base_preview_short"] = _base_preview(core, "short")
        return out

    return status
