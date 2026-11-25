"""Single read-only LIVE recently-closed feed: XAU long/short and XAG short.

Each row originates from a dedicated LIVE broker-trade link with an explicit
CLOSED* status, a broker trade ID and a recorded close timestamp. Values are
trade-ledger realised P&L, NOT a verified per-trade NET cash transaction
allocation. Historical cash/financing totals remain with their separate
broker-transaction accounting proofs. Never includes demo/research positions.
"""
from __future__ import annotations
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Dict, List, Optional

VERSION = "metals_live_recent_closed_both_assets_v1_2026_11_25"
TABLES = (
    ("XAUUSD", "metals_xau_live_trade_links"),
    ("XAGUSD", "metals_xag_live_trade_links"),
)

def _num(value: Any) -> Optional[float]:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        out = float(value)
        return out if isfinite(out) else None
    except (ValueError, TypeError, OverflowError):
        return None

def _first(row: Dict[str,Any], *keys: str):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None

def _safe_dt(value: Any):
    try:
        raw = str(value or "").replace("Z","+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None

def rows(core: Any, limit: int = 30) -> List[Dict[str,Any]]:
    lim = max(1, min(int(limit or 30), 100))
    found = []
    with core.get_conn() as conn:
        # Fixed internal table names; no caller-provided SQL identifiers.
        for asset, table in TABLES:
            raw = conn.execute(
                "SELECT * FROM " + table +
                " WHERE UPPER(COALESCE(status,'')) LIKE 'CLOSED%' "
                "AND COALESCE(CAST(broker_trade_id AS TEXT),'') <> '' "
                "AND COALESCE(CAST(closed_at_utc AS TEXT),'') <> '' "
                "ORDER BY closed_at_utc DESC, id DESC LIMIT ?",
                (lim,),
            ).fetchall()
            for item in raw:
                d = dict(item)
                closed = str(d.get("closed_at_utc") or "")
                when = _safe_dt(closed)
                if when is None:
                    continue
                risk = _num(_first(d,"estimated_risk_amount","requested_risk_amount"))
                pnl = _num(d.get("realized_pl"))
                found.append({
                    "asset":asset,
                    "side":str(d.get("side") or "").upper(),
                    "broker_trade_id":str(d.get("broker_trade_id") or ""),
                    "closed_at_utc":when.isoformat(),
                    "entry_price":_num(d.get("entry_price")),
                    "exit_price":_num(_first(d,"closed_price","close_price",
                                             "exit_price","last_known_price")),
                    "units":_num(_first(d,"filled_units","requested_units","units")),
                    "realized_pl":pnl,
                    "approx_r":(round(pnl/risk,4) if pnl is not None and risk is not None and risk > 0 else None),
                    "close_reason":str(d.get("close_reason") or d.get("status") or ""),
                    "close_status":str(d.get("status") or ""),
                    "pnl_source":"LIVE_BROKER_LINK_TRADE_REALIZED_NOT_NET_CASH",
                })
    found.sort(key=lambda d:d["closed_at_utc"],reverse=True)
    return found[:lim]

def snapshot(core: Any, limit: int = 30):
    data=rows(core,limit)
    return {
        "ok":True,"version":VERSION,
        "scope":"LIVE_XAU_LONG_SHORT_AND_XAG_SHORT",
        "rows":data,"count":len(data),
        "xau_count":sum(x["asset"]=="XAUUSD" for x in data),
        "xag_count":sum(x["asset"]=="XAGUSD" for x in data),
        "practice_excluded":True,"broker_order_writes":False,
        "trade_pl_not_net_broker_cash":True,
        "note":"Per-trade link P&L may exclude financing, fees and later broker corrections. "
               "Do not equate table sums with verified account cash.",
        "observed_at_utc":datetime.now(timezone.utc).isoformat(),
    }
