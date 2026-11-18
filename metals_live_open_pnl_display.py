"""Read-only, broker-verified combined LIVE Metals P&L for the dashboard.

The legacy Metals top tile is XAU-only; the Portfolio Hub already includes
LIVE XAU and LIVE XAG SHORT. This endpoint shares the Hub's strict trade-level
scope and reconciles the same OANDA open-trade P&L without touching execution,
historical XAU realised P&L, high-water marks or risk settings.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

VERSION = "metals_combined_live_open_pnl_v1_2026_11_16"
SCOPE = "LIVE_XAU_LONG_SHORT_PLUS_LIVE_XAG_SHORT"


def _number(value):
    if value is None or value == "":
        return None
    try:
        n = float(value)
        return n if isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _count(value):
    n = _number(value)
    return int(n) if n is not None and n >= 0 and n == int(n) else None


def reconcile(payload, max_age_seconds=120):
    """Fail closed rather than present XAU-only or incomplete P&L as combined."""
    if not isinstance(payload, dict) or payload.get("scope") != SCOPE or payload.get("ok") is not True:
        raise ValueError("Combined LIVE Metals broker scope unavailable")
    for flag in ("xau_ok", "xag_ok", "practice_excluded",
                 "xag_broker_link_reconciled", "xag_unrealized_complete"):
        if payload.get(flag) is not True:
            raise ValueError("Combined LIVE Metals broker reconciliation incomplete: " + flag)
    if _count(payload.get("xag_untracked_broker_count")) != 0:
        raise ValueError("Untracked broker XAG positions")
    try:
        ts = datetime.fromisoformat(str(payload["observed_at_utc"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            raise ValueError("Naive broker snapshot timestamp")
        age = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
        if not -10 <= age <= max_age_seconds:
            raise ValueError("Stale LIVE Metals broker snapshot")
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Invalid LIVE Metals broker snapshot timestamp") from exc

    xau_count, xag_count, xag_short_count, total = (
        _count(payload.get(key)) for key in ("xau_count", "xag_count", "xag_short_count", "count")
    )
    trades = payload.get("trades")
    if (None in (xau_count, xag_count, xag_short_count, total)
            or not isinstance(trades, list) or xag_count != xag_short_count
            or total != xau_count + xag_count or len(trades) != total):
        raise ValueError("LIVE Metals broker position counts disagree")

    seen = set()
    xau_n = xag_n = 0
    xau_pnl = xag_pnl = 0.0
    for trade in trades:
        if not isinstance(trade, dict):
            raise ValueError("Invalid broker position")
        broker_id = str(trade.get("broker_trade_id") or "").strip()
        if not broker_id or broker_id in seen:
            raise ValueError("Missing or duplicate broker trade ID")
        seen.add(broker_id)
        pnl = _number(trade.get("unrealized_gbp"))
        instrument = str(trade.get("instrument") or "").upper().replace("_", "").replace("/", "")
        if pnl is None:
            raise ValueError("Missing broker P&L")
        if trade.get("lane") == "XAU_LIVE" and instrument == "XAUUSD":
            xau_n += 1
            xau_pnl += pnl
        elif trade.get("lane") == "XAG_SHORT_LIVE" and instrument == "XAGUSD":
            if str(trade.get("side") or "").upper() != "SHORT" or trade.get("tracking") != "TRACKED":
                raise ValueError("Untracked or non-short XAG position")
            xag_n += 1
            xag_pnl += pnl
        else:
            raise ValueError("Unknown LIVE Metals position ownership")
    xag_given = _number(payload.get("xag_unrealized_gbp"))
    if (xau_n != xau_count or xag_n != xag_count or xag_given is None
            or abs(xag_given - xag_pnl) > 0.05):
        raise ValueError("LIVE Metals broker P&L/position reconciliation mismatch")
    return {
        "ok": True, "version": VERSION, "scope": SCOPE,
        "open_pnl_gbp": round(xau_pnl + xag_pnl, 4),
        "xau_open_pnl_gbp": round(xau_pnl, 4),
        "xag_short_open_pnl_gbp": round(xag_pnl, 4),
        "open_count": total, "xau_count": xau_count, "xag_short_count": xag_count,
        "observed_at_utc": payload["observed_at_utc"],
        "source": "metals:/api/live-metals-open-positions",
        "historical_realised_scope": "XAU_ONLY_XAG_LEDGER_PENDING",
        "broker_writes": False,
    }


def live_snapshot(core):
    """Same broker-verified combined proof for the API and top dashboard."""
    try:
        from metals_dashboard_clean_entrypoint import live_metals_open_positions
        snapshot = reconcile(live_metals_open_positions())
        try:
            from metals_combined_observed_hwm import observe
            snapshot.update(observe(core, snapshot))
        except Exception as exc:
            snapshot.update({
                "high_water_status": "UNAVAILABLE",
                "high_water_gbp": None, "high_water_at_utc": None,
                "high_water_tracking_started_at_utc": None,
                "giveback_gbp": None, "giveback_pct": None,
                "high_water_error": f"{type(exc).__name__}: {exc}"[:160],
            })
        return snapshot
    except Exception as exc:
        return {
            "ok": False, "version": VERSION, "scope": SCOPE,
            "open_pnl_gbp": None, "high_water_status": "UNAVAILABLE",
            "high_water_gbp": None, "giveback_gbp": None,
            "xau_open_pnl_gbp": None, "xag_short_open_pnl_gbp": None,
            "observed_at_utc": None,
            "reason": f"{type(exc).__name__}: {exc}"[:220],
            "broker_writes": False,
        }


def project_dashboard_top(payload, proof):
    """Project LIVE XAU+XAG accounting into the presentation-only top endpoint.

    Preserve the full XAU-only producer strategy separately. Do not project
    historical realised or XAU protection state as combined financials.
    Missing broker proof must never fall back to seemingly valid XAU-only totals.
    """
    import copy
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return payload
    out = copy.deepcopy(payload)
    original = out.get("strategy")
    if not isinstance(original, dict):
        raise ValueError("Live Metals top strategy missing")
    out["xau_only_strategy"] = copy.deepcopy(original)
    s = out["strategy"]
    verified = (isinstance(proof, dict) and proof.get("ok") is True
                and proof.get("scope") == SCOPE)
    s["headline_pnl"] = _number(proof.get("open_pnl_gbp")) if verified else None
    s["open_pnl"] = s["headline_pnl"]
    s["open_trades"] = _count(proof.get("open_count")) if verified else None
    s["xau_count"] = _count(proof.get("xau_count")) if verified else None
    s["xag_short_count"] = _count(proof.get("xag_short_count")) if verified else None
    peak = bool(verified and proof.get("high_water_status") == "OBSERVED_COMBINED_LIVE")
    s["high_water_gbp"] = _number(proof.get("high_water_gbp")) if peak else None
    s["high_water_time"] = proof.get("high_water_at_utc") if peak else None
    s["high_water_tracking_started_at_utc"] = (
        proof.get("high_water_tracking_started_at_utc") if peak else None)
    s["giveback_gbp"] = _number(proof.get("giveback_gbp")) if peak else None
    s["giveback_pct"] = _number(proof.get("giveback_pct")) if peak else None
    # Never reinterpret an XAU-only R multiple as XAU + XAG portfolio R.
    for key in ("basket_r", "high_water_r", "giveback_r"):
        s[key] = None
    s["combined_reporting_status"] = (
        "BROKER_VERIFIED" if peak else "HWM_UNAVAILABLE" if verified
        else "BROKER_UNVERIFIED")
    s["combined_reporting_reason"] = (
        None if peak else str(proof.get("high_water_error") or
                              proof.get("reason") or "Combined Metals proof unavailable")[:180])
    s["combined_reporting_scope"] = SCOPE
    s["realised_scope"] = "XAU_ONLY_XAG_LEDGER_PENDING"
    out["combined_metals_source"] = "/api/live-metals-open-pnl"
    return out


# Replace the *existing* dashboard renderer's labels, not its live execution
# model, so /dashboard/top remains the single writer of all visible KPI cards.
# The legacy renderer fetches /dashboard/top and then replaces #topTiles using
# innerHTML. A separate late-running DOM patch causes races and cross-card edits.
_TOP_RENDER_REPLACEMENTS = (
    ("card('Broker P&L',money(s.headline_pnl),", 
     "card('Broker P&L · LIVE XAU + XAG SHORT',money(s.headline_pnl),"),
    ("LIVE Metals open P&L · Realised ",
     "Combined broker-verified · Realised " + chr(36) + "{s.xag_realised_verified?'XAU + XAG SHORT':'XAU-only (XAG audit pending)'} "),
    ("card('High-Water',money(s.high_water_gbp),",
     "card('High-Water · LIVE XAU + XAG SHORT',money(s.high_water_gbp),"),
    ("DOLLAR{Number(s.high_water_r||0).toFixed(2)}R · ",
     "Combined observed peak · "),
    (" · LIVE Metals" + chr(96), " · XAU protection separate" + chr(96)),
    ("card('Giveback'," + chr(96) + "DOLLAR{money(s.giveback_gbp)}",
     "card('Giveback · LIVE XAU + XAG SHORT'," + chr(96) + "DOLLAR{money(s.giveback_gbp)}"),
    (chr(96) + "DOLLAR{money(s.giveback_gbp)} · DOLLAR{Number(s.giveback_pct||0).toFixed(1)}%" + chr(96),
     chr(96) + "DOLLAR{money(s.giveback_gbp)} · DOLLAR{s.giveback_pct==null?'unavailable':Number(s.giveback_pct).toFixed(1)+'%'}" + chr(96)),
    ("DOLLAR{Number(s.giveback_r||0).toFixed(2)}R",
     "From combined observed peak · XAU protection separate"),
    ("card('Open Trades',eh(s.open_trades||0),",
     "card('Open Trades',s.open_trades==null?'—':eh(s.open_trades),"),
    ("LIVE Metals · local DOLLAR{eh(s.local_open_trades||0)}",
     "LIVE XAU DOLLAR{eh(s.xau_count==null?'—':s.xau_count)} · LIVE XAG SHORT DOLLAR{eh(s.xag_short_count==null?'—':s.xag_short_count)}"),
    ("Oldest DOLLAR{eh(s.oldest_hold||0)}h",
     "XAU only · Oldest DOLLAR{eh(s.oldest_hold||0)}h"),
)

def rewrite_dashboard_html(html):
    if "async function loadTop(force=false)" not in html:
        raise ValueError("Metals top renderer signature changed")
    result = html
    for find, replace in _TOP_RENDER_REPLACEMENTS:
        find, replace = find.replace("DOLLAR", "$"), replace.replace("DOLLAR", "$")
        if find not in result:
            raise ValueError("Metals top renderer contract missing: " + find[:80])
        result = result.replace(find, replace, 1)
    # The original Metals renderer's money(null) was £0.00 because
    # Number(null) === 0. For UNKNOWN combined broker figures this is
    # financially misleading. Zero is only valid when the API says 0.
    money_code="function money(v){const n=Number(v);"
    if money_code not in result:
        raise ValueError("Metals money formatter changed; null guard missing")
    result=result.replace(
        money_code,
        "function money(v){if(v==null||v==='')return '—';const n=Number(v);",
        1,
    )
    return result


def install(app, core):
    @app.get("/api/live-metals-open-pnl")
    def live_metals_open_pnl():
        return live_snapshot(core)
    # analysis_entrypoint already mounted core.app at "/" before this wrapper
    # installs late routes. Starlette would send /api/live-metals-open-pnl
    # straight to that catch-all mount (404). Put this API route first, as the
    # other production-level risk and telemetry routes already do.
    candidates = [route for route in app.router.routes
                  if getattr(route, "path", None) == "/api/live-metals-open-pnl"
                  and getattr(route, "endpoint", None) is live_metals_open_pnl]
    if len(candidates) != 1:
        raise RuntimeError("Combined LIVE Metals P&L endpoint registration failed")
    route = candidates[0]
    app.router.routes.remove(route)
    app.router.routes.insert(0, route)
    return {"installed": True, "version": VERSION, "broker_writes": False,
            "route": "/api/live-metals-open-pnl", "route_priority": 0}
