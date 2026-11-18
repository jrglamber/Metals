"""Read-only LIVE XAG SHORT open-basket R, from broker GBP P&L and entry risk.

Sum each open trade's (unrealised GBP / its recorded initial effective GBP risk).
This mirrors a sum-of-trade-R basket, not P&L divided by the XAU risk target.
Never use broker stop-floor risk (which changes as stops ratchet), practice trades,
unlinked trades, or historic individual MFE peaks as current basket R.
"""
from __future__ import annotations

from math import isfinite

VERSION = "metals_xag_live_open_basket_r_v1_20261118"
SCOPE = "LIVE_XAG_SHORT_OPEN_INITIAL_RISK"


def _number(v):
    if v is None or isinstance(v, bool) or v == "":
        return None
    try:
        x = float(v)
        return x if isfinite(x) else None
    except (TypeError, ValueError):
        return None


def project(positions, combined, links):
    """Strictly reconcile a single broker snapshot to all open XAG ledger links."""
    if (not isinstance(combined, dict) or combined.get("ok") is not True
            or not isinstance(positions, dict) or positions.get("ok") is not True
            or positions.get("scope") != "LIVE_XAU_LONG_SHORT_PLUS_LIVE_XAG_SHORT"):
        raise ValueError("Combined LIVE Metals broker proof unavailable")
    if (positions.get("xag_broker_link_reconciled") is not True
            or positions.get("xag_unrealized_complete") is not True
            or positions.get("practice_excluded") is not True):
        raise ValueError("XAG broker ownership/reconciliation incomplete")
    count = positions.get("xag_short_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("XAG open count unavailable")
    rows = [r for r in positions.get("trades", []) if r.get("lane") == "XAG_SHORT_LIVE"]
    if len(rows) != count or not isinstance(links, list) or len(links) != count:
        raise ValueError("XAG broker/ledger open trade population differs")

    indexed = {}
    for link in links:
        bid = str(link.get("broker_trade_id") or "").strip()
        if not bid or bid in indexed or str(link.get("status") or "").upper() not in (
                "OPEN", "OPEN_UNVERIFIED_STOP", "CLOSE_RETRY"):
            raise ValueError("Duplicate, missing or non-live XAG ledger link")
        indexed[bid] = link

    seen = set()
    total_r = 0.0
    cash = 0.0
    for trade in rows:
        bid = str(trade.get("broker_trade_id") or "").strip()
        if (not bid or bid in seen or bid not in indexed
                or trade.get("tracking") != "TRACKED"
                or str(trade.get("side") or "").upper() != "SHORT"
                or str(trade.get("instrument") or "").upper().replace("_", "") != "XAGUSD"):
            raise ValueError("LIVE XAG trade not exactly broker-ledger reconciled")
        seen.add(bid)
        pnl = _number(trade.get("unrealized_gbp"))
        risk = _number(indexed[bid].get("estimated_risk_amount"))
        if pnl is None or risk is None or risk <= 0:
            raise ValueError("Missing XAG broker GBP P&L or initial GBP risk")
        total_r += pnl / risk
        cash += pnl
    if seen != set(indexed):
        raise ValueError("Orphan XAG live link")
    confirmed = _number(combined.get("xag_short_open_pnl_gbp"))
    if confirmed is None or abs(cash - confirmed) > 0.05:
        raise ValueError("XAG lane GBP P&L does not match authoritative combined snapshot")
    return {
        "ok": True, "version": VERSION, "scope": SCOPE,
        "open_count": count, "current_r": round(total_r, 4),
        "open_pnl_gbp": round(cash, 4),
        "observed_at_utc": positions.get("observed_at_utc"),
        "risk_basis": "SUM_OPEN_TRADE_UNREALISED_GBP_DIVIDED_BY_INITIAL_EFFECTIVE_RISK_GBP",
        "reconciled": True, "practice_excluded": True, "broker_writes": False,
    }
