"""Make the production XAU basket manager/profit protection explicitly own BOTH XAU sides.

This is a narrow fail-closed adapter around the existing live-XAU manager and
harvest engine. Demo/practice basket management remains untouched and separate.
"""
from __future__ import annotations

import inspect
import textwrap
from typing import Any, Dict

VERSION = "metals_xau_live_basket_both_sides_v2_2026_10_07"


def _replace_once(src: str, old: str, new: str, label: str) -> str:
    count = src.count(old)
    if count != 1:
        raise RuntimeError(f"Live XAU basket patch refused: expected one {label}, found {count}")
    return src.replace(old, new, 1)


def install(core: Any) -> Dict[str, Any]:
    ns = core.__dict__

    # The original live manager was gated by the XAU-long pilot flag. Make the
    # live manager active whenever either promoted XAU side is live.
    original_tick = core.metals_xau_live_manager_tick
    src = textwrap.dedent(inspect.getsource(original_tick))
    src = _replace_once(
        src,
        "def metals_xau_live_manager_tick(",
        "def _metals_xau_live_manager_tick_both_sides(",
        "manager header",
    )
    src = _replace_once(
        src,
        "    if not METALS_XAU_LONG_LIVE_PROMOTION_ENABLED:\n",
        "    if not (METALS_XAU_LONG_LIVE_PROMOTION_ENABLED or METALS_XAU_SHORT_LIVE_PROMOTION_ENABLED):\n",
        "long-only promotion guard",
    )

    # PostgreSQL cannot infer the type of a bind parameter used only in
    # `WHEN ? IS NOT NULL`.  The manager's fixed-48h timestamp guard therefore
    # failed before it could reach stop-candidate / broker-stop reconciliation.
    # CAST is valid in both PostgreSQL and SQLite and changes no decision logic.
    src = _replace_once(
        src,
        "WHEN ? IS NOT NULL",
        "WHEN CAST(? AS TEXT) IS NOT NULL",
        "Postgres fixed-48h nullable timestamp guard",
    )

    exec(compile(src, "<xau-live-basket-both-sides>", "exec"), ns, ns)
    patched_tick = ns.get("_metals_xau_live_manager_tick_both_sides")
    if not callable(patched_tick):
        raise RuntimeError("Live XAU basket patch refused: manager tick not callable")
    core.metals_xau_live_manager_tick = patched_tick

    # New harvest cycles should use a side-neutral LIVE XAU identity. Existing
    # cycles are deliberately left intact so we never rewrite historical state.
    original_cycle = core._metals_xau_live_harvest_cycle_id
    cyc = textwrap.dedent(inspect.getsource(original_cycle))
    cyc = _replace_once(
        cyc,
        "def _metals_xau_live_harvest_cycle_id(",
        "def _metals_xau_live_harvest_cycle_id_both_sides(",
        "harvest cycle header",
    )
    cyc = _replace_once(
        cyc,
        'current="XAU_LONG_LIVE_"+now_utc().strftime("%Y%m%dT%H%M%S%fZ")',
        'current="XAU_LIVE_"+now_utc().strftime("%Y%m%dT%H%M%S%fZ")',
        "long-only harvest cycle name",
    )
    exec(compile(cyc, "<xau-live-harvest-both-sides>", "exec"), ns, ns)
    patched_cycle = ns.get("_metals_xau_live_harvest_cycle_id_both_sides")
    if not callable(patched_cycle):
        raise RuntimeError("Live XAU basket patch refused: harvest cycle function not callable")
    core._metals_xau_live_harvest_cycle_id = patched_cycle

    # Surface an explicit status contract for dashboard/exports/diagnostics.
    previous_cfg = core.metals_xau_live_config_status
    def cfg_status() -> Dict[str, Any]:
        d = dict(previous_cfg() or {})
        d["live_basket_scope"] = "XAU_LONG+XAU_SHORT"
        d["live_basket_manager_enabled"] = bool(core.METALS_XAU_LIVE_MANAGER_ENABLED)
        d["live_harvest_execution_enabled"] = bool(core.METALS_XAU_LIVE_HARVEST_EXECUTION_ENABLED)
        d["live_basket_uses_live_oanda_account"] = core.METALS_XAU_LIVE_OANDA_ENV == "live"
        d["demo_basket_manager_separate"] = True
        d["live_basket_manager_version"] = VERSION
        d["postgres_fixed_48h_guard_typed"] = True
        return d
    core.metals_xau_live_config_status = cfg_status
    core.METALS_XAU_LIVE_BASKET_MANAGER_VERSION = VERSION

    return {
        "installed": True,
        "version": VERSION,
        "scope": "XAU_LONG+XAU_SHORT",
        "manager_enabled": bool(core.METALS_XAU_LIVE_MANAGER_ENABLED),
        "harvest_execution_enabled": bool(core.METALS_XAU_LIVE_HARVEST_EXECUTION_ENABLED),
        "demo_isolated": True,
        "postgres_fixed_48h_guard_typed": True,
        "fail_closed_source_assertions": True,
    }