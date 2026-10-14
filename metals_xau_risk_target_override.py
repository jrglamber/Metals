"""Approved live-XAU base-risk target overlay.

Promoted 2026-10-14 after sizing study and explicit user approval:
- normal live-XAU entries target 0.100% of fresh NAV;
- adaptive stack entries after campaign trade #32 remain half-size via the
  existing adaptive stacking multiplier, therefore 0.050% of NAV target;
- existing open trades are never resized;
- this overlay changes the risk-control *target* only. The persisted cash-risk
  amount changes only when the existing authenticated /control/risk-per-trade
  POST is explicitly applied from the Portfolio Hub.
"""
from __future__ import annotations

from typing import Any, Dict

VERSION = "metals_xau_base_risk_0p100_v1_2026_10_14"
BASE_RISK_PCT_OF_NAV = 0.10
OVERFLOW_MULTIPLIER = 0.50
OVERFLOW_RISK_PCT_OF_NAV = BASE_RISK_PCT_OF_NAV * OVERFLOW_MULTIPLIER


def _f(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except Exception:
        return default


def install(core: Any, app: Any) -> Dict[str, Any]:
    routes = [r for r in list(getattr(app, "routes", [])) if str(getattr(r, "path", "")) == "/control/risk-per-trade"]
    if len(routes) != 2:
        raise RuntimeError(f"XAU risk target install refused: expected GET+POST risk routes, found {len(routes)}")

    endpoint_globals = []
    methods_seen = set()
    previous_values = []
    for route in routes:
        methods = set(getattr(route, "methods", []) or [])
        methods_seen |= methods
        endpoint = getattr(route, "endpoint", None)
        glb = getattr(endpoint, "__globals__", None)
        if not isinstance(glb, dict) or "METALS_XAU_FIXED_RISK_PCT_OF_NAV" not in glb:
            raise RuntimeError("XAU risk target install refused: risk endpoint global target not found")
        previous_values.append(_f(glb.get("METALS_XAU_FIXED_RISK_PCT_OF_NAV")))
        endpoint_globals.append(glb)

    if "GET" not in methods_seen or "POST" not in methods_seen:
        raise RuntimeError(f"XAU risk target install refused: route methods incomplete {sorted(methods_seen)}")
    if any(v is None or v <= 0 or v > 1.0 for v in previous_values):
        raise RuntimeError(f"XAU risk target install refused: unexpected previous values {previous_values}")

    # The two endpoints normally share the same module globals. Setting both is
    # harmless and makes the install robust if they are ever split.
    for glb in endpoint_globals:
        glb["METALS_XAU_FIXED_RISK_PCT_OF_NAV"] = BASE_RISK_PCT_OF_NAV

    # Validate the GET/snapshot calculation without invoking the authenticated
    # POST and without touching broker/runtime risk state.
    get_route = next(r for r in routes if "GET" in set(getattr(r, "methods", []) or []))
    get_endpoint = getattr(get_route, "endpoint")
    snap = dict(get_endpoint() or {})
    fixed = _f(snap.get("fixed_risk_pct_of_nav"))
    nav = _f(snap.get("nav_gbp"))
    raw = _f(snap.get("raw_calculated_risk_gbp"))
    target = _f(snap.get("calculated_risk_per_trade_gbp"))
    current = _f(snap.get("current_risk_per_trade_gbp"))
    if fixed is None or abs(fixed - BASE_RISK_PCT_OF_NAV) > 1e-9:
        raise RuntimeError(f"XAU risk target startup self-test failed: GET reports {fixed}")
    if nav is not None and raw is not None:
        expected_raw = nav * (BASE_RISK_PCT_OF_NAV / 100.0)
        if abs(raw - expected_raw) > max(0.02, expected_raw * 0.01):
            raise RuntimeError(f"XAU risk target startup self-test failed: raw={raw} expected={expected_raw}")

    # Adaptive stacking must remain installed and half-size after #32.
    adaptive_version = str(getattr(core, "METALS_XAU_ADAPTIVE_STACKING_VERSION", "") or "")
    if not adaptive_version:
        raise RuntimeError("XAU risk target install refused: adaptive stacking not installed")

    status = {
        "installed": True,
        "version": VERSION,
        "base_risk_pct_of_nav": BASE_RISK_PCT_OF_NAV,
        "overflow_after_trade_32_pct_of_nav": OVERFLOW_RISK_PCT_OF_NAV,
        "overflow_multiplier": OVERFLOW_MULTIPLIER,
        "previous_target_values": previous_values,
        "current_cash_risk_gbp_unchanged_until_manual_apply": current,
        "calculated_target_gbp_now": target,
        "nav_gbp": nav,
        "existing_positions_resized": False,
        "manual_authenticated_apply_still_required": True,
        "adaptive_stacking_version": adaptive_version,
    }
    core.METALS_XAU_BASE_RISK_TARGET_VERSION = VERSION
    core.METALS_XAU_BASE_RISK_PCT_OF_NAV = BASE_RISK_PCT_OF_NAV
    core.METALS_XAU_OVERFLOW_RISK_PCT_OF_NAV = OVERFLOW_RISK_PCT_OF_NAV

    @app.get("/analysis/live-xau-risk-policy")
    def live_xau_risk_policy() -> Dict[str, Any]:
        fresh = dict(status)
        try:
            fresh["risk_control"] = dict(get_endpoint() or {})
            fresh["sizing_preview"] = dict(core.metals_xau_live_sizing_preview() or {})
        except Exception as exc:
            fresh["read_error"] = f"{type(exc).__name__}:{exc}"
        return fresh

    return status
