"""XAU live entries: broker-verified NAV budget; existing exits and XAG untouched."""
from __future__ import annotations
import math
import threading
from datetime import datetime, timezone

VERSION = "xau_nav_mark_to_stop_budget_v1_2026_11_23"
CAP_PCT_NAV = 2.40  # existing nominal 24 entries x approved 0.10% NAV per entry
SLIPPAGE_BUFFER = 1.10
_LOCK = threading.RLock()

def number(value):
    v = float(value)
    if not math.isfinite(v):
        raise ValueError("nonfinite_value")
    return v

def admission(used, new, nav):
    used, new, nav = map(number, (used, new, nav))
    if used < 0 or new <= 0 or nav <= 0:
        raise ValueError("invalid_risk_values")
    return used + new <= nav * CAP_PCT_NAV / 100 + 1e-9

def broker_snapshot(core):
    import portfolio_live_risk as risk
    account, token = risk._credentials("metals")
    if not account or not token:
        raise ValueError("live_broker_credentials_missing")
    base = risk._api_base("metals")
    summary = risk._get_json(base, account, token, "summary").get("account") or {}
    if str(summary.get("currency") or "").upper() != "GBP":
        raise ValueError("broker_account_must_be_gbp")
    nav = number(summary.get("NAV"))
    if nav <= 0:
        raise ValueError("broker_nav_unavailable")
    all_trades = risk._get_json(base, account, token, "openTrades").get("trades")
    if not isinstance(all_trades, list):
        raise ValueError("open_trades_unavailable")
    xau_trades = [t for t in all_trades if str(t.get("instrument") or "").upper() == "XAU_USD"]
    pending = risk._pending_stop_orders(base, account, token)
    prices = risk._get_json(base, account, token, "pricing",
                            {"instruments": "XAU_USD"}).get("prices") or []
    if len(prices) != 1:
        raise ValueError("xau_pricing_missing")
    quote = prices[0]
    bid, ask = number(quote.get("closeoutBid")), number(quote.get("closeoutAsk"))
    if bid <= 0 or ask < bid:
        raise ValueError("xau_quote_invalid")
    qt = quote.get("time")
    if not qt:
        raise ValueError("xau_quote_time_missing")
    stamp = datetime.fromisoformat(str(qt).replace("Z", "+00:00"))
    if stamp.tzinfo is None or abs((datetime.now(timezone.utc)-stamp).total_seconds()) > 180:
        raise ValueError("xau_quote_stale")
    fx = number(risk._factor(base, account, token, "USD", "GBP", {}))
    if fx <= 0:
        raise ValueError("usd_gbp_conversion_missing")
    ids, total = [], 0.0
    for trade in xau_trades:
        tid = str(trade.get("id") or "")
        units = number(trade.get("currentUnits"))
        stop = risk._verified_stop(trade, pending)
        if not tid or units == 0 or stop is None:
            raise ValueError("open_xau_missing_verified_broker_stop")
        stop = number(stop)
        if (units > 0 and stop >= bid) or (units < 0 and stop <= ask):
            raise ValueError("open_xau_stop_already_crossed")
        risk_usd = ((bid-stop) if units > 0 else (stop-ask)) * abs(units)
        total += risk_usd * fx
        ids.append(tid)
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_broker_xau_trades")
    with core.get_conn() as conn:
        local = [str(dict(r).get("broker_trade_id") or "") for r in conn.execute(
            "SELECT broker_trade_id FROM metals_xau_live_trade_links WHERE status='OPEN'"
        ).fetchall()]
    if len(local) != len(set(local)) or set(local) != set(ids):
        raise ValueError("live_xau_broker_local_links_disagree")
    return {"nav":nav, "used":total, "cap":nav*CAP_PCT_NAV/100,
            "open":len(ids), "bid":bid, "ask":ask, "fx":fx}

def next_order_risk(core, side, state):
    preview = dict(core._metals_xau_side_sizing_preview(side) or {})
    if preview.get("executable") is not True or preview.get("blocking_reasons"):
        raise ValueError("xau_sizing_not_executable")
    units, minimum = number(preview.get("units")), number(preview.get("minimum_trade_size"))
    entry, stop = number(preview.get("entry_price")), number(preview.get("stop_price"))
    units = abs(units)
    if units <= 0 or minimum <= 0 or units + 1e-12 < minimum or entry <= 0:
        raise ValueError("new_xau_broker_minimum_or_entry_invalid")
    if side == "long" and stop < entry:
        gap = state["bid"]-stop
    elif side == "short" and stop > entry:
        gap = stop-state["ask"]
    else:
        raise ValueError("new_xau_stop_wrong_side")
    if gap <= 0:
        raise ValueError("new_xau_stop_already_crossed")
    return gap*units*state["fx"]*SLIPPAGE_BUFFER

def install(core, app):
    import metals_adaptive_stacking_override as adaptive
    import metals_adaptive_minimum_size_override as overflow
    from metals_route_priority import promote_read_only_route
    if getattr(core, "_XAU_NAV_BUDGET_INSTALLED", False):
        raise RuntimeError("nav_budget_already_installed")
    if str(getattr(core, "METALS_XAU_ADAPTIVE_STACKING_VERSION", "")) != adaptive.VERSION:
        raise RuntimeError("unexpected_adaptive_stacking_version")
    if not getattr(core, "_METALS_XAU_BROKER_SAFE_OVERFLOW_INSTALLED", False):
        raise RuntimeError("broker_minimum_protection_not_installed")
    if (adaptive.NORMAL_ALLOWANCE, adaptive.FULL_SIZE_THROUGH_ORDINAL,
            overflow.FULL_SIZE_THROUGH) != (20, 32, 32):
        raise RuntimeError("unexpected_prior_stacking_settings")
    old_router = core.execute_metals_demo_candidate
    if not callable(old_router) or not callable(getattr(core, "_metals_xau_side_sizing_preview", None)):
        raise RuntimeError("xau_live_router_or_sizing_missing")

    def status():
        try:
            state = broker_snapshot(core)
            # Read-only preview: failure must not hide a verified existing budget.
            next_risks = {}
            for side in ("long", "short"):
                try:
                    next_risks[side] = round(next_order_risk(core, side, state), 2)
                except Exception:
                    next_risks[side] = None
            return {"ok":True, "version":VERSION, "active":True,
                    "next_trade_buffered_risk_gbp":next_risks,
                    "risk_basis":"current_liquidation_quote_to_broker_stop",
                    "cap_pct_nav":CAP_PCT_NAV, "nav_gbp":round(state["nav"],2),
                    "open_xau":state["open"], "risk_used_gbp":round(state["used"],2),
                    "cap_gbp":round(state["cap"],2),
                    "free_gbp":round(max(0,state["cap"]-state["used"]),2)}
        except Exception as exc:
            return {"ok":False, "active":True, "version":VERSION,
                    "new_entries_fail_closed":True,
                    "reason":f"{type(exc).__name__}:{exc}"}

    def router(raw_signal_id, source="signal_worker"):
        with _LOCK:
            try:
                c = adaptive._candidate(core, int(raw_signal_id))
                # A failed lookup cannot bypass the live XAU risk gate.
                if not c.get("ok"):
                    raise ValueError("candidate_classification_unavailable")
                if not adaptive._is_live_xau_candidate(core, c):
                    return old_router(int(raw_signal_id), source=source)
                side = str(c.get("side") or "").lower()
                if side not in {"long","short"}:
                    raise ValueError("invalid_live_xau_side")
                with core.get_conn() as conn:
                    existing = conn.execute(
                        "SELECT id FROM metals_xau_live_trade_links WHERE raw_signal_id=? LIMIT 1",
                        (int(raw_signal_id),)).fetchone()
                if existing:
                    return old_router(int(raw_signal_id), source=source)
                s = broker_snapshot(core)
                proposed = next_order_risk(core, side, s)
                proof = {"version":VERSION, "cap_pct_nav":CAP_PCT_NAV,
                         "nav_gbp":round(s["nav"],2), "open_xau":s["open"],
                         "risk_used_gbp":round(s["used"],2),
                         "new_risk_with_buffer_gbp":round(proposed,2),
                         "cap_gbp":round(s["cap"],2)}
                if not admission(s["used"], proposed, s["nav"]):
                    try:
                        core._metals_demo_audit(int(raw_signal_id), "XAUUSD", "XAU_USD",
                            "xau_nav_budget", "BLOCKED", "xau_nav_budget_exhausted",
                            response=proof, candidate_state="CANDIDATE")
                    except Exception:
                        pass
                    return {"ok":True,"blocked":True,"reason":"xau_nav_budget_exhausted",
                            "asset":"XAUUSD","side":side,"nav_budget":proof}
                result = old_router(int(raw_signal_id),source=source)
                if isinstance(result,dict):
                    result=dict(result); result["nav_budget"]=proof
                return result
            except Exception as exc:
                print("XAU_NAV_BUDGET_PREFLIGHT_BLOCK",f"{type(exc).__name__}:{exc}",flush=True)
                return {"ok":True,"blocked":True,"reason":"xau_nav_budget_preflight_unavailable",
                        "asset":"XAUUSD","version":VERSION}

    # Replaces only XAU cumulative-entry/funding and half-frequency vetoes.
    # All other policy routers, direction checks, minimum size and exits remain in place.
    adaptive.NORMAL_ALLOWANCE = 10**9
    adaptive.FULL_SIZE_THROUGH_ORDINAL = 10**9
    overflow.FULL_SIZE_THROUGH = 10**9
    core.execute_metals_demo_candidate = router
    core._XAU_NAV_BUDGET_INSTALLED = True
    core.METALS_XAU_NAV_BUDGET_VERSION = VERSION
    app.add_api_route("/api/xau-nav-budget",status,methods=["GET"],include_in_schema=False)
    promote_read_only_route(app,"/api/xau-nav-budget",status)
    return {"installed":True,"version":VERSION,"cap_pct_nav":CAP_PCT_NAV,
            "risk_basis":"current_liquidation_quote_to_broker_stop",
            "new_xau_entries_fail_closed":True,
            "replaces":["20_plus_2R_funding","post_32_half_frequency"],
            "existing_orders_stops_harvest_unchanged":True,"xag_unchanged":True}
