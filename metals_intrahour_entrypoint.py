"""Production wrapper enabling intrahour ATR2/MFE25 XAU protection."""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List

# The dashboard-v2 presentation layer still contains the one-shot reset call that
# was used during the live/demo separation migration. That migration is complete;
# repeated process starts must never rebase the live XAU HWM. Suppress only that
# import-time call, then restore the real reset function for explicit/manual use.
import metals_live_basket_entrypoint as basket_base

_real_reset = basket_base._reset_live_xau_protection_cycle


def _retired_startup_reset(reset_id: str):
    return {
        "ok": True,
        "skipped": True,
        "reason": "one_shot_dashboard_reset_retired",
        "reset_id": reset_id,
    }


basket_base._reset_live_xau_protection_cycle = _retired_startup_reset
try:
    import metals_live_dashboard_v2 as base
finally:
    basket_base._reset_live_xau_protection_cycle = _real_reset

# Presentation-only stability layer. It prevents the legacy layout retry loops
# from repeatedly refetching live state and keeps LIVE XAU basket protection as
# a top-level accordion after Broker / OANDA / Accounting. No broker or strategy
# function is modified.
import metals_dashboard_stability_patch as dashboard_stability
DASHBOARD_STABILITY_STATUS = dashboard_stability.install(base.analysis)


# Correct the read-only ladder route installed by the presentation layer so it
# uses the EXISTING authoritative Metals harvest policy rather than guessed
# configuration names. This changes dashboard serialization only; the live
# harvest engine, thresholds, fractions, execution flags and OANDA paths are
# untouched.
def _install_authoritative_xau_ladder() -> Dict[str, Any]:
    core = base.core
    route_path = "/api/live-xau-harvest-ladder-view"

    # The stability layer registered the first version of this read-only route.
    # Replace it before application startup so the existing front-end fetch keeps
    # the same URL but receives the real live-XAU policy/stage state.
    router = core.app.router
    old_routes = [r for r in list(router.routes) if getattr(r, "path", None) == route_path]
    for route in old_routes:
        router.routes.remove(route)

    def _sf(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    def _ids(value: Any) -> List[str]:
        if value in (None, "", []):
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(x) for x in value if str(x).strip()]
        text = str(value).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if str(x).strip()]
        except Exception:
            pass
        return [x.strip().strip("'\"") for x in text.strip("[]").replace(";", ",").split(",") if x.strip()]

    @core.app.get(route_path)
    def live_xau_harvest_ladder_view() -> Dict[str, Any]:
        try:
            first = _sf(getattr(core, "METALS_HARVEST_FIRST_LEVEL_R", 50.0), 50.0)
            step = _sf(getattr(core, "METALS_HARVEST_STEP_R", 50.0), 50.0)
            max_level = _sf(getattr(core, "METALS_HARVEST_MAX_LEVEL_R", 5000.0), 5000.0)
            policy_version = str(getattr(core, "METALS_XAU_LIVE_HARVEST_POLICY_VERSION", ""))

            with core.get_conn() as conn:
                active_cycle = str(core._metals_xau_live_runtime_get(conn, "active_harvest_cycle_id", "") or "")
                hwm_r = _sf(core._metals_xau_live_runtime_get(conn, "broker_hwm_r", "0"), 0.0)
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM metals_xau_live_harvest_stages ORDER BY id DESC LIMIT 300"
                ).fetchall()]

            # Never mix a previous basket cycle into the current live ladder.
            current = [r for r in rows if active_cycle and str(r.get("cycle_id") or "") == active_cycle]
            by_level: Dict[float, Dict[str, Any]] = {}
            for row in current:
                lvl = _sf(row.get("threshold_r"), -1.0)
                if lvl >= 0 and lvl not in by_level:
                    by_level[lvl] = row

            # The authoritative policy is 50R spacing. Keep the first six stages
            # visible at all times (50..300R, matching the BCO-style dashboard),
            # then include any higher stages that have actually been persisted.
            visible_levels: List[float] = []
            level = first
            for _ in range(6):
                if level <= max_level + 1e-9:
                    visible_levels.append(float(level))
                level += step
            visible_levels = sorted(set(visible_levels + list(by_level.keys())))

            ladder: List[Dict[str, Any]] = []
            for serial, lvl in enumerate(visible_levels, start=1):
                stage = by_level.get(lvl, {})
                fraction_fn = getattr(core, "_metals_harvest_fraction_for_level", None)
                fraction = _sf(fraction_fn(lvl) if callable(fraction_fn) else stage.get("bank_fraction"), 0.0)
                if stage.get("bank_fraction") not in (None, ""):
                    fraction = _sf(stage.get("bank_fraction"), fraction)

                status = str(stage.get("status") or "NOT_ARMED")
                target = stage.get("target_bank_gbp") if stage else None
                executed = stage.get("executed_bank_gbp") if stage else None
                pool = _sf(stage.get("trigger_profitable_pool_gbp"), 0.0) if stage else 0.0
                executed_num = _sf(executed, 0.0) if executed not in (None, "") else None

                actual_pct = None
                if executed_num is not None and pool > 0:
                    actual_pct = 100.0 * executed_num / pool
                elif status.upper() == "EXECUTED":
                    actual_pct = 100.0 * fraction

                ladder.append({
                    "serial": serial,
                    "level_r": lvl,
                    "status": status,
                    "bank_pct": 100.0 * fraction,
                    "target_at_trigger_gbp": _sf(target) if target not in (None, "") else None,
                    "actually_banked_pct": actual_pct,
                    "actual_gbp_banked": executed_num,
                    "executed_at": stage.get("executed_at_utc") if stage else None,
                    "trade_ids": _ids(stage.get("selected_broker_trade_ids")) if stage else [],
                })

            return {
                "ok": True,
                "scope": "LIVE_XAU_ONLY",
                "active_cycle_id": active_cycle,
                "high_water_r": hwm_r,
                "policy_version": policy_version,
                "ladder": ladder,
                "configured": {
                    "first_level_r": first,
                    "step_r": step,
                    "max_level_r": max_level,
                    "50r_fraction": _sf(getattr(core, "METALS_HARVEST_50_FRACTION", 0.20), 0.20),
                    "100r_fraction": _sf(getattr(core, "METALS_HARVEST_100_FRACTION", 0.20), 0.20),
                    "150r_plus_fraction": _sf(getattr(core, "METALS_HARVEST_150_PLUS_FRACTION", 0.25), 0.25),
                    "continues_every_r": step,
                },
            }
        except Exception as exc:
            return {
                "ok": False,
                "scope": "LIVE_XAU_ONLY",
                "ladder": [],
                "error": f"{type(exc).__name__}: {exc}",
            }

    status = {
        "installed": True,
        "route": route_path,
        "replaced_old_routes": len(old_routes),
        "authoritative_policy": True,
        "visible_base_levels": 6,
        "execution_logic_changed": False,
    }
    print("METALS_XAU_AUTHORITATIVE_LADDER", status, flush=True)
    return status


XAU_AUTHORITATIVE_LADDER_STATUS = _install_authoritative_xau_ladder()

import metals_intrahour_exit_override as intrahour

INTRAHOUR_EXIT_STATUS = intrahour.install(base.core, base.app)
app = base.app

# Permanent low-volume heartbeat. This is deliberately read-only: it makes the
# protective execution path fail-loud without changing trading decisions.
_health_stop = threading.Event()


def _health_loop() -> None:
    _health_stop.wait(20.0)
    while not _health_stop.is_set():
        s = dict(getattr(base.core, "_METALS_XAU_INTRAHOUR_EXIT_STATUS", {}) or {})
        last = dict(s.get("last_sync") or {})
        print(
            "METALS_XAU_INTRAHOUR_HEALTH "
            f"version={s.get('version')} ticks={s.get('ticks')} "
            f"last_ok={s.get('last_tick_ok')} checked={last.get('checked')} "
            f"mature={last.get('mature')} eligible={last.get('eligible')} "
            f"updated={last.get('updated')} unchanged={last.get('unchanged')} "
            f"catchup_closed={last.get('catchup_closed')} errors={last.get('errors')} "
            f"integrity_errors={s.get('integrity_errors')}",
            flush=True,
        )
        _health_stop.wait(60.0)


@app.on_event("startup")
def _start_intrahour_health() -> None:
    threading.Thread(target=_health_loop, name="metals-xau-intrahour-health", daemon=True).start()


@app.on_event("shutdown")
def _stop_intrahour_health() -> None:
    _health_stop.set()
