"""Read-only diagnostic of XAG SHORT realised broker coverage.

This module does not alter a trader's positions or database and deliberately
does not promote trade-level realisedPL as net GBP transaction accounting.
Only aggregate coverage diagnostics are returned, never trade IDs or tokens.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone

VERSION = "xag_short_broker_ledger_audit_v1_2026_11_17"
_CACHE_SECONDS = 300
_MAX_PAGES = 6
_PAGE_SIZE = 500
_cache = None
_cache_at = 0.0
_lock = threading.Lock()


def _num(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def _id(value):
    try:
        s = str(value or "").strip()
        return int(s) if s.isdigit() and int(s) > 0 else None
    except (ValueError, TypeError):
        return None


def _utc(value):
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            return None
        return d.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def audit_links(local_links, broker_trades, account_currency, history_complete,
                history_pages, errors=()):
    """Audit closed trade coverage without pretending a trade P&L is a net ledger."""
    result = {
        "ok": bool(history_complete) and str(account_currency).upper() == "GBP",
        "reporting_status": "awaiting_reconciliation",
        "version": VERSION,
        "account_currency": str(account_currency or "UNKNOWN").upper(),
        "broker_history_complete": bool(history_complete),
        "broker_history_pages": int(history_pages),
        "historical_net_gbp_transactions_verified": False,
        "errors": list(errors)[:12],
    }
    links = [x for x in local_links if isinstance(x, dict)]
    local_ids = [_id(x.get("broker_trade_id")) for x in links]
    local_valid_ids = [x for x in local_ids if x is not None]
    repeated_local = len(local_valid_ids) != len(set(local_valid_ids))
    min_linked = min(local_valid_ids) if local_valid_ids else None
    broker = {}
    repeated_broker = 0
    for raw in broker_trades:
        if not isinstance(raw, dict):
            continue
        bid = _id(raw.get("id"))
        if bid is None:
            repeated_broker += 1
        elif bid in broker:
            repeated_broker += 1
        else:
            broker[bid] = raw
    missing_broker = [bid for bid in local_valid_ids if bid not in broker]
    # Broker XAG short trades opened after the pilot's first linked trade but
    # absent locally must prevent claiming full ownership/completeness.
    relevant = {
        bid: row for bid, row in broker.items()
        if str(row.get("instrument") or "").upper() == "XAG_USD"
        and (_num(row.get("initialUnits")) or 0) < 0
        and min_linked is not None and bid >= min_linked
    }
    unlinked_broker = sorted(set(relevant) - set(local_valid_ids))
    closed = {bid: row for bid, row in relevant.items()
              if str(row.get("state") or "").upper() == "CLOSED"}
    open_broker = {bid: row for bid, row in relevant.items()
                   if str(row.get("state") or "").upper() == "OPEN"}
    closed_local = {bid for bid, row in zip(local_ids, links)
                    if bid is not None and str(row.get("status") or "").upper().startswith("CLOSED")}
    false_closures = [bid for bid in closed_local if bid not in closed]
    unrecorded_closures = [bid for bid in closed if bid not in closed_local]
    invalid_financials = 0
    nonzero_financing = 0
    nonzero_dividends = 0
    multi_close_count = 0
    open_partial_count = 0
    missing_close_time = 0
    # No realized totals returned here: complete per-period net attribution
    # requires the broker's individual cash transactions and fee entries.
    for bid, row in relevant.items():
        if _num(row.get("realizedPL")) is None:
            invalid_financials += 1
        financing = _num(row.get("financing"))
        dividends = _num(row.get("dividendAdjustment"))
        if financing is not None and abs(financing) > 1e-8:
            nonzero_financing += 1
        if dividends is not None and abs(dividends) > 1e-8:
            nonzero_dividends += 1
        closes = row.get("closingTransactionIDs") or []
        if len(closes) > 1:
            multi_close_count += 1
        if bid in open_broker and (_num(row.get("realizedPL")) or 0) != 0:
            open_partial_count += 1
        if bid in closed and _utc(row.get("closeTime")) is None:
            missing_close_time += 1
    result.update({
        "local_trade_links": len(links),
        "local_invalid_trade_ids": len(local_ids) - len(local_valid_ids),
        "local_duplicate_trade_ids": len(local_valid_ids) - len(set(local_valid_ids)),
        "broker_xag_short_since_first_link": len(relevant),
        "broker_xag_short_closed": len(closed),
        "broker_xag_short_open": len(open_broker),
        "local_xag_closed_links": len(closed_local),
        "local_links_missing_from_broker": len(missing_broker),
        "broker_shorts_not_locally_linked": len(unlinked_broker),
        "local_closed_not_broker_closed": len(false_closures),
        "broker_closed_not_locally_closed": len(unrecorded_closures),
        "broker_invalid_realised_pl": invalid_financials,
        "broker_nonzero_financing_trades": nonzero_financing,
        "broker_nonzero_dividend_trades": nonzero_dividends,
        "broker_multiclose_trades": multi_close_count,
        "broker_open_partially_realised": open_partial_count,
        "broker_closed_missing_close_time": missing_close_time,
    })
    ownership_ok = bool(result["ok"] and links and not repeated_local
                        and len(local_valid_ids) == len(local_ids)
                        and not repeated_broker and not missing_broker
                        and not unlinked_broker and not false_closures
                        and not unrecorded_closures and not invalid_financials
                        and not missing_close_time)
    result["trade_ownership_verified"] = ownership_ok
    result["net_realised_gbp_verified"] = False
    result["blockers"] = [reason for reason, triggered in [
        ("GBP_account_currency_unverified", result["account_currency"] != "GBP"),
        ("broker_history_not_exhaustive", not history_complete),
        ("local_trade_links_missing_or_duplicate", not local_valid_ids or repeated_local or len(local_ids) != len(local_valid_ids)),
        ("local_trade_ownership_mismatch", bool(missing_broker or unlinked_broker)),
        ("local_broker_close_state_mismatch", bool(false_closures or unrecorded_closures)),
        ("broker_trade_fields_invalid", bool(invalid_financials or missing_close_time)),
        ("net_GBP_transactions_not_yet_attributed", True),
    ] if triggered]
    return result


def _read(core):
    import xag_short_live_override as xag
    account_id = xag._account_id(core)
    if not account_id:
        raise RuntimeError("no live XAG broker account ID")
    with core.get_conn() as conn:
        links = [dict(x) for x in conn.execute(
            "SELECT broker_trade_id, instrument, side, status, realized_pl, "
            "created_at_utc, closed_at_utc FROM metals_xag_live_trade_links"
        ).fetchall()]
    if not links:
        return audit_links([], [], "UNKNOWN", False, 0, ("no_local_XAG_trade_links",))
    # Dedicated transport must refuse non-live OANDA; only GET operations.
    summary = xag._request(core, "/v3/accounts/"+account_id+"/summary", "GET")
    if summary.get("ok") is not True:
        raise RuntimeError("OANDA account currency read failed")
    home = str(((summary.get("data") or {}).get("account") or {}).get("currency") or "")
    history = []
    before = None
    complete = False
    last_min = None
    pages = 0
    for page in range(_MAX_PAGES):
        from urllib.parse import urlencode
        params = dict(state="ALL", instrument="XAG_USD", count=_PAGE_SIZE)
        if before is not None:
            params["beforeID"] = before
        result = xag._request(
            core, "/v3/accounts/"+account_id+"/trades?"+urlencode(params), "GET"
        )
        if result.get("ok") is not True:
            raise RuntimeError("OANDA XAG trade history read failed")
        trades = (result.get("data") or {}).get("trades")
        if not isinstance(trades, list):
            raise RuntimeError("OANDA XAG trade history missing trades array")
        pages += 1
        ids = [_id(t.get("id")) for t in trades]
        if len(trades) > _PAGE_SIZE or any(i is None for i in ids):
            raise RuntimeError("OANDA XAG trade history invalid trade IDs")
        if ids:
            smallest = min(ids)
            if last_min is not None and smallest >= last_min:
                raise RuntimeError("OANDA XAG history pagination repeated")
            last_min = smallest
        history.extend(trades)
        if len(trades) < _PAGE_SIZE:
            complete = True
            break
        before = str(min(ids))
    return audit_links(links, history, home, complete, pages,
                       () if complete else ("OANDA_history_page_cap_hit",))


def snapshot(core):
    """Cache audit for 5 minutes to avoid unnecessary broker API requests."""
    global _cache, _cache_at
    with _lock:
        if _cache is not None and time.monotonic() - _cache_at < _CACHE_SECONDS:
            return dict(_cache)
        try:
            output = _read(core)
        except Exception as exc:
            output = {
                "ok": False, "version": VERSION,
                "reporting_status": "awaiting_reconciliation",
                "trade_ownership_verified": False,
                "net_realised_gbp_verified": False,
                "blockers": ["broker_or_db_audit_unavailable"],
                "error_type": type(exc).__name__,
            }
        output["time_utc"] = datetime.now(timezone.utc).isoformat()
        _cache = dict(output)
        _cache_at = time.monotonic()
        return output
