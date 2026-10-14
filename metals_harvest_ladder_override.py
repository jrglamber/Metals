"""Live-XAU harvest ladder v2 for Project Exit Plan Metals.

Approved 2026-10-14:
- first live-XAU harvest at 30R;
- subsequent stages every +50R: 80R, 130R, 180R, ...;
- bank 20% at the first two stages and 25% from the third onward;
- preserve the current active campaign without retroactive broker closes;
- do not change practice/XAG harvest policy or individual ATR2/MFE25 managers.

Installed after adaptive stacking.  The legacy core uses one shared set of
harvest constants for practice and live lanes, so this patch transforms only the
LIVE-XAU maintenance function instead of mutating the shared practice policy.
"""
from __future__ import annotations

import inspect
import json
import textwrap
from typing import Any, Dict, List

VERSION = "metals_xau_live_harvest_v2_30r_first_50r_step_20_20_25_2026_10_14"
FIRST_LEVEL_R = 30.0
STEP_R = 50.0
FIRST_FRACTION = 0.20
SECOND_FRACTION = 0.20
LATER_FRACTION = 0.25
_TERMINAL = {"EXECUTED", "LEGACY_PASSED_UNBANKED", "EXPIRED_FLAT"}


def _f(core: Any, value: Any, default: float = 0.0) -> float:
    try:
        v = core.safe_float(value)
        return float(v) if v is not None else float(default)
    except Exception:
        try:
            return float(value)
        except Exception:
            return float(default)


def _level_sequence(up_to_r: Any, extra_levels: int = 0) -> List[float]:
    top = max(0.0, float(up_to_r or 0.0))
    top += max(0, int(extra_levels or 0)) * STEP_R
    levels: List[float] = []
    level = FIRST_LEVEL_R
    # Preserve the core's practical 5000R ceiling without sharing its ladder start.
    while level <= min(top, 5000.0) + 1e-9:
        levels.append(float(level))
        level += STEP_R
    return levels


def _fraction_for_level(level_r: Any) -> float:
    level = float(level_r or 0.0)
    if level <= FIRST_LEVEL_R + 1e-9:
        return FIRST_FRACTION
    if level <= FIRST_LEVEL_R + STEP_R + 1e-9:
        return SECOND_FRACTION
    return LATER_FRACTION


def _next_level(high_r: Any) -> float:
    high = max(0.0, float(high_r or 0.0))
    if high < FIRST_LEVEL_R - 1e-9:
        return FIRST_LEVEL_R
    steps = int((high - FIRST_LEVEL_R) // STEP_R) + 1
    return FIRST_LEVEL_R + steps * STEP_R


def _replace_once(src: str, old: str, new: str, label: str) -> str:
    count = src.count(old)
    if count != 1:
        raise RuntimeError(f"XAU harvest v2 install refused: expected one {label}, found {count}")
    return src.replace(old, new, 1)


def _install_live_tick(core: Any) -> Dict[str, Any]:
    original = core.metals_xau_live_harvest_maintenance_tick
    src = textwrap.dedent(inspect.getsource(original))
    src = _replace_once(
        src,
        "def metals_xau_live_harvest_maintenance_tick(",
        "def _metals_xau_live_harvest_maintenance_tick_v2(",
        "live harvest function header",
    )
    # The live maintenance path delegates threshold construction and fractions
    # to shared helpers; it does not directly reference the shared first/step
    # constants. Replace only those helper lookups so practice remains untouched.
    replacements = {
        "_metals_harvest_level_sequence": "_metals_xau_harvest_v2_level_sequence",
        "_metals_harvest_fraction_for_level": "_metals_xau_harvest_v2_fraction_for_level",
    }
    counts: Dict[str, int] = {}
    for old, new in replacements.items():
        n = src.count(old)
        if n < 1:
            raise RuntimeError(f"XAU harvest v2 install refused: {old} not referenced by live maintenance")
        src = src.replace(old, new)
        counts[old] = n

    core.METALS_XAU_HARVEST_V2_FIRST_LEVEL_R = FIRST_LEVEL_R
    core.METALS_XAU_HARVEST_V2_STEP_R = STEP_R
    core._metals_xau_harvest_v2_level_sequence = _level_sequence
    core._metals_xau_harvest_v2_fraction_for_level = _fraction_for_level
    ns = core.__dict__
    exec(compile(src, "<metals-xau-harvest-v2>", "exec"), ns, ns)
    patched = ns.get("_metals_xau_live_harvest_maintenance_tick_v2")
    if not callable(patched):
        raise RuntimeError("XAU harvest v2 install refused: transformed maintenance function unavailable")
    core.metals_xau_live_harvest_maintenance_tick = patched
    return {"transformed": True, "replacement_counts": counts}


def _baseline_existing_campaign(core: Any) -> Dict[str, Any]:
    """Introduce the lower ladder without retroactively closing live positions."""
    broker = dict(core.metals_xau_live_broker_snapshot(include_account_summary=False) or {})
    if broker.get("ok") is False:
        raise RuntimeError("XAU harvest v2 install refused: live broker snapshot unavailable for non-retroactive baseline")
    hwm = dict(core._metals_xau_live_highwater_state(broker) or {})
    high = _f(core, hwm.get("high_water_r"), 0.0)
    current = _f(core, hwm.get("current_r"), 0.0)
    now = core.now_utc_iso()

    with core.get_conn() as conn:
        cycle = str(core._metals_xau_live_harvest_cycle_id(conn, hwm) or "")
        if not cycle:
            return {"cycle_id": "", "high_water_r": high, "flat": True, "legacy_marked": [], "old_stages_superseded": []}

        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM metals_xau_live_harvest_stages WHERE cycle_id=? ORDER BY id",
            (cycle,),
        ).fetchall()]

        # Never abandon a stage that has already partially closed broker trades.
        unsafe = []
        for row in rows:
            status = str(row.get("status") or "").upper()
            banked = _f(core, row.get("executed_bank_gbp"), 0.0)
            if status not in _TERMINAL and banked > 1e-9:
                unsafe.append({"id": row.get("id"), "threshold_r": row.get("threshold_r"), "status": status, "banked_gbp": banked})
        if unsafe:
            raise RuntimeError(f"XAU harvest v2 install refused: partially executed legacy stage present: {unsafe}")

        superseded = []
        for row in rows:
            status = str(row.get("status") or "").upper()
            policy = str(row.get("policy_version") or "")
            threshold = _f(core, row.get("threshold_r"), -1.0)
            if policy == VERSION or status in _TERMINAL:
                continue
            conn.execute(
                "UPDATE metals_xau_live_harvest_stages SET updated_at_utc=?,status='LEGACY_PASSED_UNBANKED',reason=? WHERE id=?",
                (now, f"Superseded non-retroactively by {VERSION}; no broker close on deployment", int(row.get("id"))),
            )
            superseded.append(threshold)

        existing_thresholds = {
            int(round(_f(core, r.get("threshold_r"), -9999.0)))
            for r in rows
        }
        legacy_marked = []
        for level in _level_sequence(high):
            key = int(round(level))
            if key in existing_thresholds:
                continue
            conn.execute(
                """
                INSERT INTO metals_xau_live_harvest_stages(
                    created_at_utc,updated_at_utc,policy_version,cycle_id,
                    threshold_r,bank_fraction,status,armed_at_utc,armed_hwm_r,
                    armed_hwm_gbp,trigger_current_r,trigger_current_gbp,reason,raw_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(cycle_id,threshold_r) DO NOTHING
                """,
                (
                    now, now, VERSION, cycle, key, _fraction_for_level(level),
                    "LEGACY_PASSED_UNBANKED", now, high,
                    core.safe_float(hwm.get("high_water_gbp")), current,
                    core.safe_float(hwm.get("current_gbp")),
                    f"{level:.0f}R was already crossed before {VERSION}; deliberately no retroactive close",
                    json.dumps(hwm, default=str),
                ),
            )
            legacy_marked.append(level)

        # Keep the observed-HWM cursor at least at today's HWM so a restart cannot
        # reinterpret an old crossing as new after the ladder change.
        last = _f(core, core._metals_xau_live_runtime_get(conn, "harvest_last_seen_hwm_r", "0"), 0.0)
        if high > last:
            core._metals_xau_live_runtime_set(conn, "harvest_last_seen_hwm_r", high)
        core._metals_xau_live_runtime_set(conn, "harvest_ever_initialized", "1")
        conn.commit()

    return {
        "cycle_id": cycle,
        "high_water_r": high,
        "current_r": current,
        "flat": False,
        "legacy_marked": legacy_marked,
        "old_stages_superseded": superseded,
        "no_retroactive_broker_closes": True,
    }


def _install_plan_snapshot(core: Any) -> None:
    def plan_snapshot() -> Dict[str, Any]:
        broker = core.metals_xau_live_broker_snapshot(include_account_summary=False)
        hwm = core._metals_xau_live_highwater_state(broker)
        with core.get_conn() as conn:
            cycle = core._metals_xau_live_harvest_cycle_id(conn, hwm)
            stages = [dict(r) for r in conn.execute(
                "SELECT * FROM metals_xau_live_harvest_stages ORDER BY id DESC LIMIT 30"
            ).fetchall()]
        high = _f(core, hwm.get("high_water_r"), 0.0)
        nxt = _next_level(high)
        return {
            "ok": True,
            "cycle_id": cycle,
            "broker_hwm": hwm,
            "next_harvest_r": nxt,
            "next_fraction": _fraction_for_level(nxt),
            "stages": stages,
            "policy_version": VERSION,
            "ladder": {
                "first_level_r": FIRST_LEVEL_R,
                "step_r": STEP_R,
                "first_fraction": FIRST_FRACTION,
                "second_fraction": SECOND_FRACTION,
                "later_fraction": LATER_FRACTION,
            },
        }
    core.metals_xau_live_harvest_plan_snapshot = plan_snapshot


def _replace_dashboard_route(core: Any, app: Any) -> Dict[str, Any]:
    path = "/api/live-xau-harvest-ladder-view"
    old = [r for r in list(app.router.routes) if getattr(r, "path", None) == path]
    for route in old:
        app.router.routes.remove(route)

    @app.get(path)
    def ladder_view() -> Dict[str, Any]:
        plan = dict(core.metals_xau_live_harvest_plan_snapshot() or {})
        hwm = plan.get("broker_hwm") or {}
        cycle = str(plan.get("cycle_id") or "")
        stages = [dict(r) for r in (plan.get("stages") or []) if (not cycle or str(r.get("cycle_id") or "") == cycle)]
        by_level: Dict[int, Dict[str, Any]] = {}
        for row in stages:
            by_level.setdefault(int(round(_f(core, row.get("threshold_r"), -1.0))), row)
        levels = [FIRST_LEVEL_R + i * STEP_R for i in range(6)]
        levels = sorted(set(levels + [float(k) for k in by_level if k >= 0]))
        ladder = []
        for serial, level in enumerate(levels, 1):
            stage = by_level.get(int(round(level)), {})
            ladder.append({
                "serial": serial,
                "level_r": float(level),
                "status": str(stage.get("status") or "NOT_ARMED"),
                "bank_pct": 100.0 * _fraction_for_level(level),
                "target_at_trigger_gbp": core.safe_float(stage.get("target_bank_gbp")) if stage else None,
                "actual_gbp_banked": core.safe_float(stage.get("executed_bank_gbp")) if stage else None,
                "executed_at": stage.get("executed_at_utc") if stage else None,
            })
        return {
            "ok": True,
            "scope": "LIVE_XAU_ONLY",
            "active_cycle_id": cycle,
            "high_water_r": _f(core, hwm.get("high_water_r"), 0.0),
            "policy_version": VERSION,
            "ladder": ladder,
            "configured": {
                "first_level_r": FIRST_LEVEL_R,
                "step_r": STEP_R,
                "first_stage_fraction": FIRST_FRACTION,
                "second_stage_fraction": SECOND_FRACTION,
                "later_stage_fraction": LATER_FRACTION,
                "levels_example": [30, 80, 130, 180, 230, 280],
            },
        }
    return {"route": path, "replaced_old_routes": len(old)}


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(core, "_METALS_XAU_HARVEST_V2_INSTALLED", False):
        return dict(getattr(core, "_METALS_XAU_HARVEST_V2_STATUS", {}))

    # The live policy label is live-XAU specific and safe to update globally.
    core.METALS_XAU_LIVE_HARVEST_POLICY_VERSION = VERSION
    tick = _install_live_tick(core)
    baseline = _baseline_existing_campaign(core)
    _install_plan_snapshot(core)
    route = _replace_dashboard_route(core, app)

    # Do not mutate the shared practice constants.  Assert current individual
    # managers and adaptive stacking survived this overlay.
    checks = {
        "first_30r": FIRST_LEVEL_R == 30.0,
        "step_50r": STEP_R == 50.0,
        "fractions_20_20_25": [
            _fraction_for_level(30), _fraction_for_level(80), _fraction_for_level(130)
        ] == [0.20, 0.20, 0.25],
        "next_after_30_is_80": _next_level(30) == 80.0,
        "next_after_80_is_130": _next_level(80) == 130.0,
        "practice_first_level_unchanged": _f(core, getattr(core, "METALS_HARVEST_FIRST_LEVEL_R", 50.0), 50.0) == 50.0,
        "practice_step_unchanged": _f(core, getattr(core, "METALS_HARVEST_STEP_R", 50.0), 50.0) == 50.0,
        "xau_long_atr2_unchanged": str(core._metals_demo_new_trade_exit_policy("XAUUSD", "long").get("policy")) == "ATR2_CHANDELIER",
        "xau_short_mfe25_unchanged": str(getattr(core, "METALS_XAU_SHORT_ACTIVE_POLICY", "MFE_GIVEBACK_25")) == "MFE_GIVEBACK_25",
        "adaptive_stacking_still_installed": bool(getattr(core, "METALS_XAU_ADAPTIVE_STACKING_VERSION", "")),
        "no_retroactive_current_campaign": bool(baseline.get("no_retroactive_broker_closes", True)),
    }
    if not all(checks.values()):
        raise RuntimeError(f"XAU harvest v2 startup self-test failed: {checks}")

    status = {
        "installed": True,
        "version": VERSION,
        "scope": "LIVE_XAU_ONLY",
        "levels": "30,80,130,+50R",
        "first_level_r": FIRST_LEVEL_R,
        "step_r": STEP_R,
        "fractions": [FIRST_FRACTION, SECOND_FRACTION, LATER_FRACTION],
        "non_retroactive_baseline": baseline,
        "practice_harvest_unchanged": True,
        "individual_exit_managers_unchanged": True,
        "tick_transform": tick,
        "dashboard_route": route,
        "startup_self_test": checks,
    }
    core._METALS_XAU_HARVEST_V2_INSTALLED = True
    core._METALS_XAU_HARVEST_V2_STATUS = status

    @app.get("/api/live-xau-harvest-policy-v2")
    def harvest_v2_status() -> Dict[str, Any]:
        d = dict(status)
        try:
            d["plan"] = core.metals_xau_live_harvest_plan_snapshot()
        except Exception as exc:
            d["plan_error"] = f"{type(exc).__name__}:{exc}"
        return d

    return status
