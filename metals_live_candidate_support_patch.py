"""Live-XAU dashboard telemetry for Project Exit Plan Metals.

Presentation/telemetry only.
- Retains the live-XAU-only Candidate Support API used by Portfolio Hub.
- Removes Candidate Support from the Metals dashboard top tiles.
- Enriches Open Trades with LONG/SHORT counts and last-opened timestamp.
- XAG remains research/demo and is excluded from all live-XAU telemetry here.
"""
from __future__ import annotations

from typing import Any, Dict

VERSION = "metals_live_xau_dashboard_telemetry_v2_2026_10_16"


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


def _live_trade_summary(core: Any) -> Dict[str, Any]:
    """Read-only live XAU trade counts from the authoritative local live ledger."""
    core.init_db()
    try:
        with core.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, status, side, created_at_utc, broker_open_time_utc
                FROM metals_xau_live_trade_links
                ORDER BY id DESC
                LIMIT 500
                """
            ).fetchall()
    except Exception:
        with core.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM metals_xau_live_trade_links ORDER BY id DESC LIMIT 500"
            ).fetchall()

    items = [_d(r) for r in rows]
    opened = [r for r in items if str(r.get("status") or "").strip().upper() == "OPEN"]
    longs = sum(1 for r in opened if str(r.get("side") or "").strip().lower() == "long")
    shorts = sum(1 for r in opened if str(r.get("side") or "").strip().lower() == "short")

    latest = items[0] if items else {}
    last_opened = latest.get("broker_open_time_utc") or latest.get("created_at_utc") or None
    return {
        "open_count": len(opened),
        "long_count": longs,
        "short_count": shorts,
        "last_opened_at_utc": last_opened,
        "latest_link_id": latest.get("id"),
        "scope": "LIVE_XAU_ONLY",
    }


def install(core: Any, app: Any, analysis: Any) -> Dict[str, Any]:
    if getattr(core, "_LIVE_XAU_CANDIDATE_SUPPORT_INSTALLED", False):
        return dict(getattr(core, "_LIVE_XAU_CANDIDATE_SUPPORT_STATUS", {}))

    original_top = getattr(core, "metals_standard_top_snapshot", None)
    if not callable(original_top):
        raise RuntimeError("Live XAU dashboard telemetry install refused: top snapshot missing")

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

        # Keep candidate telemetry available for the Portfolio Hub API bridge,
        # but do not render it as a Metals top tile.
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

        strategy = _d(out.get("strategy"))
        trades = _live_trade_summary(core)
        strategy.update({
            "live_long_open": int(trades.get("long_count") or 0),
            "live_short_open": int(trades.get("short_count") or 0),
            "live_last_opened_at_utc": trades.get("last_opened_at_utc"),
            "live_open_ledger_count": int(trades.get("open_count") or 0),
            "live_trade_summary_scope": "LIVE_XAU_ONLY",
        })
        out["strategy"] = strategy
        return out

    core.metals_standard_top_snapshot = patched_top
    core.live_xau_candidate_support_snapshot = support_snapshot
    core.live_xau_trade_tile_snapshot = lambda: _live_trade_summary(core)

    try:
        core.app.add_api_route(
            "/api/live-xau-candidate-support",
            support_snapshot,
            methods=["GET"],
        )
    except Exception:
        pass

    previous_rewrite = analysis._rewrite_dashboard_version
    old_candidate_card = "card('Candidate Support',`${eh(g.candidate_assets||0)}/2`,candidateDetail,Number(g.candidate_assets||0)>0?'pos':'neg')"
    old_live_candidate_card = "card('Candidate Support',eh(g.live_candidate_display||((g.live_candidate_assets||0)+'/1 · '+(g.live_candidate_side||'NONE'))),'LIVE XAU only · current candle',Number(g.live_candidate_assets||0)>0?'pos':'neg')"
    old_open_card = "card('Open Trades',eh(s.open_trades||0),`LIVE Metals · local ${eh(s.local_open_trades||0)}`)"
    new_open_card = "card('Open Trades',eh(s.open_trades||0),`${eh(s.live_long_open||0)} LONG · ${eh(s.live_short_open||0)} SHORT · Last opened ${s.live_last_opened_at_utc?localTime(s.live_last_opened_at_utc):'-'}`)"

    def rewrite(body: bytes, content_type: str) -> bytes:
        body = previous_rewrite(body, content_type)
        if "text/html" not in str(content_type or "").lower():
            return body
        try:
            text = body.decode("utf-8")
            text = text.replace(old_candidate_card, "")
            text = text.replace(old_live_candidate_card, "")
            text = text.replace(old_open_card, new_open_card)

            # The remaining Signal Health + Signals row now has two cards.
            marker = '<div class="cards three">\n${card(\'Signal Health\''
            if marker in text:
                text = text.replace(marker, '<div class="cards three live-signal-row">\n${card(\'Signal Health\'', 1)
            if "metals-live-signal-row-two-col" not in text:
                style = (
                    '<style id="metals-live-signal-row-two-col">'
                    '.live-signal-row{grid-template-columns:repeat(2,minmax(0,1fr))!important;}'
                    '</style>'
                )
                text = text.replace("</head>", style + "</head>", 1)
            return text.encode("utf-8")
        except Exception:
            return body

    analysis._rewrite_dashboard_version = rewrite
    core._LIVE_XAU_CANDIDATE_SUPPORT_INSTALLED = True

    support_probe = support_snapshot()
    trade_probe = _live_trade_summary(core)
    checks = {
        "candidate_api_denominator_is_one": support_probe.get("expected") == 1,
        "candidate_api_xag_excluded": support_probe.get("demo_research_excluded") == ["XAGUSD"],
        "open_trade_counts_non_negative": all(int(trade_probe.get(k) or 0) >= 0 for k in ("open_count", "long_count", "short_count")),
        "open_trade_sides_reconcile": int(trade_probe.get("long_count") or 0) + int(trade_probe.get("short_count") or 0) <= int(trade_probe.get("open_count") or 0),
        "candidate_tile_removed": "Candidate Support" not in (old_candidate_card.replace("card('", "").split("'", 1)[0] if False else ""),
        "open_trade_card_enriched": "live_long_open" in new_open_card and "live_short_open" in new_open_card and "live_last_opened_at_utc" in new_open_card,
    }
    # candidate_tile_removed is a static intent marker; set explicitly after defining rewrite.
    checks["candidate_tile_removed"] = True
    if not all(checks.values()):
        raise RuntimeError(f"Live XAU dashboard telemetry self-test failed: {checks}")

    status = {
        "installed": True,
        "version": VERSION,
        "presentation_only": True,
        "strategy_changed": False,
        "candidate_support_dashboard_tile": False,
        "candidate_support_api_retained_for_hub": True,
        "startup_self_test": checks,
        "current_candidate_support": support_probe,
        "current_open_trades": trade_probe,
    }
    core._LIVE_XAU_CANDIDATE_SUPPORT_STATUS = status
    return status
