"""Read-only XAG SHORT live sizing probe.

Uses the existing validated Metals live OANDA connection to inspect XAG_USD
instrument metadata and pricing. This module has no execution authority and
cannot place, modify, or close orders.
"""
from __future__ import annotations

import os
import urllib.parse
from typing import Any, Dict

VERSION = "xag_short_live_probe_v1_2026_10_31"
INSTRUMENT = "XAG_USD"
ASSET = "XAGUSD"
SIDE = "short"


def _num(core: Any, value: Any):
    try:
        return core.safe_float(value)
    except Exception:
        try:
            return float(value) if value is not None else None
        except Exception:
            return None


def snapshot(core: Any) -> Dict[str, Any]:
    target_risk = float(os.getenv("METALS_XAG_SHORT_LIVE_RISK_AMOUNT", str(getattr(core, "METALS_DEMO_XAG_RISK_AMOUNT", 2.0))))
    sl_pct = float(os.getenv("METALS_XAG_SHORT_LIVE_SL_PCT", str(getattr(core, "METALS_DEMO_XAG_SL_PCT", 5.24))))

    base: Dict[str, Any] = {
        "ok": False,
        "version": VERSION,
        "read_only": True,
        "execution_authority": False,
        "asset": ASSET,
        "instrument": INSTRUMENT,
        "side": SIDE,
        "target_risk_gbp": target_risk,
        "sl_pct": sl_pct,
        "blocking_reasons": [],
    }

    account_id = str(getattr(core, "METALS_XAU_LIVE_OANDA_ACCOUNT_ID", "") or "").strip()
    if not account_id:
        base["blocking_reasons"].append("live_account_id_missing")
        return base

    req = getattr(core, "_metals_xau_live_request", None)
    conversion_fn = getattr(core, "_metals_home_conversion", None)
    if not callable(req) or not callable(conversion_fn):
        base["blocking_reasons"].append("live_read_helpers_unavailable")
        return base

    summary = req(f"/v3/accounts/{account_id}/summary", "GET")
    acct = ((summary.get("data") or {}).get("account") or {}) if summary.get("ok") else {}
    currency = str(acct.get("currency") or "").upper()
    base["oanda_account_currency"] = currency
    if currency != "GBP":
        base["blocking_reasons"].append(f"live_account_currency_not_gbp:{currency or 'UNKNOWN'}")

    meta_resp = req(
        f"/v3/accounts/{account_id}/instruments?instruments={urllib.parse.quote(INSTRUMENT)}",
        "GET",
    )
    price_resp = req(
        f"/v3/accounts/{account_id}/pricing?instruments={urllib.parse.quote(INSTRUMENT)}&includeHomeConversions=true",
        "GET",
    )
    metas = (meta_resp.get("data") or {}).get("instruments", []) if meta_resp.get("ok") else []
    pricing = price_resp.get("data") or {} if price_resp.get("ok") else {}
    prices = pricing.get("prices", []) or []
    conversion = conversion_fn(pricing, "USD") if pricing else {"ok": False}

    if not metas:
        base["blocking_reasons"].append("xag_live_instrument_metadata_unavailable")
    if not prices:
        base["blocking_reasons"].append("xag_live_price_unavailable")
    if not conversion.get("ok"):
        base["blocking_reasons"].append("usd_to_gbp_home_conversion_unavailable")
    if base["blocking_reasons"]:
        base["instrument_response_ok"] = bool(meta_resp.get("ok"))
        base["pricing_response_ok"] = bool(price_resp.get("ok"))
        return base

    meta, price = metas[0], prices[0]
    bid = _num(core, (price.get("bids") or [{}])[0].get("price"))
    ask = _num(core, (price.get("asks") or [{}])[0].get("price"))
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        base["blocking_reasons"].append("invalid_xag_bid_ask")
        return base

    # SHORT enters at bid; emergency stop must be above entry.
    entry = float(bid)
    stop = entry * (1.0 + sl_pct / 100.0)
    stop_usd_per_unit = stop - entry
    usd_to_gbp = float(conversion["account_loss_factor"])
    stop_gbp_per_unit = stop_usd_per_unit * usd_to_gbp

    min_units = float(_num(core, meta.get("minimumTradeSize")) or 1.0)
    precision = int(_num(core, meta.get("tradeUnitsPrecision")) or 0)
    step = 10 ** (-precision)
    raw_units = target_risk / stop_gbp_per_unit if stop_gbp_per_unit > 0 else 0.0
    floored_units = int(raw_units / step) * step if step > 0 else raw_units
    recommended_units = max(min_units, floored_units)
    min_risk = min_units * stop_gbp_per_unit
    recommended_risk = recommended_units * stop_gbp_per_unit
    overage_pct = ((recommended_risk - target_risk) / target_risk * 100.0) if target_risk else None
    mid = (float(bid) + float(ask)) / 2.0
    spread_pct = ((float(ask) - float(bid)) / mid * 100.0) if mid else None
    margin_rate = _num(core, meta.get("marginRate"))
    position_value_factor = _num(core, conversion.get("position_value_factor")) or usd_to_gbp
    estimated_margin_gbp = recommended_units * entry * (margin_rate or 0.0) * position_value_factor

    base.update({
        "ok": True,
        "price_status": str(price.get("status") or ""),
        "price_time": str(price.get("time") or ""),
        "bid": bid,
        "ask": ask,
        "spread_pct": spread_pct,
        "entry_price": entry,
        "emergency_stop_price": stop,
        "emergency_stop_side_safe": bool(stop > entry),
        "minimum_trade_size": min_units,
        "trade_units_precision": precision,
        "unit_step": step,
        "raw_target_units": raw_units,
        "minimum_size_required": bool(raw_units < min_units),
        "minimum_size_effective_risk_gbp": min_risk,
        "recommended_units": recommended_units,
        "signed_order_units_if_armed": -abs(recommended_units),
        "recommended_effective_risk_gbp": recommended_risk,
        "risk_overage_pct": overage_pct,
        "stop_distance_usd_per_unit": stop_usd_per_unit,
        "stop_distance_gbp_per_unit": stop_gbp_per_unit,
        "usd_to_gbp_account_loss_factor": usd_to_gbp,
        "margin_rate": margin_rate,
        "estimated_margin_gbp": estimated_margin_gbp,
        "maximum_order_units": _num(core, meta.get("maximumOrderUnits")),
        "display_precision": int(_num(core, meta.get("displayPrecision")) or 3),
        "safe_for_execution_review": bool(currency == "GBP" and stop > entry and recommended_units >= min_units),
        "note": "Read-only sizing evidence. No order is placed by this endpoint.",
    })
    return base


def install(core: Any, app: Any) -> Dict[str, Any]:
    @app.get("/api/xag-short-live-sizing-probe", include_in_schema=False)
    def xag_short_live_sizing_probe() -> Dict[str, Any]:
        return snapshot(core)

    return {
        "installed": True,
        "version": VERSION,
        "read_only": True,
        "execution_authority": False,
        "route": "/api/xag-short-live-sizing-probe",
    }
