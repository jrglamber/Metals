"""Read-only XAU account-wide trade-direction cash audit; no strategy ownership assumption."""
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode
import threading
import time
from metals_xag_transaction_audit import dec, _items
from metals_xag_ledger_audit import _id, _utc

VERSION = "xau_account_direction_cash_v1"
_lock = threading.Lock()
_cache = None
_at = 0
_running = False

def reconcile(trades, txs, now=None):
    now = now or datetime.now(timezone.utc)
    zero = Decimal("0")
    tolerance = Decimal("0.015")
    owners = {}
    expected = {}
    observed = {}
    issues = set()
    totals = {s: {p: zero for p in ("week","month","all_time")} for s in ("long","short")}
    for t in trades:
        tid = str(t.get("id") or "")
        units = dec(t.get("initialUnits"))
        pl = dec(t.get("realizedPL"))
        if not tid or tid in owners or not units or pl is None or t.get("instrument") != "XAU_USD":
            issues.add("invalid_broker_trade")
            continue
        owners[tid] = "long" if units > 0 else "short"
        expected[tid] = pl
        observed[tid] = zero
    if not owners:
        issues.add("no_broker_trades")
    def add(tid, amount, when):
        if tid not in owners or amount is None:
            issues.add("unattributed_cash")
            return
        a = totals[owners[tid]]
        a["all_time"] += amount
        if when.isocalendar()[:2] == now.isocalendar()[:2]:
            a["week"] += amount
        if (when.year,when.month) == (now.year,now.month):
            a["month"] += amount
    seen = set()
    opened_ids = set()
    for tx in txs:
        tid = str(tx.get("id") or "")
        when = _utc(tx.get("time"))
        if not tid or tid in seen or when is None or when > now:
            issues.add("invalid_transaction_identity")
            continue
        seen.add(tid)
        kind = tx.get("type")
        instrument = tx.get("instrument")
        if kind == "ORDER_FILL" and instrument == "XAU_USD":
            items = _items(tx)
            opened = tx.get("tradeOpened")
            if items is None or any(not isinstance(i,dict) for i in items) or (opened is not None and not isinstance(opened,dict)):
                issues.add("invalid_fill_items")
                continue
            participants = [str(i.get("tradeID") or "") for i in items]
            if opened is not None:
                oid = str(opened.get("tradeID") or "")
                participants.append(oid)
                opened_ids.add(oid)
            if not participants or any(i not in owners for i in participants):
                issues.add("unowned_fill")
                continue
            pl, fin, commission, gslo = [dec(tx.get(k,"0")) for k in ("pl","financing","commission","guaranteedExecutionFee")]
            if None in (pl,fin,commission,gslo) or commission < 0 or gslo < 0:
                issues.add("invalid_fill_cash")
                continue
            p_sum = f_sum = zero
            for i in items:
                key = str(i.get("tradeID"))
                p = dec(i.get("realizedPL"))
                f = dec(i.get("financing","0"))
                if p is None or f is None:
                    issues.add("invalid_reduction_cash")
                    continue
                observed[key] += p
                p_sum += p
                f_sum += f
                add(key,p+f,when)
            if abs(pl-p_sum)>tolerance or abs(fin-f_sum)>tolerance:
                issues.add("fill_components_not_balanced")
            if commission or gslo:
                if len({owners[i] for i in participants}) != 1:
                    issues.add("mixed_direction_fee_unallocated")
                else:
                    add(participants[0],-commission-gslo,when)
        elif kind == "DAILY_FINANCING":
            positions = tx.get("positionFinancings")
            if not isinstance(positions,list):
                issues.add("missing_daily_financing_breakdown")
                continue
            for pos in positions:
                if pos.get("instrument") != "XAU_USD":
                    continue
                rows = pos.get("openTradeFinancings")
                p = dec(pos.get("financing"))
                if not isinstance(rows,list) or p is None:
                    issues.add("missing_xau_financing_breakdown")
                    continue
                subtotal = zero
                for row in rows:
                    f = dec(row.get("financing"))
                    if f is None:
                        issues.add("invalid_daily_financing")
                        continue
                    subtotal += f
                    add(str(row.get("tradeID") or ""),f,when)
                if abs(subtotal-p)>tolerance:
                    issues.add("daily_financing_not_balanced")
        elif instrument == "XAU_USD" and kind not in ("MARKET_ORDER","LIMIT_ORDER","STOP_ORDER","STOP_LOSS_ORDER","TAKE_PROFIT_ORDER","TRAILING_STOP_LOSS_ORDER","ORDER_CANCEL","ORDER_CANCEL_REJECT","TRADE_CLIENT_EXTENSIONS_MODIFY","MARKET_ORDER_REJECT","STOP_LOSS_ORDER_REJECT"):
            issues.add("unsupported_xau_transaction_type")
    if opened_ids != set(owners):
        issues.add("opening_fill_coverage_incomplete")
    if any(abs(observed[i]-expected[i])>tolerance for i in owners):
        issues.add("per_trade_realized_pl_mismatch")
    return {"ok":not issues,"scope":"OANDA_ACCOUNT_ALL_XAU_NOT_STRATEGY_OWNERSHIP",
            "issues":sorted(issues),"trade_counts":{s:sum(v==s for v in owners.values()) for s in totals},
            "directions":{s:{p:round(float(v),4) for p,v in a.items()} for s,a in totals.items()} if not issues else {}}

def audit(core):
    import xag_short_live_override as transport
    account=transport._account_id(core)
    def read(path):
        r=transport._request(core,"/v3/accounts/"+account+path,"GET")
        if r.get("ok") is not True:
            raise RuntimeError("broker_read_failed")
        return r.get("data") or {}
    a=read("/summary")
    last=_id(a.get("lastTransactionID") or a.get("account",{}).get("lastTransactionID"))
    if a.get("account",{}).get("currency")!="GBP" or not last:
        raise RuntimeError("GBP_checkpoint_unverified")
    trades=[]
    before=None
    for page in range(10):
        q={"state":"ALL","instrument":"XAU_USD","count":500}
        if before is not None:
            q["beforeID"]=before
        batch=read("/trades?"+urlencode(q)).get("trades")
        if not isinstance(batch,list):
            raise RuntimeError("invalid_trade_page")
        trades.extend(batch)
        if len(batch)<500:
            break
        nxt=min(_id(t.get("id")) or 0 for t in batch)
        if not nxt or (before is not None and nxt>=before):
            raise RuntimeError("invalid_pagination")
        before=nxt
    else:
        raise RuntimeError("trade_page_cap")
    if not trades:
        raise RuntimeError("no_xau_trades")
    # Full account transaction history, not the earliest local trade link.
    if last>60000:
        raise RuntimeError("transaction_range_cap")
    txs=[]
    for start in range(1,last+1,500):
        end=min(last,start+499)
        batch=read("/transactions/idrange?"+urlencode({"from":start,"to":end})).get("transactions")
        if not isinstance(batch,list) or sorted(_id(t.get("id")) or 0 for t in batch)!=list(range(start,end+1)):
            raise RuntimeError("transaction_range_incomplete")
        txs.extend(batch)
    end_summary=read("/summary")
    if _id(end_summary.get("lastTransactionID") or end_summary.get("account",{}).get("lastTransactionID"))!=last:
        raise RuntimeError("broker_checkpoint_changed")
    result=reconcile(trades,txs)
    result.update(version=VERSION,account_currency="GBP",transaction_history_complete=True,trade_history_complete=True)
    return result

def _refresh(core):
    global _cache,_at,_running
    try:
        r=audit(core)
    except Exception:
        r={"ok":False,"version":VERSION,"issues":["broker_history_proof_unavailable"]}
    with _lock:
        _cache=r
        _at=time.monotonic()
        _running=False

def snapshot(core):
    global _running
    with _lock:
        age=time.monotonic()-_at
        if not _running and (_cache is None or age>900):
            _running=True
            threading.Thread(target=_refresh,args=(core,),daemon=True).start()
        if _cache is not None and age<=1200:
            return dict(_cache,proof_age_seconds=round(age,1))
        return {"ok":False,"version":VERSION,"issues":["audit_refreshing"]}
