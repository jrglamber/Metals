"""Strict adapter for broker-verified XAG net realised GBP promotion.

Does not recalculate amounts from trade prices or local P&L.
Only accepts fully attributed OANDA cash events from the read-only ledger.
"""
from datetime import datetime,timezone
import math

FIELDS=("realised_today_gbp","realised_week_gbp",
        "realised_month_gbp","realised_all_time_gbp")


def finite(value):
    if value is None or isinstance(value,bool):
        return None
    try:
        f=float(value)
        return f if math.isfinite(f) else None
    except (TypeError,ValueError):
        return None


def verify(audit,now=None,max_age_seconds=900):
    now=now or datetime.now(timezone.utc)
    if not isinstance(audit,dict) or not all(audit.get(k) is True for k in (
            "ok","trade_ownership_verified","transaction_history_complete",
            "net_realised_gbp_verified","broker_trade_cumulative_pl_verified")):
        raise ValueError("XAG transaction ledger not fully broker verified")
    if audit.get("account_currency")!="GBP":
        raise ValueError("XAG broker account not GBP")
    if audit.get("reconciliation_issues") or audit.get("blockers"):
        raise ValueError("XAG broker net cash audit has unresolved exceptions")
    if (finite(audit.get("broker_trade_count")) is None
            or finite(audit.get("transaction_count")) is None
            or int(audit["broker_trade_count"])<1 or int(audit["transaction_count"])<1):
        raise ValueError("XAG broker history missing coverage")
    try:
        stamp=datetime.fromisoformat(str(audit["time_utc"]).replace("Z","+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Audit timezone missing")
        age=(now-stamp.astimezone(timezone.utc)).total_seconds()
    except (TypeError,ValueError,KeyError) as exc:
        raise ValueError("XAG audit timestamp invalid") from exc
    if age<0 or age>max_age_seconds:
        raise ValueError("XAG audited broker values stale or future")
    fields={key:finite(audit.get(key)) for key in FIELDS}
    if any(v is None for v in fields.values()):
        raise ValueError("XAG realised net GBP contains invalid amount")
    components=audit.get("financial_components_gbp") or {}
    if not isinstance(components,dict):
        raise ValueError("XAG broker cash components missing")
    net=finite(components.get("net_gbp"))
    fill=finite(components.get("order_fills"))
    daily=finite(components.get("daily_financing",0))
    dividend=finite(components.get("dividend",0))
    if None in (net,fill,daily,dividend):
        raise ValueError("XAG net GBP financial components unavailable")
    if abs(net-(fill+daily+dividend))>0.03:
        raise ValueError("XAG broker net amount does not reconcile to components")
    if abs(net-fields["realised_all_time_gbp"])>0.03:
        raise ValueError("XAG broker transaction totals do not reconcile")
    return {
        "ok":True,"scope":"XAG_SHORT_BROKER_NET_CASH_GBP",
        "time_utc":audit["time_utc"],
        "source_version":str(audit.get("version") or ""),
        "broker_trade_count":int(audit["broker_trade_count"]),
        "broker_closed_trade_count":int(audit.get("broker_closed_xag_trades") or 0),
        "broker_transaction_count":int(audit["transaction_count"]),
        **fields,
    }
