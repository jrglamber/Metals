"""XAG Short net-GBP broker transaction attribution (read-only).

Uses OANDA's account-currency ORDER_FILL, DAILY_FINANCING and optional
DIVIDEND_ADJUSTMENT transactions. All broker trade IDs must match live
XAG Short ownership; transactions are unique and history is complete.
No orders, DB writes, broker secrets, or raw IDs in the response.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import threading
import time
from urllib.parse import urlencode

from metals_xag_ledger_audit import _id, _utc, audit_links

VERSION = "xag_short_net_cash_ledger_shadow_v1_2026_11_17"
MAX_TX_PAGES = 60
TX_RANGE_SIZE = 300
CACHE_SECONDS = 180  # Successful full-history proof is expensive (many broker pages).
FAIL_CACHE_SECONDS = 30  # Retry a first transient failure within 30 seconds.
MAX_FAIL_CACHE_SECONDS = 300  # Back off during an extended OANDA outage.
MAX_VERIFIED_PROOF_AGE_SECONDS = 480  # Never serve an older full-history result.
_cache = None
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

    # OANDA idrange is inclusive. Deliberately fetch all transaction types so
    # costs are neither overlooked nor manufactured from a filtered subset.
    transactions=[]; pages=0
    end_limit=earliest+TX_RANGE_SIZE*MAX_TX_PAGES-1
    if last>end_limit:
        return {"ok":False,"version":VERSION,
                "trade_ownership_verified":True,"transaction_history_complete":False,
                "blockers":["transaction_range_safety_cap_exceeded"],
                "transaction_id_span":last-earliest+1}
    for start in range(earliest,last+1,TX_RANGE_SIZE):
        end=min(last,start+TX_RANGE_SIZE-1)
        resp=xag._request(
            core,f"/v3/accounts/{account}/transactions/idrange?"+urlencode({"from":start,"to":end}),"GET"
        )
        if resp.get("ok") is not True:
            return {"ok":False,"version":VERSION,"transaction_history_complete":False,
                    "trade_ownership_verified":True,
                    "blockers":["broker_transactions_range_read_failed"]}
        items=(resp.get("data") or {}).get("transactions")
        if not isinstance(items,list):
            raise RuntimeError("OANDA transaction range payload malformed")
        transactions.extend(items)
        pages+=1
    amounts=transaction_financials(transactions,links,trades)
    return {
        "ok":bool(amounts["ok"]),
        "version":VERSION,
        "account_currency":currency,
        "trade_ownership_verified":True,
        "transaction_history_complete":True,
        "broker_transaction_range_pages":pages,
        "transaction_id_span":last-earliest+1,
        "broker_trade_count":len(links),
        "broker_closed_xag_trades":ownership.get("broker_xag_short_closed"),
        "net_realised_gbp_verified":bool(amounts["ok"]),
        **amounts,
    }


def _refresh_snapshot(core):
    """Perform all expensive OANDA broker GETs outside the API request/lock."""
    global _cache, _at, _consecutive_failures, _refreshing
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
        result["time_utc"] = datetime.now(timezone.utc).isoformat()
        with _lock:
            if result.get("ok") is True:
                _consecutive_failures = 0
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

    The full audit can read 33+ OANDA transaction pages and exceed the Hub's
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
