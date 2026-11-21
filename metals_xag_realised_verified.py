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


def apply_top(payload, proof):
    """Extend the existing XAU top-card data with separately proven XAG cash.

    XAU campaign protection, positions, broker NAV and the original XAU-only
    realised source remain untouched. Fail closed if a value is not present.
    """
    import copy
    if not isinstance(payload,dict) or payload.get("status")!="ok":
        return payload
    out=copy.deepcopy(payload)
    strat=out.get("strategy") or {}
    accounting=out.get("accounting") or {}
    if not isinstance(strat,dict) or not isinstance(accounting,dict):
        raise ValueError("Metals top realised schema missing")
    out["xau_only_accounting"]=copy.deepcopy(accounting)
    strat["xag_realised_verified"]=False
    strat["realised_scope"]="XAU_ONLY_XAG_LEDGER_PENDING"
    if not isinstance(proof,dict) or proof.get("ok") is not True:
        # The top cards advertise combined LIVE Metals. Do not show XAU-only
        # figures as the whole portfolio while the XAG cash proof is missing.
        # The untouched XAU baseline remains available separately for audit.
        out.setdefault("xau_only_strategy",copy.deepcopy(strat))
        strat["xau_only_realized_pnl"]=strat.get("realized_pnl")
        strat["realized_pnl"]=None
        for key in ("week_pnl","month_pnl","today_pnl","realized_today_pnl"):
            if key in accounting:
                accounting[key]=None
        strat["realised_scope"]="COMBINED_METALS_CASH_UNVERIFIED"
        strat["realised_reporting_status"]="XAG_BROKER_AUDIT_PENDING"
        return out
    fields=(
        ("realized_pnl","realised_all_time_gbp",strat),
        ("week_pnl","realised_week_gbp",accounting),
        ("month_pnl","realised_month_gbp",accounting),
    )
    projected={}
    for target,source,record in fields:
        base=finite(record.get(target))
        incremental=finite(proof.get(source))
        if base is None or incremental is None:
            raise ValueError("Metals realised top source missing " + target)
        projected[target]=round(base+incremental,4)
    strat["xau_only_realized_pnl"]=strat["realized_pnl"]
    strat["realized_pnl"]=projected["realized_pnl"]
    accounting["week_pnl"]=projected["week_pnl"]
    accounting["month_pnl"]=projected["month_pnl"]
    # Some renderer variants omit today's realised, so only modify if present.
    for today_key in ("today_pnl","realized_today_pnl"):
        if today_key in accounting:
            today_base=finite(accounting[today_key])
            if today_base is not None:
                accounting[today_key]=round(today_base+proof["realised_today_gbp"],4)
    strat["xag_realised_verified"]=True
    strat["realised_scope"]="XAU_LEGACY_PLUS_XAG_SHORT_NET_BROKER_VERIFIED"
    out["xag_short_verified_realised"]=dict(proof)
    return out


def apply_verified_xag_lane(report, proof):
    """Populate only independently broker-verified XAG SHORT cash fields.

    The full three-lane reconciliation remains false while XAU direction
    attribution is unverified. Never turn a missing proof into zero P&L.
    """
    import copy
    out = copy.deepcopy(report)
    if not isinstance(out, dict) or not isinstance(proof, dict) or proof.get("ok") is not True:
        raise ValueError("Verified XAG lane cash proof missing")
    lanes = out.get("live_lanes")
    if not isinstance(lanes, dict) or not isinstance(lanes.get("xag_short"), dict):
        raise ValueError("XAG SHORT reporting lane missing")
    lane = lanes["xag_short"]
    for field in ("realised_week_gbp", "realised_month_gbp", "realised_all_time_gbp"):
        amount = finite(proof.get(field))
        if amount is None:
            raise ValueError("Verified XAG cash field missing: " + field)
        lane[field] = amount
    lane["reporting_status"] = "broker_verified_realised_only"
    lane["unrealised_gbp"] = None  # Separate open-position proof required.
    out["reconciled"] = False
    return out
