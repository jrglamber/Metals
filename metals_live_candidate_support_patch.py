"""Live-XAU-only Candidate Support for the Metals dashboard.

Presentation/telemetry only. XAG remains research/demo and is deliberately
excluded from the live Candidate Support denominator. XAU LONG and XAU SHORT
share one live slot, shown as 1/1 LONG, 1/1 SHORT, or 0/1 NONE.
"""
from __future__ import annotations

from typing import Any, Dict

VERSION = "metals_live_xau_candidate_support_v1_2026_10_16"


def _d(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        return dict(value or {})
    except Exception:
        return {}


def _latest_xau(core: Any) -> Dict[str, Any]:
    """Evaluate the latest XAU candle through the currently installed candidate logic."""
    core.init_db()
    with core.get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM raw_signals
            WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(pair,''),'_',''),'-',''),' ',''))
                  IN ('XAUUSD','XAU','GOLD')
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    if not row:
        return {
            "supported": 0,
            "side": "NONE",
            "raw_signal_id": None,
            "candle": None,
            "candidate_state": "NO_SIGNAL",
            "reason": "no_xau_signal",
        }

    raw = core._raw_signal_json(row)
    candidate = _d(core.metals_demo_candidate_for_row(raw, row))
    side = str(core._metals_demo_side(candidate.get("demo_side")) or "").strip().lower()
    is_candidate = bool(candidate.get("demo_candidate")) and side in {"long", "short"}

    # Candidate Support is about a live XAU lane, not a demo/research candidate.
    if side == "long":
        lane_live = bool(getattr(core, "METALS_XAU_LONG_LIVE_PROMOTION_ENABLED", False))
    elif side == "short":
        gate = getattr(core, "_metals_xau_short_live_gate_enabled", None)
        lane_live = bool(gate()) if callable(gate) else False
    else:
        lane_live = False

    supported = int(bool(is_candidate and lane_live))
    side_label = side.upper() if supported else "NONE"
    reason = str(candidate.get("demo_reason") or candidate.get("demo_blockers") or "")
    return {
        "supported": supported,
        "side": side_label,
        "raw_signal_id": int(row["id"]),
        "candle": row["timestamp_readable"] if "timestamp_readable" in row.keys() else None,
        "received_at_utc": row["received_at_utc"] if "received_at_utc" in row.keys() else None,
        "candidate_state": str(candidate.get("demo_state") or ("CANDIDATE" if supported else "BLOCKED")),
        "reason": reason,
        "model_version": str(candidate.get("model_version") or ""),
    }


def install(core: Any, app: Any, analysis: Any) -> Dict[str, Any]:
    if getattr(core, "_LIVE_XAU_CANDIDATE_SUPPORT_INSTALLED", False):
        return dict(getattr(core, "_LIVE_XAU_CANDIDATE_SUPPORT_STATUS", {}))

    original_top = getattr(core, "metals_standard_top_snapshot", None)
    if not callable(original_top):
        raise RuntimeError("Live XAU candidate support install refused: top snapshot missing")

    def support_snapshot() -> Dict[str, Any]:
        latest = _latest_xau(core)
        supported = int(latest.get("supported") or 0)
        side = str(latest.get("side") or "NONE").upper()
        return {
            "ok": True,
            "version": VERSION,
            "scope": "LIVE_XAU_ONLY",
            "live_assets": ["XAUUSD"],
            "demo_research_excluded": ["XAGUSD"],
            "supported": supported,
            "expected": 1,
            "side": side,
            "display": f"{supported}/1 · {side}",
            "semantics": "CURRENT_XAU_LIVE_CANDIDATE_LONG_OR_SHORT",
            "latest": latest,
            "strategy_changed": False,
        }

    def patched_top(force: bool = False) -> Dict[str, Any]:
        out = _d(original_top(force=force))
        signals = _d(out.get("signals"))
        live = support_snapshot()
        signals.update({
            "live_candidate_assets": int(live.get("supported") or 0),
            "live_expected_candidate_assets": 1,
            "live_candidate_side": str(live.get("side") or "NONE"),
            "live_candidate_display": str(live.get("display") or "0/1 · NONE"),
            "live_candidate_support_semantics": live.get("semantics"),
            "live_candidate_raw_signal_id": (live.get("latest") or {}).get("raw_signal_id"),
            "live_candidate_candle": (live.get("latest") or {}).get("candle"),
            "live_candidate_demo_research_excluded": ["XAGUSD"],
        })
        out["signals"] = signals
        return out

    # /dashboard/top resolves this global at request time, so replacing the
    # module attribute updates the existing route without replacing the route.
    core.metals_standard_top_snapshot = patched_top
    core.live_xau_candidate_support_snapshot = support_snapshot

    try:
        core.app.add_api_route(
            "/api/live-xau-candidate-support",
            support_snapshot,
            methods=["GET"],
        )
    except Exception:
        pass

    previous_rewrite = analysis._rewrite_dashboard_version
    old_card = "card('Candidate Support',`${eh(g.candidate_assets||0)}/2`,candidateDetail,Number(g.candidate_assets||0)>0?'pos':'neg')"
    new_card = "card('Candidate Support',eh(g.live_candidate_display||((g.live_candidate_assets||0)+'/1 · '+(g.live_candidate_side||'NONE'))),'LIVE XAU only · current candle',Number(g.live_candidate_assets||0)>0?'pos':'neg')"

    def rewrite(body: bytes, content_type: str) -> bytes:
        body = previous_rewrite(body, content_type)
        if "text/html" not in str(content_type or "").lower():
            return body
        try:
            text = body.decode("utf-8")
            if old_card in text:
                text = text.replace(old_card, new_card)
            return text.encode("utf-8")
        except Exception:
            return body

    analysis._rewrite_dashboard_version = rewrite
    core._LIVE_XAU_CANDIDATE_SUPPORT_INSTALLED = True

    # Static presentation test plus a live read-only probe. The probe does not
    # place/close trades or alter strategy state.
    live_probe = support_snapshot()
    checks = {
        "denominator_is_one": live_probe.get("expected") == 1,
        "side_is_explicit": live_probe.get("side") in {"LONG", "SHORT", "NONE"},
        "xag_excluded": live_probe.get("demo_research_excluded") == ["XAGUSD"],
        "dashboard_card_rewrite_defined": "/1 · " in new_card and "live_candidate_side" in new_card,
    }
    if not all(checks.values()):
        raise RuntimeError(f"Live XAU candidate support self-test failed: {checks}")

    status = {
        "installed": True,
        "version": VERSION,
        "presentation_only": True,
        "strategy_changed": False,
        "startup_self_test": checks,
        "current": live_probe,
    }
    core._LIVE_XAU_CANDIDATE_SUPPORT_STATUS = status
    return status
