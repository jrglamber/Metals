"""Pure, read-only contract for the LIVE Metals open positions dashboard.

Combines the native XAU-only broker positions with the separately broker-
verified LIVE XAG SHORT positions. Does not include XAG practice trades.
Unknown broker values stay unavailable instead of becoming a false zero.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


VERSION = "metals_live_positions_combined_v1_2026_11_12"


def combine(xau: Dict[str, Any], xag: Dict[str, Any]) -> Dict[str, Any]:
    xau_ok = xau.get("ok") is True
    xag_ok = xag.get("ok") is True
    entries: List[Dict[str, Any]] = []
    if xau_ok:
        for t in xau.get("trades") or []:
            entries.append({
                "instrument": str(t.get("instrument") or "XAU_USD"),
                "side": str(t.get("side") or "").upper(),
                "units": t.get("units"),
                "entry_price": t.get("price"),
                "unrealized_gbp": t.get("unrealized_pl"),
                "opened_at_utc": t.get("open_time") or "",
                "broker_trade_id": str(t.get("id") or ""),
                "stop_price": None,
                "manager": "XAU LIVE",
                "tracking": "BROKER OWNED",
                "lane": "XAU_LIVE",
            })
    if xag_ok:
        for t in xag.get("positions") or []:
            entries.append({
                "instrument": str(t.get("asset") or "XAG_USD"),
                "side": str(t.get("side") or "").upper(),
                "units": t.get("units"),
                "entry_price": t.get("entry_price"),
                "unrealized_gbp": t.get("unrealized_gbp"),
                "opened_at_utc": t.get("opened_at_utc") or "",
                "broker_trade_id": str(t.get("broker_trade_id") or ""),
                "stop_price": t.get("recorded_stop_price"),
                "manager": t.get("manager") or "",
                "tracking": "TRACKED" if t.get("tracked") else "UNLINKED — CHECK",
                "lane": "XAG_SHORT_LIVE",
            })
    xau_count = len(xau.get("trades") or []) if xau_ok else None
    xag_count = xag.get("count") if xag_ok else None
    if xag_ok and xag_count is None:
        xag_ok = False
        xag_count = None
    all_ok = xau_ok and xag_ok
    last_opened_at = max(
        (str(t.get("opened_at_utc") or "") for t in entries if t.get("opened_at_utc")),
        default="",
    )
    return {
        "ok": all_ok,
        "partial": not all_ok and (xau_ok or xag_ok),
        "version": VERSION,
        "scope": "LIVE_XAU_LONG_SHORT_PLUS_LIVE_XAG_SHORT",
        "xau_ok": xau_ok,
        "xag_ok": xag_ok,
        "xau_count": xau_count,
        "xag_count": xag_count,
        "xag_short_count": xag.get("short_count") if xag_ok else None,
        "count": (xau_count + xag_count) if all_ok else None,
        "trades": entries,
        "last_opened_at_utc": last_opened_at or None,
        "xag_broker_link_reconciled": xag.get("broker_link_reconciled") if xag_ok else None,
        "xag_untracked_broker_count": xag.get("untracked_broker_count") if xag_ok else None,
        "xag_unrealized_gbp": xag.get("unrealized_gbp") if xag_ok else None,
        "xag_unrealized_complete": xag.get("unrealized_complete") if xag_ok else False,
        "xau_error": str(xau.get("error") or "XAU broker unavailable") if not xau_ok else None,
        "xag_error": str(xag.get("reason") or xag.get("error") or "XAG broker unavailable") if not xag_ok else None,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "LIVE_OANDA_BROKER_XAU_AND_XAG_XAG_LIVE_LINKS",
        "practice_excluded": True,
        "broker_order_writes": False,
        "historical_realized_pnl_verified": False,
    }
