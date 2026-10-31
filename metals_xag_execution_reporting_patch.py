"""Final XAG SHORT execution-status reporting overlay.

Presentation/export only. Reads the installed XAG live executor's authoritative
runtime state so dashboard/export labels cannot drift from the broker lane.
"""
from __future__ import annotations

from typing import Any, Dict

VERSION = "metals_xag_execution_reporting_v1_2026_10_31"


def install(contract: Any, core: Any) -> Dict[str, Any]:
    original = contract.execution_status

    def execution_status(source_core: Any) -> Dict[str, Any]:
        status = dict(original(source_core) or {})
        lanes = dict(status.get("lanes") or {})
        lane = dict(lanes.get("XAGUSD_SHORT") or {})

        runtime_fn = getattr(source_core, "xag_short_live_runtime_status", None)
        runtime: Dict[str, Any] = {}
        if callable(runtime_fn):
            try:
                runtime = dict(runtime_fn() or {})
            except Exception as exc:
                runtime = {"status_error": f"{type(exc).__name__}: {exc}"}

        promoted = bool(runtime.get("promotion_enabled"))
        ready = bool(runtime.get("executor_ready"))
        armed = bool(runtime.get("execution_armed"))
        orders_allowed = bool(runtime.get("orders_allowed"))
        manager_ready = bool(runtime.get("management_writes_allowed"))

        if orders_allowed:
            state = "LIVE"
            capital = "LIVE"
            execution_lane = "LIVE_XAG_SHORT"
        elif promoted and ready:
            state = "LIVE_READY_NOT_ARMED"
            capital = "PRACTICE_UNTIL_EXECUTION_ARM"
            execution_lane = "LIVE_XAG_SHORT_READY"
        else:
            state = "LIVE_APPROVED_NOT_READY"
            capital = "PRACTICE"
            execution_lane = "XAG_SHORT_PROMOTION_STAGING"

        lane.update({
            "label": "XAG SHORT",
            "state": state,
            "capital": capital,
            "promotion_decision": "APPROVED_FOR_LIVE_TRIAL_2026_10_31",
            "promotion_enabled": promoted,
            "executor_ready": ready,
            "execution_armed": armed,
            "broker_orders_allowed": orders_allowed,
            "management_writes_allowed": manager_ready,
            "execution_lane": execution_lane,
            "signed_units_required": "NEGATIVE",
            "pilot_units": float(runtime.get("pilot_units") or 1.0),
            "target_risk_gbp": float(runtime.get("target_risk_gbp") or 2.0),
            "emergency_sl_pct": float(runtime.get("sl_pct") or 5.24),
            "emergency_stop_side_required": "ABOVE_ENTRY",
            "initial_policy": "48H_MINIMUM_PLUS_MFE_GIVEBACK_25",
            "active_exit_policy": str(runtime.get("active_exit_policy") or "MFE_GIVEBACK_25"),
            "active_exit_policy_version": str(runtime.get("active_exit_policy_version") or "metals_xag_short_mfe25_48h_v1_2026_10_13"),
            "mfe25_status": "ACTIVE_FOR_NEW_XAG_SHORT_SINCE_2026_10_13",
            "max_open_trades": int(runtime.get("max_open_trades") or 24),
            "retroactive_mirroring": False,
            "open_live_trade_count": int(runtime.get("open_live_xag_short_count") or 0),
            "executor_version": str(runtime.get("version") or ""),
            "status_error": runtime.get("status_error"),
        })
        lanes["XAGUSD_SHORT"] = lane
        status["lanes"] = lanes
        status["xag_short_live_config"] = runtime
        status["xag_reporting_overlay_version"] = VERSION
        return status

    contract.execution_status = execution_status

    # Repair wording embedded in older manifests/text exports as they are generated.
    stale = tuple(getattr(contract, "_STALE_REPLACEMENTS", ()) or ())
    extra = (
        (
            "XAG SHORT is approved for live trial but requires separate execution arming",
            "XAG SHORT live pilot status is reported from the authoritative executor state",
        ),
        (
            "RESEARCH_CONTINUES_NOT_REQUIRED_FOR_PROMOTION",
            "ACTIVE_FOR_NEW_XAG_SHORT_SINCE_2026_10_13",
        ),
        (
            "48H_MINIMUM_PLUS_VALIDATED_PROTECTION",
            "48H_MINIMUM_PLUS_MFE_GIVEBACK_25",
        ),
    )
    contract._STALE_REPLACEMENTS = stale + tuple(x for x in extra if x not in stale)

    return {
        "installed": True,
        "version": VERSION,
        "presentation_only": True,
        "execution_authority": False,
        "authoritative_source": "xag_short_live_runtime_status",
    }
