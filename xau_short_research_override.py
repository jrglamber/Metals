"""Research-only XAU SHORT challenger declaration after MFE25 live promotion."""
from __future__ import annotations

from typing import Any, Dict

XAU_SHORT_LIVE_CONTROL = "MFE_GIVEBACK_25"
XAU_SHORT_CHALLENGERS = [
    "ATR2_CHANDELIER",
    "MFE_GIVEBACK_50",
    "MFE_GIVEBACK_75",
    "FIXED_120H",
]
VERSION = "metals_xau_short_full_challenger_suite_v1_2026_10_06"


def install(core: Any) -> Dict[str, Any]:
    original_status = getattr(core, "metals_exit_policy_status", None)

    def status() -> Dict[str, Any]:
        d = dict(original_status() or {}) if callable(original_status) else {}
        active = d.setdefault("active_execution", {})
        active["XAUUSD_SHORT_new_trades"] = XAU_SHORT_LIVE_CONTROL
        shadows = d.setdefault("forward_shadow", {})
        shadows["XAUUSD_SHORT"] = list(XAU_SHORT_CHALLENGERS)
        d["xau_short_research_control"] = XAU_SHORT_LIVE_CONTROL
        d["xau_short_research_challengers"] = list(XAU_SHORT_CHALLENGERS)
        d["xau_short_research_execution_authority"] = False
        d["xau_short_research_suite_version"] = VERSION
        return d

    if callable(original_status):
        core.metals_exit_policy_status = status

    # Explicit metadata for exporters/review tooling. These values carry no
    # broker authority; the live MFE25 manager remains the execution control.
    core.METALS_XAU_SHORT_RESEARCH_CONTROL = XAU_SHORT_LIVE_CONTROL
    core.METALS_XAU_SHORT_RESEARCH_CHALLENGERS = tuple(XAU_SHORT_CHALLENGERS)
    core.METALS_XAU_SHORT_RESEARCH_EXECUTION_AUTHORITY = False
    core.METALS_XAU_SHORT_RESEARCH_SUITE_VERSION = VERSION

    return {
        "installed": True,
        "live_control": XAU_SHORT_LIVE_CONTROL,
        "challengers": list(XAU_SHORT_CHALLENGERS),
        "execution_authority": False,
        "version": VERSION,
    }
