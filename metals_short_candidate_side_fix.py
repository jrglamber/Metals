"""Side-safe Metals short-candidate classification hotfix — 2026-10-14.

The multi-context TradingView payload uses `forward_test_candidate` for either
side. Legacy execution code still interprets the database copy of that generic
flag as a LONG-only flag and also requires the older generated_short_v1 model.
That can block an explicit SHORT payload with both:

- generated_short_v1_not_true
- long_forward_candidate_true

This override is intentionally narrow. It promotes the explicit payload short
only when the alert is unambiguously SHORT and marked trade_candidate. All
existing cleaned-v2 bounce/RSI/context blockers remain authoritative. It also
prevents the legacy long cleaner from treating the same explicit SHORT payload
as a long candidate.
"""
from __future__ import annotations

from typing import Any, Dict, List

VERSION = "metals_short_candidate_side_fix_v1_2026_10_14"
_REMOVABLE_SHORT_BLOCKERS = {
    "generated_short_v1_not_true",
    "long_forward_candidate_true",
}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _explicit_trade_short(raw: Dict[str, Any]) -> bool:
    """Require redundant side evidence before bypassing legacy side assumptions."""
    if not _truthy(raw.get("metal_short_candidate")):
        return False
    if _truthy(raw.get("metal_long_candidate")):
        return False

    signal_side = _text(raw.get("signal_side"))
    root_side = _text(raw.get("root_candidate_side"))
    if signal_side != "short" and root_side != "short":
        return False
    if signal_side == "long" or root_side == "long":
        return False

    # Do not turn Pine watch rows into trades. Current production payloads use
    # trade_candidate for executable candidates and watch_candidate for watches.
    quality = _text(raw.get("signal_quality"))
    if quality and quality not in {"trade_candidate", "candidate", "trade"}:
        return False

    # If the payload declares a forward rule, it must not explicitly be long.
    rule = _text(raw.get("forward_rule_name"))
    if rule and "long" in rule and "short" not in rule:
        return False

    return True


def _split_blockers(value: Any) -> List[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def install(core: Any, app: Any) -> Dict[str, Any]:
    original_short = core.cleaned_metal_short_demo_candidate
    original_long = core.cleaned_metal_long_demo_candidate

    def fixed_short(raw: Dict[str, Any], row: Any) -> Dict[str, Any]:
        result = dict(original_short(raw, row) or {})
        if not _explicit_trade_short(raw):
            result.setdefault("side_classification_fix_version", VERSION)
            result.setdefault("explicit_payload_short", 0)
            return result

        blockers = _split_blockers(result.get("demo_blockers"))
        remaining = [b for b in blockers if b not in _REMOVABLE_SHORT_BLOCKERS]

        # The explicit multi-context short payload replaces only the two legacy
        # classification assumptions. Every other cleaned-v2 blocker survives.
        result["short_candidate"] = 1
        result["explicit_payload_short"] = 1
        result["side_classification_fix_version"] = VERSION
        result["legacy_side_blockers_removed"] = [
            b for b in blockers if b in _REMOVABLE_SHORT_BLOCKERS
        ]
        result["demo_blockers"] = "; ".join(remaining)

        if not remaining:
            result["demo_candidate"] = 1
            result["demo_state"] = "CANDIDATE"
            result["demo_reason"] = (
                "explicit_metal_short_candidate_true; "
                "cleaned_v2_bounce_filters_passed"
            )
            result["short_watch"] = 0
        else:
            result["demo_candidate"] = 0
            # Preserve WATCH only where the underlying short engine already had
            # enough evidence for WATCH; otherwise remain fail-closed BLOCKED.
            result["demo_state"] = (
                "WATCH" if int(result.get("short_watch") or 0) == 1 else "BLOCKED"
            )
            result["demo_reason"] = ""
        return result

    def fixed_long(raw: Dict[str, Any], row: Any) -> Dict[str, Any]:
        result = dict(original_long(raw, row) or {})
        if not _explicit_trade_short(raw):
            result.setdefault("side_classification_fix_version", VERSION)
            return result

        blockers = _split_blockers(result.get("demo_blockers"))
        if "explicit_short_payload" not in blockers:
            blockers.append("explicit_short_payload")
        result["demo_candidate"] = 0
        result["demo_state"] = "BLOCKED"
        result["demo_reason"] = ""
        result["demo_blockers"] = "; ".join(blockers)
        result["explicit_payload_short"] = 1
        result["side_classification_fix_version"] = VERSION
        return result

    core.cleaned_metal_short_demo_candidate = fixed_short
    core.cleaned_metal_long_demo_candidate = fixed_long
    core.METALS_SHORT_CANDIDATE_SIDE_FIX_VERSION = VERSION

    # Deterministic startup tests: explicit executable short is promoted through
    # the two legacy side blockers; watch-quality rows are never promoted.
    synthetic_trade = {
        "metal_short_candidate": True,
        "metal_long_candidate": False,
        "signal_side": "short",
        "root_candidate_side": "short",
        "signal_quality": "trade_candidate",
        "forward_rule_name": "metals_short_research",
        "d_bear": True,
        "d_bull": False,
        "exec_rsi": 35.0,
        "d_rsi": 45.0,
        "exec_rsi_up": False,
        "ctx_rsi_up": False,
        "exec_close_gt_ema20": False,
    }
    synthetic_row = {"forward_test_candidate": True, "take_trade": False}
    trade_check = fixed_short(synthetic_trade, synthetic_row)

    synthetic_watch = dict(synthetic_trade)
    synthetic_watch["signal_quality"] = "watch_candidate"
    watch_check = fixed_short(synthetic_watch, synthetic_row)

    long_check = fixed_long(synthetic_trade, synthetic_row)

    checks = {
        "explicit_short_promoted": bool(trade_check.get("demo_candidate")),
        "legacy_generated_short_blocker_removed": "generated_short_v1_not_true" not in _split_blockers(trade_check.get("demo_blockers")),
        "generic_forward_not_treated_as_long_blocker": "long_forward_candidate_true" not in _split_blockers(trade_check.get("demo_blockers")),
        "watch_not_promoted": not bool(watch_check.get("demo_candidate")),
        "explicit_short_cannot_be_long_candidate": not bool(long_check.get("demo_candidate")),
    }
    if not all(checks.values()):
        raise RuntimeError(f"Metals short side-classification startup self-test failed: {checks}")

    def status() -> Dict[str, Any]:
        return {
            "ok": True,
            "installed": True,
            "version": VERSION,
            "scope": "candidate_side_classification_only",
            "removed_legacy_short_blockers_for_explicit_trade_short": sorted(_REMOVABLE_SHORT_BLOCKERS),
            "preserved_filters": "all_other_cleaned_v2_RSI_context_bounce_filters",
            "watch_candidate_execution": False,
            "startup_self_test": checks,
        }

    try:
        app.add_api_route("/api/metals-short-candidate-side-fix", status, methods=["GET"])
    except Exception:
        pass
    core.metals_short_candidate_side_fix_status = status
    return status()
