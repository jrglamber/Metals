"""Production hotfixes for explicit Metals short routing — 2026-10-14.

Two legacy assumptions blocked intended live XAU shorts:
1. Candidate classification treated the generic forward-test flag as LONG-only
   and required the obsolete generated_short_v1 model even when TradingView
   explicitly supplied a SHORT trade candidate.
2. Adaptive stacking funding read `owned_open_trades`, which is a legacy
   long-only XAU broker-snapshot field, and then called a legacy long-only trade
   metrics helper with the wrong signature. A live SHORT campaign therefore
   appeared to have no reconstructable open R and failed closed.

This module is deliberately narrow. It fixes side classification and the
broker-authoritative open-R reader only. It does not alter the +2R stacking
threshold, entry allowances, sizing policy, MFE25/ATR2 exits, harvest ladder,
or any other strategy rule.
"""
from __future__ import annotations

from typing import Any, Dict, List

VERSION = "metals_short_candidate_side_fix_v1_2026_10_14"
ADAPTIVE_FUNDING_FIX_VERSION = "metals_xau_adaptive_funding_side_neutral_v1_2026_10_14"
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


def _safe_float(value: Any):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _safe_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        return dict(value or {})
    except Exception:
        return {}


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


def _side_neutral_xau_trades(broker: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return every live XAU trade irrespective of sign/direction.

    `metals_xau_live_broker_snapshot()` historically calls positive-unit trades
    `owned_open_trades` and negative-unit trades `foreign_xau_short_trades`.
    Since XAU SHORT is now intentionally live, adaptive campaign accounting must
    use the side-neutral `all_xau_trades` collection instead.
    """
    all_xau = list((broker or {}).get("all_xau_trades") or [])
    if all_xau:
        return [_safe_dict(t) for t in all_xau]

    # Compatibility fallback for an older snapshot shape. De-duplicate by ID.
    merged: Dict[str, Dict[str, Any]] = {}
    for key in ("owned_open_trades", "foreign_xau_short_trades"):
        for trade in list((broker or {}).get(key) or []):
            d = _safe_dict(trade)
            tid = str(d.get("id") or "")
            if tid:
                merged[tid] = d
    return list(merged.values())


def install(core: Any, app: Any) -> Dict[str, Any]:
    import metals_adaptive_stacking_override as adaptive_stacking

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

    # Replace only the open-campaign R reader used by adaptive stacking. The
    # surrounding +2R gate remains untouched and continues to fail closed if any
    # broker trade, side, P/L, or recorded risk cannot be reconciled exactly.
    def fixed_open_basket_r(core_obj: Any, side: str, open_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        side = str(side or "").strip().lower()
        same = [r for r in open_rows if str(r.get("side") or "").strip().lower() == side]
        if not same:
            return {
                "r": 0.0,
                "count": 0,
                "exact": True,
                "funding_fix_version": ADAPTIVE_FUNDING_FIX_VERSION,
            }

        broker = core_obj.metals_xau_live_broker_snapshot()
        if not broker.get("ok"):
            return {
                "r": None,
                "count": len(same),
                "exact": False,
                "reason": "live_broker_snapshot_failed",
                "funding_fix_version": ADAPTIVE_FUNDING_FIX_VERSION,
            }

        trades = _side_neutral_xau_trades(broker)
        by_id = {str(t.get("id") or ""): t for t in trades if str(t.get("id") or "")}
        total_r = 0.0
        missing: List[str] = []
        side_mismatch: List[str] = []
        missing_metrics: List[str] = []

        for link in same:
            bid = str(link.get("broker_trade_id") or "")
            bt = by_id.get(bid)
            if not bt:
                missing.append(bid or f"link:{link.get('id')}")
                continue

            units = _safe_float(bt.get("currentUnits"))
            if units is None or units == 0.0:
                side_mismatch.append(bid or f"link:{link.get('id')}")
                continue
            if (side == "short" and units >= 0.0) or (side == "long" and units <= 0.0):
                side_mismatch.append(bid or f"link:{link.get('id')}")
                continue

            unrealized_pl = _safe_float(bt.get("unrealizedPL"))
            risk = adaptive_stacking._risk_amount(link)
            if unrealized_pl is None or risk is None or risk <= 0.0:
                missing_metrics.append(bid or f"link:{link.get('id')}")
                continue
            total_r += float(unrealized_pl) / float(risk)

        if missing or side_mismatch or missing_metrics:
            return {
                "r": None,
                "count": len(same),
                "exact": False,
                "missing": missing,
                "side_mismatch": side_mismatch,
                "missing_metrics": missing_metrics,
                "broker_xau_trade_count": len(trades),
                "reason": "open_basket_r_incomplete",
                "funding_fix_version": ADAPTIVE_FUNDING_FIX_VERSION,
            }

        return {
            "r": total_r,
            "count": len(same),
            "exact": True,
            "broker_xau_trade_count": len(trades),
            "calculation": "broker_unrealized_pl_div_recorded_trade_risk",
            "funding_fix_version": ADAPTIVE_FUNDING_FIX_VERSION,
        }

    adaptive_stacking._open_basket_r = fixed_open_basket_r
    core.METALS_XAU_ADAPTIVE_FUNDING_FIX_VERSION = ADAPTIVE_FUNDING_FIX_VERSION

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
        "adaptive_funding_reader_installed": adaptive_stacking._open_basket_r is fixed_open_basket_r,
    }
    if not all(checks.values()):
        raise RuntimeError(f"Metals short-routing startup self-test failed: {checks}")

    def adaptive_funding_runtime_diagnostic() -> Dict[str, Any]:
        """Read-only proof that the current side's +2R funding can be reconstructed."""
        try:
            open_rows = adaptive_stacking._open_links(core)
            sides = sorted({str(r.get("side") or "").strip().lower() for r in open_rows if str(r.get("side") or "").strip()})
            return {
                "ok": True,
                "open_links": len(open_rows),
                "by_side": {
                    side: adaptive_stacking._campaign_funding(core, side, open_rows)
                    for side in sides
                },
                "funding_fix_version": ADAPTIVE_FUNDING_FIX_VERSION,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "funding_fix_version": ADAPTIVE_FUNDING_FIX_VERSION,
            }

    def latest_xau_runtime_diagnostic() -> Dict[str, Any]:
        """Read-only proof of how the latest persisted XAU row evaluates now."""
        try:
            core.init_db()
            with core.get_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM raw_signals WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(pair,''),'_',''),'-',''),' ','')) IN ('XAUUSD','XAU','GOLD') ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if not row:
                    return {"ok": True, "found": False}
                raw_signal_id = int(row["id"])
                raw = core._raw_signal_json(row)
                candidate = dict(core.metals_demo_candidate_for_row(raw, row) or {})
                audit_row = conn.execute(
                    "SELECT * FROM metals_demo_execution_audit WHERE raw_signal_id=? ORDER BY id DESC LIMIT 1",
                    (raw_signal_id,),
                ).fetchone()
                link_row = conn.execute(
                    "SELECT * FROM metals_demo_trade_links WHERE raw_signal_id=? ORDER BY id DESC LIMIT 1",
                    (raw_signal_id,),
                ).fetchone()
            audit = dict(audit_row) if audit_row else None
            link = dict(link_row) if link_row else None
            return {
                "ok": True,
                "found": True,
                "raw_signal_id": raw_signal_id,
                "pair": row["pair"],
                "received_at_utc": row["received_at_utc"] if "received_at_utc" in row.keys() else None,
                "payload": {
                    "signal_side": raw.get("signal_side"),
                    "root_candidate_side": raw.get("root_candidate_side"),
                    "metal_short_candidate": raw.get("metal_short_candidate"),
                    "metal_long_candidate": raw.get("metal_long_candidate"),
                    "signal_quality": raw.get("signal_quality"),
                    "forward_test_candidate": raw.get("forward_test_candidate"),
                    "forward_rule_name": raw.get("forward_rule_name"),
                },
                "candidate": {
                    "demo_candidate": candidate.get("demo_candidate"),
                    "demo_side": candidate.get("demo_side"),
                    "demo_state": candidate.get("demo_state"),
                    "demo_reason": candidate.get("demo_reason"),
                    "demo_blockers": candidate.get("demo_blockers"),
                    "explicit_payload_short": candidate.get("explicit_payload_short"),
                    "legacy_side_blockers_removed": candidate.get("legacy_side_blockers_removed"),
                    "model_version": candidate.get("model_version"),
                },
                "latest_audit": None if not audit else {
                    "id": audit.get("id"),
                    "created_at_utc": audit.get("created_at_utc"),
                    "action": audit.get("action"),
                    "status": audit.get("status"),
                    "candidate_state": audit.get("candidate_state"),
                    "message": audit.get("message"),
                    "broker_trade_id": audit.get("broker_trade_id"),
                    "response_status_code": audit.get("response_status_code"),
                },
                "trade_link": None if not link else {
                    "id": link.get("id"),
                    "status": link.get("status"),
                    "side": link.get("side"),
                    "broker_trade_id": link.get("broker_trade_id"),
                },
            }
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def status() -> Dict[str, Any]:
        return {
            "ok": True,
            "installed": True,
            "version": VERSION,
            "scope": "candidate_side_classification_and_adaptive_funding_reader_only",
            "adaptive_funding_fix_version": ADAPTIVE_FUNDING_FIX_VERSION,
            "adaptive_funding_rule_unchanged": "+2R after normal allowance",
            "removed_legacy_short_blockers_for_explicit_trade_short": sorted(_REMOVABLE_SHORT_BLOCKERS),
            "preserved_filters": "all_other_cleaned_v2_RSI_context_bounce_filters",
            "watch_candidate_execution": False,
            "startup_self_test": checks,
            "adaptive_funding_runtime_diagnostic": adaptive_funding_runtime_diagnostic(),
            "latest_xau_runtime_diagnostic": latest_xau_runtime_diagnostic(),
        }

    try:
        app.add_api_route("/api/metals-short-candidate-side-fix", status, methods=["GET"])
    except Exception:
        pass
    core.metals_short_candidate_side_fix_status = status
    return status()
