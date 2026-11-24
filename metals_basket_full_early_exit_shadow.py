"""Forward-only, read-only basket-full early-MFE25 challenger for LIVE XAU/XAG shorts.

Uses only captured broker marks, verified read-only NAV budget snapshots, and
persisted LIVE manager candle counters. Hypothetical closures NEVER reach OANDA.
No order/stop/entry/harvest hooks, execution permissions, or policy overrides.
Results are five-minute MARK approximations, NOT broker-executable fills or a
causal portfolio replay. XAU long/ATR2 remains observational, not simulated.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

VERSION = "basket_full_early_mfe25_shadow_v1_2026_11_24"
POLICIES = (("ANY_POSITIVE", 0.0, 0.0),
            ("MFE_1R", 1.0, 0.0),
            ("MFE_1R_12H", 1.0, 12.0),
            ("MFE_2R", 2.0, 0.0))
MAX_EXPORT_ROWS = 100000


def _num(x):
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _time(x):
    try:
        d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def _ensure(conn):
    serial = ("BIGSERIAL PRIMARY KEY" if getattr(conn, "postgres", False)
              else "INTEGER PRIMARY KEY AUTOINCREMENT")
    conn.execute("""CREATE TABLE IF NOT EXISTS metals_basket_full_shadow_config (
        id INTEGER PRIMARY KEY, first_sample_utc TEXT NOT NULL
    )""")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS metals_basket_full_shadow_samples (
        id {serial}, sampled_at_utc TEXT NOT NULL, asset TEXT NOT NULL,
        status TEXT NOT NULL, reason TEXT, broker_risk_used_gbp DOUBLE PRECISION,
        broker_risk_cap_gbp DOUBLE PRECISION, nav_gbp DOUBLE PRECISION,
        broker_open_count INTEGER, budget_full INTEGER,
        young_short_count INTEGER, legacy_young_excluded INTEGER,
        execution_authority INTEGER NOT NULL DEFAULT 0,
        UNIQUE(sampled_at_utc,asset)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS metals_basket_full_shadow_trades (
        asset TEXT NOT NULL, broker_trade_id TEXT NOT NULL, policy TEXT NOT NULL,
        first_seen_utc TEXT NOT NULL, opened_at_utc TEXT NOT NULL,
        last_observed_utc TEXT NOT NULL, observed_mfe_r DOUBLE PRECISION NOT NULL,
        armed_at_utc TEXT, armed_mfe_r DOUBLE PRECISION,
        early_exit_at_utc TEXT, early_exit_observed_r DOUBLE PRECISION,
        early_exit_mark_gbp DOUBLE PRECISION,
        actual_broker_close_at_utc TEXT, actual_gross_realised_gbp DOUBLE PRECISION,
        last_status TEXT NOT NULL, notes TEXT,
        execution_authority INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(asset,broker_trade_id,policy)
    )""")


def _budget(core, asset):
    # Both functions have only OANDA GET calls and fail if current stop/ownership
    # cannot be verified. They are the exact entry-gate budget calculations.
    try:
        if asset == "XAU":
            from metals_xau_nav_risk_override import broker_snapshot
            s = broker_snapshot(core)
        else:
            from metals_xag_nav_risk_override import snapshot
            s = snapshot(core)
        used, cap, nav = (_num(s.get(k)) for k in ("used", "cap", "nav"))
        if used is None or cap is None or nav is None or used < 0 or cap <= 0 or nav <= 0:
            raise ValueError("invalid_broker_budget_values")
        return {"status":"VERIFIED", "reason":None,
                "used":used,"cap":cap,"nav":nav,"open":int(s["open"]),
                "full":used + 1e-9 >= cap}
    except Exception as exc:
        return {"status":"UNVERIFIED", "reason":type(exc).__name__ + ":" + str(exc)[:110],
                "used":None,"cap":None,"nav":None,"open":None,"full":None}


def capture(core, sampled_at_utc):
    """Consume exactly one newly collected forward sample, never backfill history."""
    with core.get_conn() as conn:
        _ensure(conn)
        prior = conn.execute(
            "SELECT first_sample_utc FROM metals_basket_full_shadow_config WHERE id=1"
        ).fetchone()
        if prior is None:
            conn.execute("INSERT INTO metals_basket_full_shadow_config VALUES (1,?)",
                         (sampled_at_utc,))
            first = sampled_at_utc
        else:
            first = dict(prior)["first_sample_utc"]
        source = conn.execute(
            "SELECT status,position_marks_json FROM metals_forward_equity_samples "
            "WHERE sampled_at_utc=?", (sampled_at_utc,)
        ).fetchone()
        if source is None:
            raise ValueError("missing_matching_forward_source_sample")
        source = dict(source)
        marks = json.loads(source.get("position_marks_json") or "[]")
        if not isinstance(marks, list):
            raise ValueError("malformed_forward_position_marks")
        # Both link tables are live-owned; reading them changes no trade state.
        link_maps = {}
        for asset, table in (("XAU","metals_xau_live_trade_links"),
                             ("XAG","metals_xag_live_trade_links")):
            rows = [dict(r) for r in conn.execute(
                "SELECT broker_trade_id,status,manager_last_review_candles,"
                "closed_at_utc,realized_pl FROM " + table
            ).fetchall()]
            link_maps[asset] = {str(r["broker_trade_id"]): r for r in rows
                                if r.get("broker_trade_id")}
        conn.commit()

    budgets = {asset:_budget(core,asset) for asset in ("XAU","XAG")}
    rows = {"XAU":[],"XAG":[]}
    for mark in marks:
        if not isinstance(mark,dict):
            continue
        instrument = str(mark.get("instrument") or "").upper()
        side = str(mark.get("side") or "").upper()
        asset = "XAU" if instrument == "XAU_USD" else "XAG" if instrument == "XAG_USD" else None
        if asset and side == "SHORT":
            rows[asset].append(mark)

    result = {"version":VERSION,"sampled_at_utc":sampled_at_utc,"lanes":{},
              "execution_authority":False,"broker_order_writes":False}
    timestamp = _time(sampled_at_utc)
    earliest = _time(first)
    if timestamp is None or earliest is None:
        raise ValueError("invalid_source_or_activation_timestamp")
    good_source = source.get("status") == "COMPLETE"

    with core.get_conn() as conn:
        _ensure(conn)
        for asset in ("XAU","XAG"):
            risk = budgets[asset]
            good = good_source and risk["status"] == "VERIFIED"
            young = 0
            legacy = 0
            considered = 0
            for m in rows[asset]:
                bid = str(m.get("trade_id") or "").strip()
                opened = _time(m.get("opened_at_utc"))
                r = _num(m.get("current_r"))
                gbp = _num(m.get("unrealized_gbp"))
                link = link_maps[asset].get(bid)
                if not bid or opened is None or not link:
                    continue
                completed = _num(link.get("manager_last_review_candles"))
                if completed is None or completed < 0:
                    continue
                if completed >= 48:
                    continue
                young += 1
                if opened < earliest:
                    legacy += 1
                    continue
                if not good or r is None or gbp is None:
                    continue
                age_h = (timestamp-opened).total_seconds()/3600.0
                if age_h < 0:
                    continue
                considered += 1
                for policy,threshold,min_age in POLICIES:
                    existing = conn.execute(
                        "SELECT * FROM metals_basket_full_shadow_trades "
                        "WHERE asset=? AND broker_trade_id=? AND policy=?",
                        (asset,bid,policy)).fetchone()
                    old = dict(existing) if existing else {}
                    peak = max(0.,_num(old.get("observed_mfe_r")) or 0.,r)
                    armed = old.get("armed_at_utc")
                    armed_mfe = _num(old.get("armed_mfe_r"))
                    exit_at = old.get("early_exit_at_utc")
                    exit_r = _num(old.get("early_exit_observed_r"))
                    exit_gbp = _num(old.get("early_exit_mark_gbp"))
                    # A full-budget trigger is separate from eligibility: a
                    # trade reaching peak R after the basket freed up does not
                    # automatically become armed until a future full snapshot.
                    if not armed and risk["full"] and age_h >= min_age and (
                        peak > 0 if threshold == 0 else peak >= threshold
                    ):
                        armed = sampled_at_utc
                        armed_mfe = peak
                    if armed and not exit_at and peak>0 and r <= .75*peak:
                        # This is a mark-time hypothetical, not an order or a
                        # promised broker stop/close fill.
                        exit_at = sampled_at_utc
                        exit_r = r
                        exit_gbp = gbp
                    status = ("EARLY_EXIT_SHADOW" if exit_at else
                              "ARMED_SHADOW" if armed else "OBSERVING")
                    conn.execute("""INSERT INTO metals_basket_full_shadow_trades (
                        asset,broker_trade_id,policy,first_seen_utc,opened_at_utc,
                        last_observed_utc,observed_mfe_r,armed_at_utc,armed_mfe_r,
                        early_exit_at_utc,early_exit_observed_r,early_exit_mark_gbp,
                        actual_broker_close_at_utc,actual_gross_realised_gbp,
                        last_status,notes,execution_authority)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                        ON CONFLICT(asset,broker_trade_id,policy) DO UPDATE SET
                        last_observed_utc=excluded.last_observed_utc,
                        observed_mfe_r=excluded.observed_mfe_r,
                        armed_at_utc=excluded.armed_at_utc,
                        armed_mfe_r=excluded.armed_mfe_r,
                        early_exit_at_utc=excluded.early_exit_at_utc,
                        early_exit_observed_r=excluded.early_exit_observed_r,
                        early_exit_mark_gbp=excluded.early_exit_mark_gbp,
                        last_status=excluded.last_status
                    """, (asset,bid,policy,old.get("first_seen_utc") or sampled_at_utc,
                          opened.isoformat(),sampled_at_utc,peak,armed,armed_mfe,
                          exit_at,exit_r,exit_gbp,
                          old.get("actual_broker_close_at_utc"),
                          old.get("actual_gross_realised_gbp"),status,
                          "MFE from five-minute marks; incomplete intrahour path"))
            # Later broker-closed outcomes are recorded separately and labelled
            # GROSS: XAG financing attribution and full economic replay differ.
            for bid,link in link_maps[asset].items():
                if not str(link.get("status") or "").upper().startswith("CLOSED"):
                    continue
                actual = _num(link.get("realized_pl"))
                if actual is None:
                    continue
                conn.execute("""UPDATE metals_basket_full_shadow_trades
                    SET actual_broker_close_at_utc=?,actual_gross_realised_gbp=?
                    WHERE asset=? AND broker_trade_id=?
                      AND actual_broker_close_at_utc IS NULL
                """, (link.get("closed_at_utc"),actual,asset,bid))
            status = "VERIFIED" if good else "INCOMPLETE"
            reason = (None if good else
                      "forward_mark_unverified" if not good_source else risk["reason"])
            conn.execute("""INSERT INTO metals_basket_full_shadow_samples (
                sampled_at_utc,asset,status,reason,broker_risk_used_gbp,
                broker_risk_cap_gbp,nav_gbp,broker_open_count,budget_full,
                young_short_count,legacy_young_excluded,execution_authority)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,0)
                ON CONFLICT(sampled_at_utc,asset) DO NOTHING
            """, (sampled_at_utc,asset,status,reason,risk["used"],risk["cap"],
                  risk["nav"],risk["open"],
                  int(risk["full"]) if good else None,
                  young,legacy))
            result["lanes"][asset] = {
                "status":status,"budget_full":risk["full"] if good else None,
                "young_short_count":young,"new_short_count_eligible_for_study":considered,
                "legacy_young_excluded":legacy}
        conn.commit()
    return result


def export_rows(core):
    with core.get_conn() as conn:
        _ensure(conn)
        output={}
        for label,table in (("budget","metals_basket_full_shadow_samples"),
                            ("trades","metals_basket_full_shadow_trades")):
            count = int(conn.execute("SELECT COUNT(*) AS n FROM "+table).fetchone()["n"])
            if count>MAX_EXPORT_ROWS:
                raise ValueError("basket_full_shadow_export_cap_exceeded_no_truncation")
            output[label] = [dict(r) for r in conn.execute(
                "SELECT * FROM "+table+" ORDER BY "+
                ("sampled_at_utc,asset" if label=="budget"
                 else "asset,broker_trade_id,policy")).fetchall()]
    return output


def status(core):
    data=export_rows(core)
    recent={}
    for asset in ("XAU","XAG"):
        budget=[x for x in data["budget"] if x["asset"]==asset]
        trades=[x for x in data["trades"] if x["asset"]==asset]
        recent[asset]={
            "last_sample":budget[-1] if budget else None,
            "sample_count":len(budget),
            "verified_samples":sum(x["status"]=="VERIFIED" for x in budget),
            "early_exit_signals_by_policy":{
                name:sum(x["policy"]==name and x["early_exit_at_utc"] is not None
                         for x in trades)
                for name,_,_ in POLICIES},
        }
    return {"installed":True,"version":VERSION,"research_only":True,
            "execution_authority":False,"broker_order_writes":False,
            "note":"Five-minute counterfactual marks, not orders, fills or full portfolio P&L.",
            "scope":"XAU_SHORT_AND_XAG_SHORT;XAU_LONG_ATR2_NOT_MODELLED",
            "lanes":recent}


def install(app,core):
    from metals_route_priority import promote_read_only_route
    def view():
        try:
            return status(core)
        except Exception as exc:
            return {"installed":True,"research_only":True,"ok":False,
                    "error_type":type(exc).__name__,
                    "execution_authority":False}
    path="/api/metals-basket-full-early-exit-shadow"
    app.add_api_route(path,view,methods=["GET"],include_in_schema=False)
    promote_read_only_route(app,path,view)
    return {"installed":True,"version":VERSION,"research_only":True,
            "execution_authority":False,"broker_order_writes":False,
            "source":"existing five-minute forward capture",
            "scenarios":[x[0] for x in POLICIES]}
