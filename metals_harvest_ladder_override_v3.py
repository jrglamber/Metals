"""Fail-closed live-XAU harvest ladder overlay.

Changes only the live-XAU harvest worker to 30R first, then +50R (80/130/...)
with 20% / 20% / 25%+ banking. Practice/XAG ladder constants remain untouched.
"""
from __future__ import annotations

import inspect
import textwrap
from typing import Any, Dict

import metals_harvest_ladder_override as common

VERSION = common.VERSION


def _replace_required(src: str, old: str, new: str) -> tuple[str, int]:
    n = src.count(old)
    if n < 1:
        raise RuntimeError(f"XAU harvest v3 refused: expected runtime token {old}")
    return src.replace(old, new), n


def _patch_live_unlocked(core: Any) -> Dict[str, Any]:
    original = getattr(core, "_metals_xau_live_harvest_maintenance_tick_unlocked", None)
    if not callable(original):
        raise RuntimeError("XAU harvest v3 refused: live unlocked harvest worker unavailable")
    src = textwrap.dedent(inspect.getsource(original))
    old_header = "def _metals_xau_live_harvest_maintenance_tick_unlocked("
    if src.count(old_header) != 1:
        raise RuntimeError("XAU harvest v3 refused: live unlocked worker source shape changed")
    src = src.replace(old_header, "def _metals_xau_live_harvest_maintenance_tick_v3_unlocked(", 1)

    counts: Dict[str, int] = {}
    for old, new in (
        ("METALS_HARVEST_FIRST_LEVEL_R", "METALS_XAU_HARVEST_V2_FIRST_LEVEL_R"),
        ("_metals_harvest_level_sequence", "_metals_xau_harvest_v2_level_sequence"),
        ("_metals_harvest_fraction_for_level", "_metals_xau_harvest_v2_fraction_for_level"),
    ):
        src, counts[old] = _replace_required(src, old, new)

    core.METALS_XAU_HARVEST_V2_FIRST_LEVEL_R = common.FIRST_LEVEL_R
    core.METALS_XAU_HARVEST_V2_STEP_R = common.STEP_R
    core._metals_xau_harvest_v2_level_sequence = common._level_sequence
    core._metals_xau_harvest_v2_fraction_for_level = common._fraction_for_level
    ns = core.__dict__
    exec(compile(src, "<metals-xau-harvest-v3-unlocked>", "exec"), ns, ns)
    patched = ns.get("_metals_xau_live_harvest_maintenance_tick_v3_unlocked")
    if not callable(patched):
        raise RuntimeError("XAU harvest v3 refused: transformed worker unavailable")
    core._metals_xau_live_harvest_maintenance_tick_unlocked = patched
    return {"patched_unlocked_worker": True, "replacement_counts": counts}


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(core, "_METALS_XAU_HARVEST_V3_INSTALLED", False):
        return dict(getattr(core, "_METALS_XAU_HARVEST_V3_STATUS", {}))

    # Label future live-XAU stages before any worker can run under the new ladder.
    core.METALS_XAU_LIVE_HARVEST_POLICY_VERSION = VERSION
    patch = _patch_live_unlocked(core)

    # Baseline the basket already in progress so lowering the first rung can never
    # trigger a delayed close merely because this release was deployed.
    baseline = common._baseline_existing_campaign(core)
    common._install_plan_snapshot(core)
    route = common._replace_dashboard_route(core, app)

    checks = {
        "first_30r": common.FIRST_LEVEL_R == 30.0,
        "step_50r": common.STEP_R == 50.0,
        "fractions_20_20_25": [common._fraction_for_level(30), common._fraction_for_level(80), common._fraction_for_level(130)] == [0.20, 0.20, 0.25],
        "next_30_to_80": common._next_level(30) == 80.0,
        "next_80_to_130": common._next_level(80) == 130.0,
        "practice_first_still_50": float(getattr(core, "METALS_HARVEST_FIRST_LEVEL_R", 50.0)) == 50.0,
        "practice_step_still_50": float(getattr(core, "METALS_HARVEST_STEP_R", 50.0)) == 50.0,
        "xau_long_atr2": str(core._metals_demo_new_trade_exit_policy("XAUUSD", "long").get("policy")) == "ATR2_CHANDELIER",
        "xau_short_mfe25": str(getattr(core, "METALS_XAU_SHORT_ACTIVE_POLICY", "MFE_GIVEBACK_25")) == "MFE_GIVEBACK_25",
        "adaptive_stacking_present": bool(getattr(core, "METALS_XAU_ADAPTIVE_STACKING_VERSION", "")),
        "live_worker_replaced": getattr(core._metals_xau_live_harvest_maintenance_tick_unlocked, "__name__", "") == "_metals_xau_live_harvest_maintenance_tick_v3_unlocked",
    }
    if not all(checks.values()):
        raise RuntimeError(f"XAU harvest v3 startup self-test failed: {checks}")

    status = {
        "installed": True,
        "version": VERSION,
        "scope": "LIVE_XAU_ONLY",
        "levels": [30, 80, 130, 180, 230, 280],
        "continues_every_r": 50,
        "fractions": [0.20, 0.20, 0.25],
        "non_retroactive_baseline": baseline,
        "practice_harvest_unchanged": True,
        "individual_exit_managers_unchanged": True,
        "worker_patch": patch,
        "dashboard_route": route,
        "startup_self_test": checks,
    }
    core._METALS_XAU_HARVEST_V3_INSTALLED = True
    core._METALS_XAU_HARVEST_V3_STATUS = status

    @app.get("/api/live-xau-harvest-policy-v3")
    def policy_status() -> Dict[str, Any]:
        out = dict(status)
        try:
            out["plan"] = core.metals_xau_live_harvest_plan_snapshot()
        except Exception as exc:
            out["plan_error"] = f"{type(exc).__name__}:{exc}"
        return out

    return status
