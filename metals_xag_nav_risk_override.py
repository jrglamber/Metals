"""Live XAG short entry capacity: NAV budget with broker-confirmed stops.

This is an entry-only overlay on the independent XAG executor. Existing trades,
MFE25, harvesting, XAU policy, one-unit sizing, cooldown and XAU confirmation
stay unchanged. Missing broker evidence blocks only fresh XAG entries.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

VERSION = "xag_nav_mark_to_stop_budget_v1_2026_11_23"
CAP_PCT_NAV = 0.85  # 24 existing one-unit £2-risk entries / ~£5758 verified NAV
OPERATIONAL_MAX_OPEN = 96
BUFFER = 1.10

def num(value):
    if value is None or isinstance(value, bool):
        raise ValueError("missing_numeric_evidence")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("nonfinite_numeric_evidence")
    return value

def fresh(value):
    if not value:
        raise ValueError("market_quote_time_missing")
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("market_quote_timezone_missing")
    age = (datetime.now(timezone.utc) - dt).total_seconds()
    if age < -30 or age > 180:
        raise ValueError("market_quote_stale")
    return True

def allowed(used, new_risk, nav):
    used, new_risk, nav = num(used), num(new_risk), num(nav)
    if used < 0 or new_risk <= 0 or nav <= 0:
        raise ValueError("invalid_risk_budget_values")
    return used + new_risk <= nav * CAP_PCT_NAV / 100.0 + 1e-9

def snapshot(core):
    import portfolio_live_risk as risk
    account, token = risk._credentials("metals")
    if not account or not token:
        raise ValueError("broker_credentials_unavailable")
    base = risk._api_base("metals")
    account_summary = risk._get_json(base, account, token, "summary").get("account") or {}
    if str(account_summary.get("currency") or "").upper() != "GBP":
        raise ValueError("live_account_currency_not_gbp")
    nav = num(account_summary.get("NAV"))
    if nav <= 0:
        raise ValueError("broker_nav_invalid")
    trades = risk._get_json(base, account, token, "openTrades").get("trades")
    if not isinstance(trades, list):
        raise ValueError("open_trades_invalid")
    xag = [t for t in trades if str(t.get("instrument") or "").upper() == "XAG_USD"]
    pending = risk._pending_stop_orders(base, account, token)
    prices = risk._get_json(base, account, token, "pricing",
                            {"instruments": "XAG_USD"}).get("prices") or []
    if len(prices) != 1:
        raise ValueError("xag_broker_quote_missing")
    price = prices[0]
    if str(price.get("status") or "").lower() != "tradeable":
        raise ValueError("xag_quote_not_tradeable")
    fresh(price.get("time"))
    bid = num(price.get("closeoutBid"))
    ask = num(price.get("closeoutAsk"))
    if bid <= 0 or ask < bid:
        raise ValueError("xag_bid_ask_invalid")
    factor = num(risk._factor(base, account, token, "USD", "GBP", {}))
    if factor <= 0:
        raise ValueError("usd_to_gbp_broker_conversion_invalid")
    current_risk = 0.0
    ids = []
    for trade in xag:
        tid = str(trade.get("id") or "").strip()
        units = num(trade.get("currentUnits"))
        if not tid or units >= 0:
            raise ValueError("xag_unexpected_broker_direction_or_id")
        stop = risk._verified_stop(trade, pending)
        if stop is None:
            raise ValueError("xag_broker_attached_stop_missing")
        stop = num(stop)
        if stop <= ask:
            raise ValueError("xag_stop_at_or_below_current_ask")
        current_risk += (stop-ask)*abs(units)*factor
        ids.append(tid)
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_xag_broker_trade_id")
    with core.get_conn() as conn:
        local = [str(dict(r).get("broker_trade_id") or "").strip() for r in conn.execute(
            "SELECT broker_trade_id FROM metals_xag_live_trade_links "
            "WHERE status IN ('OPEN','OPEN_UNVERIFIED_STOP','CLOSE_RETRY')"
        ).fetchall()]
        unresolved = int(dict(conn.execute(
            "SELECT COUNT(*) AS n FROM metals_xag_live_entry_intents "
            "WHERE status IN ('SUBMITTING','UNKNOWN_NEEDS_RECONCILIATION','OPEN_UNVERIFIED_STOP')"
        ).fetchone()).get("n") or 0)
    if unresolved:
        raise ValueError("xag_unresolved_broker_entry_intents")
    if len(local) != len(set(local)) or set(local) != set(ids):
        raise ValueError("xag_broker_local_reconciliation_mismatch")
    return {"nav":nav, "used":current_risk,
            "cap":nav*CAP_PCT_NAV/100.0, "open":len(ids),
            "bid":bid, "ask":ask, "fx":factor}

def candidate_risk(core, state):
    import xag_short_live_probe as sizing
    p = sizing.snapshot(core)
    if p.get("ok") is not True or p.get("blocking_reasons"):
        raise ValueError("xag_sizing_probe_unavailable")
    if str(p.get("price_status") or "").lower() != "tradeable":
        raise ValueError("xag_entry_quote_not_tradeable")
    fresh(p.get("price_time"))
    if str(p.get("oanda_account_currency") or "").upper() != "GBP":
        raise ValueError("xag_sizing_currency_not_gbp")
    units, minimum = num(p.get("recommended_units")), num(p.get("minimum_trade_size"))
    entry, stop = num(p.get("entry_price")), num(p.get("emergency_stop_price"))
    if units != 1.0 or minimum != 1.0:
        raise ValueError("xag_one_unit_pilot_sizing_changed")
    if entry <= 0 or stop <= entry or stop <= state["ask"]:
        raise ValueError("xag_candidate_stop_invalid")
    # If quote from the independent sizing probe differs, refuse a stale preflight.
    if abs(entry-state["bid"])/state["bid"] > 0.005:
        raise ValueError("xag_sizing_and_broker_quote_diverged")
    estimated = num(p.get("recommended_effective_risk_gbp"))
    if estimated <= 0:
        raise ValueError("xag_estimated_order_risk_invalid")
    # Do not let this preflight bypass the existing 25% pilot overage ceiling.
    target = num(p.get("target_risk_gbp"))
    if target <= 0 or estimated > target*1.25:
        raise ValueError("xag_pilot_risk_overage")
    return max(estimated, (stop-state["ask"])*units*state["fx"])*BUFFER

def install(core, app):
    import xag_short_live_override as live
    from metals_route_priority import promote_read_only_route
    if getattr(core, "_XAG_NAV_BUDGET_INSTALLED", False):
        raise RuntimeError("xag_nav_budget_double_install")
    if live.MAX_OPEN != 24:
        raise RuntimeError("unexpected_original_xag_open_cap")
    old_executor = live._execute_live

    def execute(core_arg: Any, raw_signal_id: int, approved: dict):
        # Called by the independent XAG router inside its existing entry mutex.
        # All regular entry quality, reconciling and idempotency checks remain.
        try:
            state = snapshot(core_arg)
            next_risk = candidate_risk(core_arg, state)
            detail = {"version":VERSION, "cap_pct_nav":CAP_PCT_NAV,
                      "nav_gbp":round(state["nav"],2),
                      "open_xag":state["open"],
                      "current_risk_gbp":round(state["used"],2),
                      "candidate_risk_buffered_gbp":round(next_risk,2),
                      "budget_gbp":round(state["cap"],2)}
            if not allowed(state["used"], next_risk, state["nav"]):
                try:
                    live._audit(core_arg, int(raw_signal_id), "entry_governance", "BLOCKED",
                                "xag_nav_risk_budget_exhausted", raw=detail)
                except Exception:
                    pass
                return {"ok":True, "blocked":True,
                        "reason":"xag_nav_risk_budget_exhausted",
                        "xag_nav_budget":detail}
            result = old_executor(core_arg, int(raw_signal_id), approved)
            if isinstance(result, dict):
                result = dict(result)
                result["xag_nav_budget"] = detail
            return result
        except Exception as exc:
            reason = "xag_nav_risk_budget_evidence_unavailable"
            try:
                live._audit(core_arg, int(raw_signal_id), "entry_governance", "BLOCKED",
                            reason, raw={"error": f"{type(exc).__name__}: {exc}"})
            except Exception:
                pass
            print("XAG_NAV_BUDGET_PREFLIGHT_BLOCK "
                  + f"{type(exc).__name__}:{exc}", flush=True)
            return {"ok":True, "blocked":True,
                    "reason":reason,
                    "version":VERSION, "fail_closed":True}

    def status():
        try:
            state = snapshot(core)
            return {"ok":True, "installed":True, "version":VERSION,
                    "cap_pct_nav":CAP_PCT_NAV, "nav_gbp":round(state["nav"],2),
                    "open_xag":state["open"],
                    "risk_used_gbp":round(state["used"],2),
                    "cap_gbp":round(state["cap"],2),
                    "free_gbp":round(max(0.0,state["cap"]-state["used"]),2),
                    "risk_basis":"current_ask_to_verified_broker_stop",
                    "max_positions_operational_only":OPERATIONAL_MAX_OPEN}
        except Exception as exc:
            return {"ok":False, "installed":True, "version":VERSION,
                    "new_entries_fail_closed":True,
                    "reason":f"{type(exc).__name__}:{exc}"}

    live._execute_live = execute
    live.MAX_OPEN = OPERATIONAL_MAX_OPEN
    core._XAG_NAV_BUDGET_INSTALLED = True
    core.METALS_XAG_NAV_BUDGET_VERSION = VERSION
    app.add_api_route("/api/xag-nav-budget", status, methods=["GET"],
                      include_in_schema=False)
    promote_read_only_route(app, "/api/xag-nav-budget", status)
    return {"installed":True, "version":VERSION,
            "cap_pct_nav":CAP_PCT_NAV,
            "entry_guard":"inside_serialized_xag_live_executor",
            "max_positions_operational_only":OPERATIONAL_MAX_OPEN,
            "old_24_trade_strategic_cap_replaced":True,
            "preserved":"MFE25, harvest, one-unit pilot, XAU 6h confirmation, cooldown",
            "xau_unchanged":True, "new_entries_fail_closed":True}
