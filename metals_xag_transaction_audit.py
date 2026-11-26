"""XAG Short net-GBP broker transaction attribution (read-only).

Uses OANDA's account-currency ORDER_FILL, DAILY_FINANCING and optional
DIVIDEND_ADJUSTMENT transactions. All broker trade IDs must match live
XAG Short ownership; transactions are unique and history is complete.
No orders or broker secrets/raw IDs in the response. The only DB write is
an isolated, non-authoritative cache of validated immutable transaction ranges.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import threading
import time
from urllib.parse import urlencode

from metals_xag_ledger_audit import _id, _utc, audit_links
from metals_xag_transaction_checkpoint import Checkpoint

VERSION = "xag_short_net_cash_ledger_checkpoint_v3_2026_11_26"
MAX_TX_PAGES = 60  # Per worker pass, never an unbounded broker request loop.
TX_RANGE_SIZE = 300
CACHE_SECONDS = 180  # Successful full-history proof is expensive (many broker pages).
FAIL_CACHE_SECONDS = 30  # Retry a first transient failure within 30 seconds.
MAX_FAIL_CACHE_SECONDS = 300  # Back off during an extended OANDA outage.
MAX_VERIFIED_PROOF_AGE_SECONDS = 480  # Never serve an older full-history result.
_cache = None
_export_events = None  # Private, timestamp-bound; excluded from existing public audit API.
_at = 0.0
_consecutive_failures = 0
_refreshing = False
_lock = threading.Lock()


def dec(value):
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _items(tx):
    closed = tx.get("tradesClosed") or []
    reduced = tx.get("tradeReduced")
    if isinstance(closed, dict):
        closed = [closed]
    if not isinstance(closed, list):
        return None
    if reduced is not None:
        if not isinstance(reduced, dict):
            return None
        closed = [*closed, reduced]
    return closed


def transaction_financials(transactions, links, broker_trades, now=None):
    """Return GBP net cash realised after complete trade + transaction proof.

    ORDER_FILL commission, GSLO fees, and daily financing are cash effects.
    Half-spread costs are already reflected in fill prices, not an extra charge.
    """
    now = now or datetime.now(timezone.utc)
    ids = {str(x["broker_trade_id"]) for x in links if x.get("broker_trade_id")}
    owned = {str(t["id"]):t for t in broker_trades if str(t.get("id") or "") in ids}
    issues = set()
    if len(ids)!=len(links) or len(owned)!=len(ids):
        issues.add("broker_trade_ownership_incomplete")
    seen_tx = set()
    # Retain only attributed, verified cash movements for the private export.
    # No trade IDs, account identifiers or raw OANDA payloads are exported.
    cash_events = []
    daily = defaultdict(lambda:Decimal("0"))
    components = defaultdict(lambda:Decimal("0"))
    event_count = defaultdict(int)
    net=Decimal("0")
    fill_pl=Decimal("0")
    fill_fin=Decimal("0")
    daily_fin=Decimal("0")
    fill_commission=Decimal("0")
    fill_gslo=Decimal("0")
    dividend=Decimal("0")
    for tx in transactions:
        if not isinstance(tx,dict):
            issues.add("invalid_transaction")
            continue
        tid=str(tx.get("id") or "").strip()
        if not tid or tid in seen_tx:
            issues.add("transaction_id_missing_or_repeated")
            continue
        seen_tx.add(tid)
        kind=str(tx.get("type") or "").upper()
        event_time=_utc(tx.get("time"))
        if event_time is None or event_time>now:
            issues.add("transaction_timestamp_invalid")
            continue
        amount=Decimal("0")
        if kind=="ORDER_FILL":
            items=_items(tx)
            if items is None:
                issues.add("fill_trade_items_invalid")
                continue
            opened=tx.get("tradeOpened")
            participants=[str(i.get("tradeID") or "") for i in items if isinstance(i,dict)]
            if opened is not None:
                if not isinstance(opened,dict):
                    issues.add("fill_trade_open_invalid")
                    continue
                participants.append(str(opened.get("tradeID") or ""))
            participates=bool(set(participants)&ids)
            is_xag=str(tx.get("instrument") or "").upper()=="XAG_USD"
            if is_xag and not participates:
                # Other activity on the same instrument might mean incomplete
                # account attribution even if no linked trade was closed.
                issues.add("xag_fill_unowned_trade")
                continue
            if not participates:
                continue
            if not is_xag or not participants or not set(participants)<=ids:
                issues.add("fill_mixed_or_missing_trade_ownership")
                continue
            if not all(isinstance(i,dict) and i.get("tradeID") for i in items):
                issues.add("fill_trade_items_invalid")
                continue
            broker_pl=dec(tx.get("pl"))
            broker_fin=dec(tx.get("financing", "0"))
            commission=dec(tx.get("commission", "0"))
            gslo=dec(tx.get("guaranteedExecutionFee", "0"))
            detail_pl=[dec(i.get("realizedPL")) for i in items]
            detail_fin=[dec(i.get("financing","0")) for i in items]
            if None in (broker_pl,broker_fin,commission,gslo) or any(v is None for v in detail_pl+detail_fin):
                issues.add("fill_money_invalid")
                continue
            if abs(broker_pl-sum(detail_pl,Decimal("0"))) > Decimal("0.015"):
                issues.add("fill_pl_not_equal_trade_reductions")
            if abs(broker_fin-sum(detail_fin,Decimal("0"))) > Decimal("0.015"):
                issues.add("fill_financing_not_equal_trade_reductions")
            if commission<0 or gslo<0:
                issues.add("fee_sign_invalid")
            # OANDA pl/financing are AccountUnits, positive fees reduce cash.
            amount=broker_pl+broker_fin-commission-gslo
            fill_pl+=broker_pl
            fill_fin+=broker_fin
            fill_commission+=commission
            fill_gslo+=gslo
            components["order_fills"]+=amount
            event_count["order_fills"]+=1

        elif kind=="DAILY_FINANCING":
            positions=tx.get("positionFinancings")
            if not isinstance(positions,list):
                issues.add("daily_financing_trade_breakdown_missing")
                continue
            for position in positions:
                if not isinstance(position,dict):
                    issues.add("daily_financing_position_invalid")
                    continue
                if str(position.get("instrument") or "").upper()!="XAG_USD":
                    continue
                subtotals=position.get("openTradeFinancings")
                if not isinstance(subtotals,list):
                    issues.add("xag_daily_financing_trade_breakdown_missing")
                    continue
                p_total=dec(position.get("financing"))
                child_total=Decimal("0")
                for row in subtotals:
                    if not isinstance(row,dict):
                        issues.add("xag_daily_financing_item_invalid")
                        continue
                    trade_id=str(row.get("tradeID") or "")
                    if trade_id not in ids:
                        issues.add("xag_daily_financing_unowned_trade")
                        continue
                    amt=dec(row.get("financing"))
                    if amt is None:
                        issues.add("xag_daily_financing_amount_invalid")
                        continue
                    child_total+=amt
                    amount+=amt
                if p_total is None or abs(p_total-child_total)>Decimal("0.015"):
                    issues.add("xag_daily_financing_not_balanced")
            if amount:
                daily_fin+=amount
                components["daily_financing"]+=amount
                event_count["daily_financing"]+=1

        elif kind=="DIVIDEND_ADJUSTMENT":
            if str(tx.get("instrument") or "").upper()!="XAG_USD":
                continue
            rows=tx.get("openTradeDividendAdjustments")
            if not isinstance(rows,list):
                issues.add("xag_dividend_trade_breakdown_missing")
                continue
            for row in rows:
                if not isinstance(row,dict):
                    issues.add("xag_dividend_row_invalid")
                    continue
                if str(row.get("tradeID") or "") not in ids:
                    issues.add("xag_dividend_unowned_trade")
                    continue
                value=dec(row.get("dividendAdjustment"))
                if value is None:
                    issues.add("xag_dividend_value_invalid")
                    continue
                amount+=value
            broker_total=dec(tx.get("dividendAdjustment"))
            if broker_total is None or abs(broker_total-amount)>Decimal("0.015"):
                issues.add("xag_dividend_not_balanced")
            dividend+=amount
            components["dividend"]+=amount
            event_count["dividend"]+=1
        else:
            # No other transaction type above is interpreted as realised GBP
            # without a documented trade-level cash attribution method.
            if (str(tx.get("instrument") or "").upper()=="XAG_USD"
                and kind not in ("STOP_LOSS_ORDER","TRAILING_STOP_LOSS_ORDER",
                                 "MARKET_ORDER","ORDER_CANCEL","ORDER_CANCEL_REJECT",
                                 "LIMIT_ORDER","TAKE_PROFIT_ORDER","STOP_LOSS_ORDER_REJECT",
                                 "TRADE_CLIENT_EXTENSIONS_MODIFY")):
                issues.add("unknown_XAG_transaction_kind_"+kind[:32])
            continue

        if amount:
            cash_events.append({"transaction_time_utc": event_time.isoformat(),
                                "transaction_type": kind, "net_realized_gbp": round(float(amount), 6)})
            net+=amount
            components["net_gbp"]+=amount
            # Transaction timestamp gives correct week/month for partial and
            # multi-close fills; never use the trade's final closeTime.
            if event_time.date()==now.date():
                daily["today"]+=amount
            if event_time.isocalendar()[:2]==now.isocalendar()[:2]:
                daily["week"]+=amount
            if event_time.year==now.year and event_time.month==now.month:
                daily["month"]+=amount

    # Independently reconcile OANDA's cumulative trade realizedPL with the
    # sum of *individual* ORDER_FILL closing events, not market-value estimates.
    cumulative=[]
    for t in owned.values():
        value=dec(t.get("realizedPL"))
        if value is None:
            issues.add("broker_trade_realised_pl_missing")
        else:
            cumulative.append(value)
    if len(cumulative)==len(ids) and abs(sum(cumulative,Decimal("0"))-fill_pl)>Decimal("0.025"):
        issues.add("order_fill_pl_mismatch_broker_trade_history")

    return {
        "ok":not issues,
        "transaction_count":len(seen_tx),
        "relevant_events":dict(event_count),
        "reconciliation_issues":sorted(issues),
        "cash_events": cash_events if not issues else [],
        "broker_trade_cumulative_pl_verified": "order_fill_pl_mismatch_broker_trade_history" not in issues and len(cumulative)==len(ids),
        "financial_components_gbp":{k:round(float(v),4) for k,v in components.items()},
        "realised_today_gbp":round(float(daily["today"]),4) if not issues else None,
        "realised_all_time_gbp":round(float(net),4) if not issues else None,
        "realised_week_gbp":round(float(daily["week"]),4) if not issues else None,
        "realised_month_gbp":round(float(daily["month"]),4) if not issues else None,
        "broker_fill_realised_pl_gbp":round(float(fill_pl),4),
        "broker_fill_financing_gbp":round(float(fill_fin),4),
        "broker_daily_financing_gbp":round(float(daily_fin),4),
        "broker_commission_gbp":round(float(fill_commission),4),
        "broker_guaranteed_stop_fee_gbp":round(float(fill_gslo),4),
        "broker_dividend_adjustment_gbp":round(float(dividend),4),
    }


class TransactionHistory:
    """A restart-safe, contiguous history checkpoint for one live account.

    OANDA transactions are immutable. Reuse only successfully read ranges
    beginning at the same first linked trade; a new account, changed boundary
    or rewound broker checkpoint discards the cache. Each refresh retains the
    original bounded request budget. No incomplete history is ever promoted.
    """

    # Keep only fields used by transaction_financials, not raw broker quotes,
    # account identifiers, or unrelated transaction metadata.
    _FIELDS = (
        "id", "type", "time", "instrument", "pl", "financing", "commission",
        "guaranteedExecutionFee", "tradesClosed", "tradeReduced", "tradeOpened",
        "positionFinancings", "openTradeDividendAdjustments", "dividendAdjustment",
    )

    def __init__(self):
        self.key = None
        self.next_id = None
        self.rows = []
        self.pages = 0
        self.pending = []

    def read(self, account, earliest, last, request, max_pages=None, checkpoint=None):
        max_pages = MAX_TX_PAGES if max_pages is None else max_pages
        key = (account, earliest)
        if self.key != key or self.next_id is None or last < self.next_id - 1:
            self.key = key
            self.next_id = earliest
            self.rows = []
            self.pages = 0
            self.pending = []
            # Only a fully validated contiguous prefix can survive restart.
            if checkpoint is not None:
                try:
                    loaded = checkpoint.load(account, earliest, last)
                    for start, end, rows in loaded:
                        self.rows.extend(rows)
                        self.next_id = end + 1
                        self.pages += 1
                    if loaded:
                        print(f"XAG_TRANSACTION_CHECKPOINT_LOADED pages={len(loaded)} "
                              f"transactions={len(self.rows)}", flush=True)
                except Exception as exc:
                    print(f"XAG_TRANSACTION_CHECKPOINT_LOAD_FAILED type={type(exc).__name__}", flush=True)
        pages_read = 0
        blocker = None
        while self.next_id <= last and pages_read < max_pages:
            start = self.next_id
            end = min(last, start + TX_RANGE_SIZE - 1)
            response = request(start, end)
            pages_read += 1
            if response.get("ok") is not True:
                blocker = "broker_transactions_range_read_failed"
                break
            items = (response.get("data") or {}).get("transactions")
            if not isinstance(items, list):
                blocker = "broker_transactions_range_invalid"
                break
            ids = [_id(t.get("id")) if isinstance(t, dict) else None for t in items]
            if (any(i is None or i < start or i > end for i in ids)
                    or len(ids) != len(set(ids))):
                blocker = "broker_transactions_range_invalid"
                break
            # The successful inclusive idrange response covers the entire
            # requested interval, even if there are gaps in broker IDs.
            # Advance only after validating and storing the entire page.
            compact = [{k: t[k] for k in self._FIELDS if k in t} for t in items]
            self.rows.extend(compact)
            if checkpoint is not None:
                self.pending.append((start, end, compact))
            self.next_id = end + 1
            self.pages += 1
        # Persist at most one bounded batch per refresh, outside broker calls.
        # An unavailable DB cannot invalidate fresh broker evidence; pending
        # pages are retried on subsequent refreshes. Never label unverified
        # broker history complete solely because the cache exists.
        if checkpoint is not None and self.pending:
            batch = self.pending[:MAX_TX_PAGES]
            try:
                checkpoint.save(account, earliest, batch)
                del self.pending[:len(batch)]
                print(f"XAG_TRANSACTION_CHECKPOINT_SAVED pages={len(batch)} "
                      f"pending={len(self.pending)}", flush=True)
            except Exception as exc:
                print(f"XAG_TRANSACTION_CHECKPOINT_SAVE_FAILED type={type(exc).__name__}", flush=True)
        complete = self.next_id > last
        if not complete and blocker is None:
            blocker = "transaction_history_backfill_pending"
        return {
            "transaction_history_complete": complete,
            "broker_transaction_range_pages": self.pages,
            "broker_transaction_pages_this_refresh": pages_read,
            "transaction_id_span": last - earliest + 1,
            "transaction_ids_covered": self.next_id - earliest,
            "transaction_ids_remaining": max(0, last + 1 - self.next_id),
            "blockers": [blocker] if blocker else [],
        }


_history = TransactionHistory()


def audit_live(core):
    import xag_short_live_override as xag
    account=xag._account_id(core)
    if not account:
        raise RuntimeError("OANDA live account ID missing")
    with core.get_conn() as conn:
        links=[dict(row) for row in conn.execute(
            "SELECT broker_trade_id, instrument, side, status, realized_pl, closed_at_utc "
            "FROM metals_xag_live_trade_links"
        ).fetchall()]
    broker_ids=[_id(row.get("broker_trade_id")) for row in links]
    if not broker_ids or any(v is None for v in broker_ids):
        raise RuntimeError("Invalid XAG live broker trade-link IDs")
    summary=xag._request(core,f"/v3/accounts/{account}/summary","GET")
    if summary.get("ok") is not True:
        raise RuntimeError("OANDA account summary unavailable")
    payload=summary.get("data") or {}
    currency=str((payload.get("account") or {}).get("currency") or "")
    last=_id(payload.get("lastTransactionID") or (payload.get("account") or {}).get("lastTransactionID"))
    if currency!="GBP" or last is None:
        raise RuntimeError("OANDA GBP currency or transaction checkpoint unverified")
    earliest=min(broker_ids)
    if earliest>last:
        raise RuntimeError("Invalid XAG broker transaction history boundary")

    # Exhaustively read official trades in pages; fail when truncated.
    trades=[];before=None;history_complete=False
    for pg in range(6):
        q=dict(state="ALL",instrument="XAG_USD",count=500)
        if before is not None:q["beforeID"]=str(before)
        resp=xag._request(core,f"/v3/accounts/{account}/trades?"+urlencode(q),"GET")
        if resp.get("ok") is not True:
            raise RuntimeError("OANDA XAG trade list failed")
        items=(resp.get("data") or {}).get("trades")
        if not isinstance(items,list):
            raise RuntimeError("OANDA XAG trades malformed")
        if not items:history_complete=True;break
        next_before=min(_id(t.get("id")) or 0 for t in items)
        if next_before<=0 or (before is not None and next_before>=before):
            raise RuntimeError("OANDA XAG trade history pagination invalid")
        trades.extend(items)
        if len(items)<500:history_complete=True;break
        before=next_before
    ownership=audit_links(links,trades,currency,history_complete,pg+1)
    if not ownership["trade_ownership_verified"]:
        return {"ok":False,"version":VERSION,"account_currency":currency,
                "trade_ownership_verified":False,
                "ownership_blockers":ownership.get("blockers"),
                "transaction_history_complete":False}

    # Fetch all transaction kinds in bounded inclusive ranges. Grow the
    # persistent checkpoint incrementally instead of imposing a lifetime
    # transaction-span ceiling or repeating the entire history every refresh.
    def request_range(start, end):
        return xag._request(
            core, f"/v3/accounts/{account}/transactions/idrange?"+
            urlencode({"from": start, "to": end}), "GET"
        )

    progress = _history.read(account, earliest, last, request_range,
                             checkpoint=Checkpoint(core.get_conn))
    if not progress["transaction_history_complete"]:
        return {
            "ok": False, "version": VERSION, "account_currency": currency,
            "trade_ownership_verified": True, "net_realised_gbp_verified": False,
            **progress,
        }
    amounts=transaction_financials(_history.rows,links,trades)
    return {
        "ok":bool(amounts["ok"]),
        "version":VERSION,
        "account_currency":currency,
        "trade_ownership_verified":True,
        **progress,
        "broker_trade_count":len(links),
        "broker_closed_xag_trades":ownership.get("broker_xag_short_closed"),
        "net_realised_gbp_verified":bool(amounts["ok"]),
        **amounts,
    }


def _refresh_snapshot(core):
    """Perform all expensive OANDA broker GETs outside the API request/lock."""
    global _cache, _at, _consecutive_failures, _refreshing, _export_events
    try:
        try:
            result = audit_live(core)
        except Exception as exc:
            result = {"ok": False, "version": VERSION,
                      "net_realised_gbp_verified": False,
                      "transaction_history_complete": False,
                      "reconciliation_issues": ["producer_unavailable"],
                      "error_type": type(exc).__name__}
        if not isinstance(result, dict):
            result = {"ok": False, "version": VERSION,
                      "error_type": "InvalidAuditResult"}
        result = dict(result)
        events = result.pop("cash_events", None)
        result["time_utc"] = datetime.now(timezone.utc).isoformat()
        with _lock:
            _export_events = (result["time_utc"], list(events)) if result.get("ok") is True and isinstance(events,list) else None
            if result.get("ok") is True:
                _consecutive_failures = 0
            else:
                # Completing one bounded backfill pass is normal progress,
                # not an outage. Retry on the existing first-failure cadence.
                if result.get("blockers") == ["transaction_history_backfill_pending"]:
                    _consecutive_failures = 1
                else:
                    _consecutive_failures += 1
                result["retry_delay_seconds"] = min(
                    MAX_FAIL_CACHE_SECONDS,
                    FAIL_CACHE_SECONDS * (2 ** min(10, max(0, _consecutive_failures - 1))))
            _cache = result
            _at = time.monotonic()
    finally:
        with _lock:
            _refreshing = False


def snapshot(core):
    """Nonblocking latest broker-verified cash proof, never a fabricated total.

    A cold audit can read up to 60 OANDA transaction pages and exceed the Hub's
    five-second request deadline. Refresh in one daemon worker while returning
    the last time-stamped proof for at most eight minutes; initial/cold state,
    expired proof, and failed audits stay unverified. Trading is unaffected.
    """
    global _refreshing
    now = time.monotonic()
    with _lock:
        cache = dict(_cache) if _cache is not None else None
        age = max(0, now - _at) if cache is not None else None
        retry_delay = min(
            MAX_FAIL_CACHE_SECONDS,
            FAIL_CACHE_SECONDS * (2 ** min(10, max(0, _consecutive_failures - 1))))
        refresh_due = (cache is None or age >=
                       (CACHE_SECONDS if cache.get("ok") is True else retry_delay))
        if refresh_due and not _refreshing:
            _refreshing = True
            threading.Thread(
                target=_refresh_snapshot, args=(core,),
                name="metals-xag-net-gbp-readonly-audit",
                daemon=True,
            ).start()
        in_progress = _refreshing
        if cache is not None and cache.get("ok") is True and age <= MAX_VERIFIED_PROOF_AGE_SECONDS:
            # The original timestamp is never replaced with now. Hub freshness
            # and XAG broker-cash scope verification remain mandatory.
            cache["audit_refresh_in_progress"] = in_progress
            cache["verified_proof_age_seconds"] = round(age, 2)
            return cache
        if cache is not None and cache.get("ok") is False and not refresh_due:
            cache["audit_refresh_in_progress"] = in_progress
            return cache
        return {
            "ok": False, "version": VERSION,
            "net_realised_gbp_verified": False,
            "transaction_history_complete": False,
            "error_type": "AUDIT_REFRESHING" if in_progress else "AUDIT_PROOF_EXPIRED",
            "audit_refresh_in_progress": in_progress,
            "last_verified_time_utc": (cache or {}).get("time_utc")
                                    if (cache or {}).get("ok") is True else None,
            "time_utc": datetime.now(timezone.utc).isoformat(),
        }



def export_verified_cash_events(core):
    """Return only the current auditor-certified event rows for download.

    The normal audit endpoints keep returning aggregates, with no transaction IDs.
    An expired/refreshing proof is NEVER treated as an empty XAG history.
    """
    import copy
    from metals_xag_realised_verified import verify
    proof = verify(snapshot(core))
    with _lock:
        pair = _export_events
        if pair is None or pair[0] != proof.get("time_utc"):
            raise ValueError("XAG verified cash detail is unavailable or stale")
        events = copy.deepcopy(pair[1])
    if abs(sum(float(r["net_realized_gbp"]) for r in events)
           - float(proof["realised_all_time_gbp"])) > 0.02:
        raise ValueError("XAG cash detail does not reconcile with verified all-time P&L")
    return {"proof": proof, "events": events}

