"""Read-only broker-ledger attribution for live Metals lanes.

Never infer a trade direction from the sign of P&L. Unknown ownership remains
unattributed rather than silently allocating money to a lane.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone

LANES = ('xau_long', 'xau_short', 'xag_short')


def _side(value):
    value = str(value or '').strip().lower()
    return 'long' if value in ('long', 'buy', '1') else ('short' if value in ('short', 'sell', '-1') else None)


def _lane(record):
    explicit = str(record.get('lane') or record.get('strategy_lane') or '').lower().replace('-', '_')
    if explicit in LANES:
        return explicit
    instrument = str(record.get('instrument') or record.get('symbol') or '').upper().replace('/', '').replace('_', '')
    side = _side(record.get('side') or record.get('direction'))
    if instrument in ('XAUUSD', 'XAU') and side:
        return 'xau_' + side
    if instrument in ('XAGUSD', 'XAG') and side == 'short':
        return 'xag_short'
    return None


def _money(value):
    try:
        return float(value) if value is not None and value != '' else None
    except (ValueError, TypeError):
        return None


def _date(value):
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)



def _broker_trade_id(tx):
    """Extract a single unambiguous OANDA trade ID from a broker transaction.

    A fill may close several trades; its aggregate net P&L must not be
    allocated to any one trade unless separately itemised by the broker.
    """
    direct = tx.get('broker_trade_id') or tx.get('trade_id') or tx.get('oanda_trade_id')
    raw = tx.get('raw_json')
    try:
        raw = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        raw = None
    ids = set()
    if direct is not None and str(direct).strip():
        ids.add(str(direct).strip())
    if isinstance(raw, dict):
        for field in ('tradeID', 'tradeId'):
            if raw.get(field) is not None:
                ids.add(str(raw[field]).strip())
        for field in ('tradesClosed', 'tradeReduced', 'tradeOpened'):
            records = raw.get(field) or []
            if isinstance(records, dict): records = [records]
            if not isinstance(records, list): return None
            for item in records:
                if isinstance(item, dict):
                    value = item.get('tradeID') or item.get('tradeId')
                    if value is not None: ids.add(str(value).strip())
    return next(iter(ids)) if len(ids) == 1 else ('__AMBIGUOUS__' if len(ids) > 1 else None)

def live_lane_financials(transactions, trade_links, broker_open_trades=(), now=None, coverage_verified=False):
    """Only attribute uniquely linked broker rows; report incomplete coverage.

    Transactions must be net GBP values from the broker ledger. Trade-link rows
    establish instrument and direction, including for partial closes. Never
    count the same broker transaction ID twice.
    """
    now = now or datetime.now(timezone.utc)
    links = {}
    conflicting_trade_ids = set()
    for link in trade_links:
        lane = _lane(link)
        if lane not in LANES:
            continue
        for key in ('broker_trade_id', 'oanda_trade_id', 'trade_id'):
            trade_id = link.get(key)
            if trade_id is not None and str(trade_id).strip():
                key = str(trade_id)
                if key in links and links[key] != lane:
                    conflicting_trade_ids.add(key)
                links[key] = lane
    totals = {lane: dict(realised_week_gbp=0., realised_month_gbp=0.,
                         realised_all_time_gbp=0., unrealised_gbp=0., open_trades=0)
              for lane in LANES}
    seen = set()
    unattributed = []
    duplicate = []
    for tx in transactions:
        tx_id = tx.get('transaction_id') or tx.get('broker_transaction_id') or tx.get('id')
        if tx_id is None:
            unattributed.append('transaction_missing_id')
            continue
        tx_id = str(tx_id)
        if tx_id in seen:
            duplicate.append(tx_id)
            continue
        seen.add(tx_id)
        trade_id = _broker_trade_id(tx)
        if trade_id == '__AMBIGUOUS__' or (trade_id is not None and str(trade_id) in conflicting_trade_ids):
            unattributed.append(tx_id)
            continue
        lane = links.get(str(trade_id)) if trade_id is not None else None
        # A broker-linked trade cannot be reassigned using an untrusted
        # transaction-level label if the two sources disagree.
        explicit_lane = _lane(tx)
        if lane and explicit_lane and lane != explicit_lane:
            unattributed.append(tx_id)
            continue
        lane = lane or explicit_lane
        amount = _money(tx.get('net_realized_gbp'))
        dt = _date(tx.get('transaction_time') or tx.get('time'))
        if lane not in LANES or amount is None or dt is None:
            unattributed.append(tx_id)
            continue
        totals[lane]['realised_all_time_gbp'] += amount
        if dt.year == now.year and dt.month == now.month:
            totals[lane]['realised_month_gbp'] += amount
        if dt.isocalendar()[:2] == now.isocalendar()[:2]:
            totals[lane]['realised_week_gbp'] += amount
    open_seen = set()
    for trade in broker_open_trades:
        trade_id = trade.get('id') or trade.get('broker_trade_id')
        if trade_id is None or not str(trade_id).strip() or str(trade_id) in open_seen:
            unattributed.append('open_duplicate_or_missing_id:' + str(trade_id))
            continue
        open_seen.add(str(trade_id))
        if str(trade_id) in conflicting_trade_ids:
            unattributed.append('open_conflicting_id:' + str(trade_id))
            continue
        lane = links.get(str(trade_id))
        explicit_lane = _lane(trade)
        if lane and explicit_lane and lane != explicit_lane:
            unattributed.append('open_lane_mismatch:' + str(trade_id))
            continue
        lane = lane or explicit_lane
        upl = _money(trade.get('unrealizedPL') if 'unrealizedPL' in trade else trade.get('unrealised_gbp'))
        if lane not in LANES or upl is None:
            unattributed.append('open:' + str(trade_id))
            continue
        totals[lane]['unrealised_gbp'] += upl
        totals[lane]['open_trades'] += 1
    # An empty or partial ledger is not evidence of complete broker coverage.
    # Only a separate authoritative broker reconciliation can assert this flag.
    complete = bool(coverage_verified) and not unattributed and not duplicate and not conflicting_trade_ids
    for lane, row in totals.items():
        row['reporting_status'] = 'available' if complete else 'awaiting_reconciliation'
        if not complete:
            for key in ('realised_week_gbp', 'realised_month_gbp', 'realised_all_time_gbp', 'unrealised_gbp'):
                row[key] = None
    return {'live_lanes': totals, 'reconciled': complete,
            'unattributed_count': len(unattributed), 'duplicate_count': len(duplicate), 'conflicting_trade_id_count': len(conflicting_trade_ids),
            'transaction_count': len(seen), 'coverage_verified': bool(coverage_verified), 'time_utc': now.isoformat()}
