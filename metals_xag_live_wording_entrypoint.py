"""Metals presentation wrapper for authoritative XAG SHORT live wording.

Presentation/export only. Execution remains owned by metals_live_risk_entrypoint.
This wrapper removes stale XAG demo/research labels now that the XAG SHORT live
executor is armed, while keeping XAG LONG explicitly research-only and making
clear that legacy top tiles remain XAU-only until aggregate accounting expands.
"""
from __future__ import annotations

from typing import Any, Dict
import json

import metals_execution_contract_20261031 as execution_contract
import metals_xau_manual_campaign_reset as manual_campaign_reset
import metals_xau_manual_campaign_hwm_sync as campaign_hwm_sync
import metals_xag_live_independent_override as live_xag_health
import metals_xag_live_signal_status_panel as live_xag_signal_panel
import metals_xag_live_open_positions_panel as live_xag_open_positions
import metals_live_open_pnl_display as combined_open_pnl
import metals_reporting_snapshot as authoritative_reporting
from metals_route_priority import promote_read_only_route
from metals_live_risk_entrypoint import app as app
from metals_live_risk_entrypoint import core as core

# Research-only observer recovery; no execution policy or broker authority.
import metals_ai_observer_resilience as ai_observer_resilience
try:
    METALS_AI_OBSERVER_RECOVERY = ai_observer_resilience.install(core)
    print("METALS_AI_OBSERVER_RECOVERY " + json.dumps(METALS_AI_OBSERVER_RECOVERY, default=str), flush=True)
except Exception as ai_observer_exc:
    METALS_AI_OBSERVER_RECOVERY = {"installed": False, "error_type": type(ai_observer_exc).__name__}
    print("METALS_AI_OBSERVER_RECOVERY " + json.dumps(METALS_AI_OBSERVER_RECOVERY), flush=True)

MANUAL_XAU_RESET_INSTALL = manual_campaign_reset.install(core, app)
MANUAL_XAU_HWM_SYNC_INSTALL = campaign_hwm_sync.install(core, app)
COMBINED_OPEN_PNL_INSTALL = combined_open_pnl.install(app, core)

VERSION = "metals_xag_live_wording_v1_2026_10_31"

# These also flow through future ZIP export repairs because the existing
# execution-contract exporter reads this tuple at response time.
_EXPORT_REPLACEMENTS = (
    (
        "XAUUSD LONG + XAUUSD SHORT live lanes; XAG practice/research excluded from live totals",
        "XAUUSD LONG + XAUUSD SHORT + XAGUSD SHORT live lanes; XAGUSD LONG remains research-only; XAG SHORT accounting is reported separately from legacy XAU top-tile totals",
    ),
    (
        "LIVE metals capital is used by XAU LONG + XAU SHORT. XAG SHORT live trial is separately gated.",
        "LIVE metals capital is used by XAU LONG + XAU SHORT + XAG SHORT. XAG LONG remains research-only.",
    ),
    (
        "XAG SHORT live pilot status is reported from the authoritative executor state",
        "XAG SHORT is LIVE and reported from the authoritative executor state; XAG LONG remains research-only",
    ),
    (
        "XAG practice/research excluded from live totals",
        "XAG SHORT is LIVE and reported separately from legacy XAU totals; XAG LONG remains research-only",
    ),
)

_existing = tuple(getattr(execution_contract, "_STALE_REPLACEMENTS", ()) or ())
execution_contract._STALE_REPLACEMENTS = _existing + tuple(
    item for item in _EXPORT_REPLACEMENTS if item not in _existing
)

_HTML_REPLACEMENTS = (
    (
        "XAU LONG + XAU SHORT live · XAG demo/research",
        "XAU LONG + XAU SHORT + XAG SHORT live · XAG LONG research-only",
    ),
    (
        "XAU LONG + XAU SHORT live · XAG demo / research",
        "XAU LONG + XAU SHORT + XAG SHORT live · XAG LONG research-only",
    ),
    (
        "LIVE METALS — XAU LONG + XAU SHORT.",
        "LIVE METALS — XAU LONG + XAU SHORT + XAG SHORT.",
    ),
    (
        "Top tiles are LIVE XAU only: XAU LONG + XAU SHORT. XAG is demo/research and are shown lower in Broker / OANDA / Accounting.",
        "Broker P&L and the observed combined high-water/giveback cover LIVE XAU + LIVE XAG SHORT. Historical realised and the XAU protection high-water and base risk remain XAU-only; XAG LONG remains research-only.",
    ),
    (
        "XAG is demo/research",
        "XAG SHORT is LIVE; XAG LONG is research-only",
    ),
    (
        "XAG demo/research",
        "XAG SHORT live · XAG LONG research-only",
    ),
)


class XagLiveWordingMiddleware:
    def __init__(self, inner: Any):
        self.inner = inner

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or str(scope.get("path") or "") not in {"/", "/dashboard", "/dashboard/top", "/dashboard/section/latest-signals", "/dashboard/section/open-trades"}:
            await self.inner(scope, receive, send)
            return

        start = None
        chunks = []

        async def capture(message: Dict[str, Any]) -> None:
            nonlocal start
            if message.get("type") == "http.response.start":
                start = dict(message)
                return
            if message.get("type") != "http.response.body":
                await send(message)
                return

            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return

            body = b"".join(chunks)
            headers = list((start or {}).get("headers", []))
            content_type = ""
            for key, value in headers:
                if key.lower() == b"content-type":
                    content_type = value.decode("latin-1", errors="ignore").lower()
                    break

            if scope.get("path") == "/dashboard/top" and "application/json" in content_type:
                try:
                    top_payload = json.loads(body.decode("utf-8"))
                    if isinstance(top_payload, dict) and top_payload.get("status") == "ok":
                        # Top cards and Hub now share the same broker-observed
                        # snapshot and explicit cash/risk verification status.
                        report = authoritative_reporting.snapshot(core)
                        broker_proof = report["combined"]
                        top_payload = combined_open_pnl.project_dashboard_top(top_payload, broker_proof)
                        from metals_xag_realised_verified import apply_top
                        cash = report["cash"]
                        top_payload = apply_top(top_payload, cash["proof"] if cash["ok"]
                                                else {"ok": False})
                        top_payload["metals_reporting_snapshot"] = {
                            "version": report["version"],
                            "generated_at_utc": report["generated_at_utc"],
                            "complete": report["complete"],
                            "cash_status": cash["status"],
                            "risk_status": report["risk"]["status"],
                            "open_status": "BROKER_VERIFIED" if report["ok"] else "UNVERIFIED",
                        }
                        body = json.dumps(top_payload, separators=(",", ":"), default=str).encode("utf-8")
                except Exception as exc:
                    # Fail visibly rather than allowing legacy XAU-only top
                    # financials to masquerade as combined live Metals.
                    print("METALS_TOP_COMBINED_PROJECTION_FAILED "
                          + f"{type(exc).__name__}: {exc}", flush=True)
                    body = json.dumps({
                        "status": "error", "error": "Combined live Metals reporting unavailable",
                    }).encode("utf-8")

            if "text/html" in content_type:
                try:
                    text = body.decode("utf-8")
                    for old, new in _HTML_REPLACEMENTS:
                        text = text.replace(old, new)
                    if scope.get("path") == "/dashboard/section/latest-signals" and 'pep-xag-live-signal-status' not in text:
                        # Read-only authoritative LIVE XAG Short status. Keep
                        # existing practice guard table for research, but make
                        # its lack of live-execution authority explicit.
                        text = live_xag_signal_panel.render(core) + text
                    if scope.get("path") == "/dashboard/section/open-trades" and 'pep-live-xag-open-positions' not in text:
                        # Broker-authoritative XAG SHORT positions, separately
                        # from the legacy XAU-only open-trades view.
                        text = live_xag_open_positions.render(core) + text
                    if scope.get("path") == "/dashboard":
                        # Integrate at the existing top-card renderer; never
                        # mutate another KPI card after a legacy refresh.
                        text = combined_open_pnl.rewrite_dashboard_html(text)
                    if scope.get("path") in {"/", "/dashboard"}:
                        add = ""
                        if "pep-xau-campaign-reset" not in text:
                            add += manual_campaign_reset.DASHBOARD_SNIPPET
                        if "pep-xag-live-health" not in text:
                            add += live_xag_health.DASHBOARD_SNIPPET
                        if add:
                            anchor = "<h2>Details</h2>"
                            if anchor in text:
                                text = text.replace(anchor, anchor + add, 1)
                            else:
                                text = text.replace("</body>", add + "</body>", 1)
                    body = text.encode("utf-8")
                except Exception as exc:
                    print("METALS_TOP_HTML_REWRITE_FAILED "
                          + f"{type(exc).__name__}: {exc}", flush=True)
                    if scope.get("path") == "/dashboard":
                        # Make a presentation failure obvious instead of
                        # silently claiming XAU-only values are combined.
                        text = body.decode("utf-8", errors="replace")
                        text = text.replace('<div id="topStatus"',
                            '<div class="section-note" style="color:#fbbf24">Combined Metals dashboard rendering unavailable; use Portfolio Hub for broker-verified XAU + XAG figures.</div><div id="topStatus"', 1)
                        body = text.encode("utf-8")

            clean = [(k, v) for k, v in headers if k.lower() not in {b"content-length", b"content-encoding"}]
            clean.append((b"content-length", str(len(body)).encode("ascii")))
            head = dict(start or {"type": "http.response.start", "status": 200})
            head["headers"] = clean
            await send(head)
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.inner(scope, receive, capture)


# Canonical producer-owned GET contract consumed by both top cards and Hub.
# Each sub-snapshot retains its own age and confidence; never fabricate zeros.
@app.get("/api/live-metals-reporting-snapshot")
def live_metals_reporting_snapshot():
    return authoritative_reporting.snapshot(core)

REPORTING_SNAPSHOT_ROUTE = promote_read_only_route(
    app, "/api/live-metals-reporting-snapshot", live_metals_reporting_snapshot
)
print("METALS_REPORTING_SNAPSHOT_ROUTE " +
      json.dumps({"installed": REPORTING_SNAPSHOT_ROUTE, "version": authoritative_reporting.VERSION}),
      flush=True)

# Read-only lane-accounting probe. Do not expose partial totals as authoritative.
@app.get("/api/live-metals-lane-accounting")
def live_metals_lane_accounting():
    from metals_live_lane_accounting import live_lane_financials
    from metals_live_risk_entrypoint import core
    try:
        with core.get_conn() as conn:
            txs = [dict(r) for r in conn.execute(
                "SELECT * FROM metals_xau_live_broker_transactions ORDER BY transaction_time").fetchall()]
            links = [dict(r) for r in conn.execute(
                "SELECT * FROM metals_xau_live_trade_links").fetchall()]
        broker = core.metals_xau_live_broker_snapshot(include_account_summary=False) or {}
        if broker.get("ok") is not True:
            return {"ok": False, "reporting_status": "broker_unavailable"}
        # XAG SHORT uses a separate trade-links table. Audit its coverage
        # independently; realized_pl there is not a verified GBP net ledger.
        with core.get_conn() as conn:
            xag_links = [dict(r) for r in conn.execute(
                "SELECT broker_trade_id, status, realized_pl, closed_at_utc "
                "FROM metals_xag_live_trade_links").fetchall()]
        xag_closed = [r for r in xag_links if str(r.get("status") or "").upper().startswith("CLOSED")]
        xag_missing = sum(1 for r in xag_closed if r.get("realized_pl") is None)
        # The legacy XAU ledger does not prove XAG SHORT completeness.
        # Require an independently verified XAG ledger before declaring success.
        result = live_lane_financials(txs, links, broker.get("owned_open_trades") or [])
        result["reconciled"] = False
        # Counts are diagnostics, not proof of complete broker coverage.
        # Include an observation timestamp and the missing reconciliation
        # dependencies so the Hub can distinguish stale and incomplete data.
        from datetime import datetime, timezone
        result["time_utc"] = datetime.now(timezone.utc).isoformat()
        result["xau_ledger_audit"] = {
            "broker_transaction_rows": len(txs),
            "trade_link_rows": len(links),
            "unattributed_transaction_rows": result.get("unattributed_count"),
            "duplicate_transaction_rows": result.get("duplicate_count"),
            "conflicting_trade_ids": result.get("conflicting_trade_id_count"),
            "authoritative_broker_history_verified": False,
        }
        result["xag_short_ledger_audit"] = {
            "trade_links": len(xag_links), "closed_links": len(xag_closed),
            "closed_without_realized_pl": xag_missing,
            "net_gbp_broker_transactions_verified": False,
        }
        result["missing_reconciliation_requirements"] = [
            "authoritative_broker_transaction_history_coverage",
            "xag_short_net_gbp_transaction_attribution",
            "broker_account_currency_and_open_pnl_conversion",
        ]
        result["coverage_warning"] = "XAG SHORT has a separate trade-links ledger; net GBP broker transactions not verified"
        for lane in result["live_lanes"].values():
            lane["reporting_status"] = "awaiting_reconciliation"
            for key in ("realised_week_gbp", "realised_month_gbp", "realised_all_time_gbp", "unrealised_gbp"):
                lane[key] = None
        # XAG Short is independently reconciled from authoritative OANDA
        # transactions. XAU LONG/SHORT per-lane split remains unverified; the
        # existing XAU aggregate accounting source remains unchanged.
        from metals_xag_transaction_audit import snapshot as xag_cash_snapshot
        from metals_xag_realised_verified import verify as verify_xag_cash
        try:
            xag_cash = verify_xag_cash(xag_cash_snapshot(core))
            result["xag_short_verified_realised"] = xag_cash
            result["xag_short_net_gbp_verified"] = True
            result["xag_short_ledger_audit"]["net_gbp_broker_transactions_verified"] = True
            result["missing_reconciliation_requirements"] = [
                "authoritative_xau_broker_transaction_history_coverage",
                "xau_long_short_net_gbp_per_lane_attribution",
            ]
            result["coverage_warning"] = (
                "XAG SHORT net GBP broker cash verified; "
                "XAU LONG/SHORT historical per-side split remains unverified"
            )
            result["reporting_status"] = "xag_short_net_verified_xau_split_pending"
        except Exception as xag_exc:
            result["xag_short_net_gbp_verified"] = False
            result["xag_short_verification_error"] = type(xag_exc).__name__
            result["reporting_status"] = "awaiting_reconciliation"
        # Do NOT claim all three lane ledgers are independently reconciled.
        result["reconciled"] = False
        result["ok"] = False
        return result
    except Exception as exc:
        return {"ok": False, "reporting_status": "schema_or_broker_error",
                "error": f"{type(exc).__name__}: {exc}"}


# This late-installed GET route must precede the pre-existing root mount.
# Otherwise the Hub receives HTTP 404 and labels lane reporting unavailable.
LANE_ACCOUNTING_ROUTE = promote_read_only_route(
    app, "/api/live-metals-lane-accounting", live_metals_lane_accounting
)
print("METALS_LANE_ACCOUNTING_ROUTE " + str(LANE_ACCOUNTING_ROUTE), flush=True)

# On-demand, read-only XAG realised-ledger audit. A separate endpoint avoids
# adding OANDA historical requests to the Hub's normal 5-second refresh.
# Diagnostics are aggregate counts only; no raw trade IDs/account tokens.
@app.get("/api/metals-xag-ledger-audit")
def metals_xag_ledger_audit_endpoint():
    from metals_xag_ledger_audit import snapshot
    return snapshot(core)

XAG_LEDGER_AUDIT_ROUTE = promote_read_only_route(
    app, "/api/metals-xag-ledger-audit", metals_xag_ledger_audit_endpoint
)
print("METALS_XAG_LEDGER_AUDIT_ROUTE " + str(XAG_LEDGER_AUDIT_ROUTE), flush=True)

# Shadow reconciliation of broker transaction cash movements (GBP).
# On-demand only; does not alter accounting or claim success in the Hub.
@app.get("/api/metals-xag-transaction-audit")
def metals_xag_transaction_audit_endpoint():
    from metals_xag_transaction_audit import snapshot
    return snapshot(core)

XAG_TRANSACTION_AUDIT_ROUTE = promote_read_only_route(
    app, "/api/metals-xag-transaction-audit", metals_xag_transaction_audit_endpoint
)
print("METALS_XAG_TRANSACTION_AUDIT_ROUTE " + str(XAG_TRANSACTION_AUDIT_ROUTE), flush=True)

app.add_middleware(XagLiveWordingMiddleware)

# Read-only XAG routing audit. Do not replay or retry candidate execution.
try:
    import json as _json
    from metals_xag_execution_chain_diag import snapshot as _xag_chain_snapshot
    _xag_chain = _xag_chain_snapshot(core, limit=20)
    print("METALS_XAG_SHORT_EXECUTION_CHAIN " + _json.dumps(_xag_chain, default=str), flush=True)
    import metals_xag_execution_chain_diag as _xag_diag
    print("METALS_XAG_LIVE_OWNERSHIP " + _json.dumps(_xag_diag.ownership_snapshot(core), default=str), flush=True)
except Exception as _xag_chain_exc:
    print(f"METALS_XAG_SHORT_EXECUTION_CHAIN error={type(_xag_chain_exc).__name__}: {_xag_chain_exc}", flush=True)

print(
    "METALS_XAG_LIVE_WORDING "
    f"version={VERSION} xag_short=LIVE xag_long=RESEARCH_ONLY "
    "open_pnl=XAU_PLUS_XAG_SHORT high_water=COMBINED_OBSERVED_SEPARATE_FROM_XAU_PROTECTION realised=XAU_ONLY open_trades=XAU_PLUS_XAG_SHORT presentation_only=True "
    f"manual_xau_campaign_reset={MANUAL_XAU_RESET_INSTALL.get('version')} "
    f"reset_routes={MANUAL_XAU_RESET_INSTALL.get('route_count')}",
    flush=True,
)
