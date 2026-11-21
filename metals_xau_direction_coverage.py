"""Read-only local XAU direction coverage probe; never asserts broker reconciliation."""
from metals_live_lane_accounting import _broker_trade_id, _lane

def diagnose_xau_coverage(transactions, links):
    ownership = {}
    conflicts = set()
    for row in links:
        lane = _lane(row)
        if lane not in ("xau_long", "xau_short"):
            continue
        tid = row.get("broker_trade_id") or row.get("oanda_trade_id") or row.get("trade_id")
        if not tid:
            continue
        tid = str(tid)
        if tid in ownership and ownership[tid] != lane:
            conflicts.add(tid)
        ownership[tid] = lane
    counts = {"xau_long": 0, "xau_short": 0, "unlinked": 0, "ambiguous": 0, "duplicates": 0, "missing_transaction_id": 0, "mismatch": 0}
    seen = set()
    examined = 0
    for tx in transactions:
        if str(tx.get("instrument") or "").upper().replace("_", "").replace("/", "") != "XAUUSD":
            continue
        examined += 1
        txid = tx.get("transaction_id") or tx.get("broker_transaction_id") or tx.get("id")
        if not txid:
            counts["missing_transaction_id"] += 1
            continue
        if str(txid) in seen:
            counts["duplicates"] += 1
            continue
        seen.add(str(txid))
        tid = _broker_trade_id(tx)
        if tid == "__AMBIGUOUS__" or str(tid) in conflicts:
            counts["ambiguous"] += 1
            continue
        lane = ownership.get(str(tid))
        if lane is None:
            counts["unlinked"] += 1
        elif _lane(tx) not in (None, lane):
            counts["mismatch"] += 1
        else:
            counts[lane] += 1
    return {
        "scope": "LOCAL_XAU_BROKER_SYNC_EPOCH_ONLY",
        "status": "awaiting_broker_history_reconciliation",
        "authoritative_broker_history_verified": False,
        "per_side_net_gbp_verified": False,
        "historical_financing_verified": False,
        "transaction_rows_examined": examined,
        "linked_trade_ids": len(ownership),
        "conflicting_trade_ids": len(conflicts),
        "transaction_row_counts": counts,
        "blockers": ["complete_oanda_xau_history", "multi_trade_fill_and_financing_attribution", "gbp_net_cash_reconciliation"],
    }
