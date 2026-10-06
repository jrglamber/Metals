"""Side-safe promotion of XAU SHORT into the existing live-XAU broker lane.

Fail-closed runtime patch: the existing XAU LONG execution function is sourced
at startup and only a small, asserted set of long-only assumptions is made
side-aware. If upstream structure changes, install raises before serving.
"""
from __future__ import annotations

import inspect
import os
import textwrap
from typing import Any, Dict

SHORT_GATE_ENV = "METALS_XAU_SHORT_LIVE_PROMOTION_ENABLED"
SHORT_VERSION = "metals_xau_short_live_mfe25_v1_2026_10_06"


def _enabled() -> bool:
    return str(os.getenv(SHORT_GATE_ENV, "false")).strip().lower() in {"1", "true", "yes", "on"}


def _side_sizing_preview(core: Any, original_preview, side: str) -> Dict[str, Any]:
    p = dict(original_preview() or {})
    if str(side).lower() != "short":
        return p

    p["side"] = "short"
    bid = core.safe_float(p.get("bid"))
    ask = core.safe_float(p.get("ask"))
    sl_pct = core.safe_float(p.get("sl_pct")) or core.safe_float(getattr(core, "METALS_XAU_LIVE_SL_PCT", None))
    units = abs(float(core.safe_float(p.get("units")) or 0.0))
    if bid is None or ask is None or bid <= 0 or ask <= 0 or sl_pct is None or sl_pct <= 0 or units <= 0:
        reasons = list(p.get("blocking_reasons") or [])
        reasons.append("short_side_sizing_inputs_invalid")
        p["blocking_reasons"] = reasons
        p["executable"] = False
        return p

    # A market short enters on/near bid and its emergency SL must sit ABOVE entry.
    entry = float(bid)
    stop = entry * (1.0 + float(sl_pct) / 100.0)

    # Reuse the long preview's already broker-validated unit quantity. Because
    # ask >= bid and SL is percentage based, this is marginally conservative
    # for a short (never increases the long preview's requested units).
    long_est = core.safe_float(p.get("estimated_risk_gbp"))
    estimated = (float(long_est) * entry / float(ask)) if long_est is not None and ask > 0 else None
    requested = core.safe_float(p.get("risk_amount")) or core.safe_float(p.get("requested_risk_gbp"))
    overage = None
    if estimated is not None and requested is not None and requested > 0:
        overage = (estimated - requested) / requested * 100.0

    p.update({
        "entry_price": entry,
        "stop_price": stop,
        "units": units,
        "estimated_risk_gbp": estimated,
        "estimated_risk_overage_pct": overage,
        "short_side_safe": bool(stop > entry and units > 0),
        "short_units_will_be_signed_negative_at_order": True,
    })
    if not p["short_side_safe"]:
        reasons = list(p.get("blocking_reasons") or [])
        reasons.append("short_side_stop_or_units_invalid")
        p["blocking_reasons"] = reasons
        p["executable"] = False
    return p


def _replace_once(src: str, old: str, new: str, label: str) -> str:
    count = src.count(old)
    if count != 1:
        raise RuntimeError(f"XAU short live patch refused: expected one {label}, found {count}")
    return src.replace(old, new, 1)


def install(core: Any) -> Dict[str, Any]:
    original = core.execute_metals_xau_live_candidate
    original_preview = core.metals_xau_live_sizing_preview
    original_cfg = core.metals_xau_live_config_status

    src = textwrap.dedent(inspect.getsource(original))
    src = _replace_once(
        src,
        "def execute_metals_xau_live_candidate(",
        "def _execute_metals_xau_live_candidate_side_aware(",
        "candidate function header",
    )

    old_guard = '''    if side != "long":\n        return {\n            "ok": True,\n            "skipped": True,\n            "execution_lane": "LIVE_XAU_LONG",\n            "asset": "XAUUSD",\n            "side": side,\n            "reason": "xau_short_remains_practice",\n        }\n'''
    new_guard = '''    if side not in {"long", "short"}:\n        return {\n            "ok": True,\n            "skipped": True,\n            "execution_lane": "LIVE_XAU_UNSUPPORTED",\n            "asset": "XAUUSD",\n            "side": side,\n            "reason": "unsupported_xau_live_side",\n        }\n    if side == "short" and not _metals_xau_short_live_gate_enabled():\n        return {\n            "ok": True,\n            "skipped": True,\n            "execution_lane": "LIVE_XAU_SHORT",\n            "asset": "XAUUSD",\n            "side": "short",\n            "reason": "xau_short_live_promotion_not_enabled",\n        }\n    active_exit_policy = (\n        METALS_XAU_LONG_MFE50_POLICY if side == "long"\n        else METALS_XAU_SHORT_ACTIVE_POLICY\n    )\n    active_exit_policy_version = (\n        METALS_XAU_LONG_MFE50_POLICY_VERSION if side == "long"\n        else METALS_XAU_SHORT_ACTIVE_POLICY_VERSION\n    )\n'''
    src = _replace_once(src, old_guard, new_guard, "long-only side guard")

    old_chop = '''    chop_brake = metals_xau_live_persistent_chop_gate(\n        int(raw_signal_id),\n        revalidate_latest=(safe_str(source) == "market_reopen_retry"),\n    )\n'''
    new_chop = '''    chop_brake = (\n        metals_xau_live_persistent_chop_gate(\n            int(raw_signal_id),\n            revalidate_latest=(safe_str(source) == "market_reopen_retry"),\n        )\n        if side == "long"\n        else {\n            "enabled": False, "allow": True, "blocked": False,\n            "decision": "ALLOW", "reason": "long_only_chop_brake_not_applied_to_xau_short",\n            "asset": "XAUUSD", "side": "short",\n        }\n    )\n'''
    src = _replace_once(src, old_chop, new_chop, "persistent-chop call")
    src = _replace_once(
        src,
        "    preview = metals_xau_live_sizing_preview()\n",
        "    preview = _metals_xau_side_sizing_preview(side)\n",
        "sizing preview call",
    )
    src = _replace_once(
        src,
        '    units = abs(float(preview["units"]))\n',
        '    units = abs(float(preview["units"])) * (-1.0 if side == "short" else 1.0)\n    if side == "short" and (units >= 0 or float(preview["stop_price"]) <= float(preview["entry_price"])):\n        return {"ok": False, "blocked": True, "execution_lane": "LIVE_XAU_SHORT", "asset": "XAUUSD", "side": "short", "reason": "short_order_safety_invariant_failed", "preview": preview}\n',
        "signed order units",
    )

    # Persist/report the true direction everywhere the live function formerly
    # hard-coded LONG. These replacements are intentionally broad inside this
    # one function source; LONG strategy constants are handled separately.
    src = src.replace('"execution_lane": "LIVE_XAU_LONG"', '"execution_lane": f"LIVE_XAU_{side.upper()}"')
    src = src.replace('"side": "long"', '"side": side')
    src = src.replace('METALS_XAU_LIVE_ALLOWED_INSTRUMENT, "long",', 'METALS_XAU_LIVE_ALLOWED_INSTRUMENT, side,')
    src = src.replace('METALS_XAU_LONG_MFE50_POLICY_VERSION', 'active_exit_policy_version')
    src = src.replace('METALS_XAU_LONG_MFE50_POLICY', 'active_exit_policy')
    src = src.replace('f"LIVE XAU LONG opened from {source}; "', 'f"LIVE XAU {side.upper()} opened from {source}; "')

    # The source-level substitution above also touches the newly inserted local
    # assignment names if done blindly. Repair those two assignment expressions
    # to the real globals explicitly and assert they exist afterwards.
    src = src.replace(
        'active_exit_policy = (\n        active_exit_policy if side == "long"\n        else METALS_XAU_SHORT_ACTIVE_POLICY\n    )',
        'active_exit_policy = (\n        METALS_XAU_LONG_MFE50_POLICY if side == "long"\n        else METALS_XAU_SHORT_ACTIVE_POLICY\n    )',
    )
    src = src.replace(
        'active_exit_policy_version = (\n        active_exit_policy_version if side == "long"\n        else METALS_XAU_SHORT_ACTIVE_POLICY_VERSION\n    )',
        'active_exit_policy_version = (\n        METALS_XAU_LONG_MFE50_POLICY_VERSION if side == "long"\n        else METALS_XAU_SHORT_ACTIVE_POLICY_VERSION\n    )',
    )

    required = [
        '(-1.0 if side == "short" else 1.0)',
        'short_order_safety_invariant_failed',
        'METALS_XAU_SHORT_ACTIVE_POLICY',
        'METALS_XAU_SHORT_ACTIVE_POLICY_VERSION',
        '_metals_xau_side_sizing_preview(side)',
    ]
    missing = [x for x in required if x not in src]
    if missing:
        raise RuntimeError(f"XAU short live patch refused: transformed invariants missing {missing}")

    core._metals_xau_short_live_gate_enabled = _enabled
    core._metals_xau_side_sizing_preview = lambda side: _side_sizing_preview(core, original_preview, side)
    ns = core.__dict__
    exec(compile(src, "<xau-short-live-side-aware>", "exec"), ns, ns)
    patched = ns.get("_execute_metals_xau_live_candidate_side_aware")
    if not callable(patched):
        raise RuntimeError("XAU short live patch refused: transformed candidate not callable")
    core.execute_metals_xau_live_candidate = patched

    def cfg_status() -> Dict[str, Any]:
        d = dict(original_cfg() or {})
        armed = bool(d.get("orders_allowed")) and _enabled()
        d.update({
            "xau_short_policy": getattr(core, "METALS_XAU_SHORT_ACTIVE_POLICY", "MFE_GIVEBACK_25"),
            "xau_short_policy_version": getattr(core, "METALS_XAU_SHORT_ACTIVE_POLICY_VERSION", SHORT_VERSION),
            "xau_short_broker_orders_allowed": armed,
            "xau_short_live_promotion_enabled": _enabled(),
            "xau_short_execution_lane": "LIVE_XAU_SHORT",
            "xau_short_signed_units": "NEGATIVE",
            "xau_short_emergency_stop_side": "ABOVE_ENTRY",
            "xau_short_sizing": "REUSE_LONG_VALIDATED_UNITS_CONSERVATIVELY_WITH_SHORT_SIDE_PRICE_AND_STOP",
        })
        if armed:
            d.pop("xau_short_broker_block_reason", None)
        return d

    core.metals_xau_live_config_status = cfg_status
    core.METALS_XAU_SHORT_LIVE_VERSION = SHORT_VERSION
    return {
        "installed": True,
        "version": SHORT_VERSION,
        "gate_enabled": _enabled(),
        "fail_closed_source_assertions": True,
        "short_negative_units": True,
        "short_stop_above_entry": True,
        "long_path_side_aware": True,
    }
