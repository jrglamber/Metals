"""Forward-only LIVE Metals economic-equity and XAG funding-R research recorder.

No broker order/stop APIs, no changes to trade state and no shadow execution.
Samples the already canonical, broker-backed read-only reporting contract every
five minutes; stores independently verified/unknown fields without fake zeroes.
Historical broker paths prior to this installation are NOT reconstructed.
"""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any

VERSION = "metals_forward_economic_capture_v1_2026_11_18"
INTERVAL_SECONDS = 300
_lock = threading.Lock()
_worker = None
_state = {"last_sample_at_utc": None, "last_complete_at_utc": None,
          "last_status": "NOT_STARTED", "last_error_class": None,
          "sample_count_since_start": 0, "last_economic_gbp": None,
          "last_xag_funding_proxy_r": None}


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _number(x):
    if x is None or isinstance(x, bool):
        return None
    try:
        n = float(x)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _float_sum(items, field):
    values = [_number(t.get(field)) for t in items]
    return sum(values) if all(n is not None for n in values) else None


def _datetime(x):
    try:
        v = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return v.astimezone(timezone.utc) if v.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_age_seconds(stamp, now):
    dt = _datetime(stamp)
    return (now - dt).total_seconds() if dt else None


def _ensure(conn):
    serial = "BIGSERIAL PRIMARY KEY" if getattr(conn, "postgres", False) else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute("""CREATE TABLE IF NOT EXISTS metals_forward_equity_state (
        id INTEGER PRIMARY KEY, campaign_id TEXT, baseline_realised_gbp DOUBLE PRECISION,
        peak_equity_gbp DOUBLE PRECISION, started_at_utc TEXT,
        was_flat INTEGER, bootstrap_partial INTEGER
    )""")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS metals_forward_equity_samples (
        id {serial}, sampled_at_utc TEXT NOT NULL UNIQUE,
        source_generated_at_utc TEXT, status TEXT NOT NULL, reason TEXT,
        xau_open_count INTEGER, xag_open_count INTEGER, total_open_count INTEGER,
        xag_campaign_epoch_utc TEXT, xag_campaign_entries INTEGER,
        xag_open_r DOUBLE PRECISION, xag_banked_r_proxy DOUBLE PRECISION,
        xag_funding_r_proxy DOUBLE PRECISION, xag_funding_status TEXT,
        xau_verified_since_epoch_gbp DOUBLE PRECISION, xag_verified_lifetime_gbp DOUBLE PRECISION,
        combined_realised_since_epochs_gbp DOUBLE PRECISION,
        combined_open_pnl_gbp DOUBLE PRECISION,
        family_campaign_id TEXT, family_bootstrap_partial INTEGER,
        family_baseline_realised_gbp DOUBLE PRECISION,
        realised_since_family_baseline_gbp DOUBLE PRECISION,
        economic_equity_gbp DOUBLE PRECISION, economic_high_water_gbp DOUBLE PRECISION,
        economic_giveback_gbp DOUBLE PRECISION,
        mark_to_market_complete INTEGER NOT NULL DEFAULT 0,
        broker_population_matched INTEGER NOT NULL DEFAULT 0,
        cash_broker_verified INTEGER NOT NULL DEFAULT 0,
        position_marks_json TEXT, execution_authority INTEGER NOT NULL DEFAULT 0
    )""")


def _xau_cash(conn, now):
    """Use same broker-synced XAU accounting epoch and freshness as Live ZIP."""
    e = conn.execute("SELECT value FROM metals_xau_live_runtime_state WHERE key='accounting_epoch_utc'").fetchone()
    t = conn.execute("SELECT updated_at_utc FROM metals_xau_live_runtime_state WHERE key='broker_transaction_cursor'").fetchone()
    epoch = (dict(e).get("value") if e else None)
    observed = (dict(t).get("updated_at_utc") if t else None)
    age = _safe_age_seconds(observed, now)
    if not _datetime(epoch) or age is None or age < -10 or age > 900:
        return None
    rows = conn.execute("SELECT instrument,pl_gbp,financing_gbp,net_realized_gbp FROM metals_xau_live_broker_transactions").fetchall()
    if len(rows) > 100000:
        return None
    total = 0.
    for raw in rows:
        r = dict(raw)
        a,b,n = (_number(r.get(k)) for k in ("pl_gbp","financing_gbp","net_realized_gbp"))
        if str(r.get("instrument")) != "XAU_USD" or None in (a,b,n) or abs(a+b-n)>0.02:
            return None
        total += n
    return round(total,6)


def _xag_funding(conn, positions, now):
    """Observe exact broker open marks and independently scoped *proxy* banked R.

    Local trade realised_pl excludes independently attributed daily financing,
    so this must never be called 'verified full funding R' or used to gate LIVE.
    """
    active = [t for t in (positions.get("trades") or []) if t.get("instrument") == "XAG_USD"]
    marks = []
    tracked = [dict(r) for r in conn.execute(
        "SELECT broker_trade_id,created_at_utc,closed_at_utc,status,"
        "realized_pl,estimated_risk_amount "
        "FROM metals_xag_live_trade_links"
    ).fetchall()]
    by_id = {str(r.get("broker_trade_id")): r for r in tracked if r.get("broker_trade_id")}
    if not positions.get("ok") or not positions.get("xag_broker_link_reconciled"):
        return {"status": "BROKER_LINKS_UNVERIFIED", "open_r": None,
                "banked_r": None, "funding_r": None, "marks": []}
    open_r = 0.0
    for t in active:
        tid = str(t.get("broker_trade_id") or "")
        item = by_id.get(tid)
        if not item or str(item.get("status")) != "OPEN" or str(t.get("side")) != "SHORT":
            return {"status":"OPEN_OWNERSHIP_UNVERIFIED","open_r":None,
                    "banked_r":None,"funding_r":None,"marks":[]}
        risk = _number(item.get("estimated_risk_amount"))
        pnl = _number(t.get("unrealized_gbp"))
        if risk is None or risk <= 0 or pnl is None:
            return {"status":"OPEN_R_INCOMPLETE","open_r":None,
                    "banked_r":None,"funding_r":None,"marks":[]}
        open_r += pnl/risk
        marks.append({"trade_id":tid,"instrument":"XAG_USD","side":"SHORT",
                      "opened_at_utc":t.get("opened_at_utc"),
                      "units":t.get("units"),"entry_price":t.get("entry_price"),
                      "unrealized_gbp":pnl,"estimated_risk_gbp":risk,
                      "current_r":round(pnl/risk,6),"stop_price":t.get("stop_price")})
    try:
        s = conn.execute("SELECT started_at_utc FROM metals_xag_live_campaign_state WHERE id=1").fetchone()
        epoch = dict(s).get("started_at_utc") if s else None
    except Exception:
        epoch = None
    if not epoch or not _datetime(epoch):
        return {"status":"LEGACY_CAMPAIGN_EPOCH_UNKNOWN","open_r":round(open_r,6),
                "banked_r":None,"funding_r":None,"epoch":None,
                "count":None,"marks":marks}
    selected = [r for r in tracked if _datetime(r.get("created_at_utc")) is not None
                and _datetime(r.get("created_at_utc")) >= _datetime(epoch)]
    banked = 0.0
    complete = True
    for r in selected:
        status = str(r.get("status") or "").upper()
        if status.startswith("CLOSED"):
            pl = _number(r.get("realized_pl"))
            risk = _number(r.get("estimated_risk_amount"))
            if pl is None or risk is None or risk <= 0:
                complete = False
            else:
                banked += pl/risk
        elif status not in ("OPEN",):
            complete = False
    return {"status":"PROXY_EXCLUDES_BROKER_FINANCING" if complete else "BANKED_R_INCOMPLETE",
            "open_r":round(open_r,6),
            "banked_r":round(banked,6) if complete else None,
            "funding_r":round(open_r+banked,6) if complete else None,
            "epoch":epoch,"count":len(selected),"marks":marks}


def _capture(core, report, now=None):
    now = now or datetime.now(timezone.utc)
    sample_time = now.isoformat()
    p = report.get("positions") or {}
    combined = report.get("combined") or {}
    cash = report.get("cash") or {}
    risk = report.get("risk") or {}
    good_positions = p.get("ok") is True and combined.get("ok") is True and risk.get("ok") is True
    generation_age = _safe_age_seconds(report.get("generated_at_utc"),now)
    if generation_age is None or generation_age < -10 or generation_age > 120:
        good_positions = False
    pnl = _number(combined.get("open_pnl_gbp")) if good_positions else None
    if pnl is None:
        good_positions = False
    proof = cash.get("proof") if cash.get("ok") is True else None
    xag_cash = _number((proof or {}).get("realised_all_time_gbp"))
    # Verify the XAG auditor's timestamp, not just the producer's own age.
    cash_age = _safe_age_seconds((proof or {}).get("time_utc"),now)
    if cash_age is None or cash_age < -10 or cash_age > 900:
        xag_cash = None
    row = {
        "sampled_at_utc":sample_time,
        "source_generated_at_utc":report.get("generated_at_utc"),
        "status":"INCOMPLETE", "reason":None,
        "xau_open_count":p.get("xau_count") if good_positions else None,
        "xag_open_count":p.get("xag_short_count") if good_positions else None,
        "total_open_count":p.get("count") if good_positions else None,
        "xag_campaign_epoch_utc":None,"xag_campaign_entries":None,
        "xag_open_r":None,"xag_banked_r_proxy":None,
        "xag_funding_r_proxy":None,"xag_funding_status":"UNVERIFIED",
        "xau_verified_since_epoch_gbp":None,
        "xag_verified_lifetime_gbp":xag_cash,
        "combined_realised_since_epochs_gbp":None,
        "combined_open_pnl_gbp":pnl,
        "family_campaign_id":None,"family_bootstrap_partial":None,
        "family_baseline_realised_gbp":None,
        "realised_since_family_baseline_gbp":None,
        "economic_equity_gbp":None,"economic_high_water_gbp":None,
        "economic_giveback_gbp":None,
        "mark_to_market_complete":0,
        "broker_population_matched":1 if good_positions else 0,
        "cash_broker_verified":0,
        "position_marks_json":None,"execution_authority":0,
    }
    with core.get_conn() as conn:
        _ensure(conn)
        try:
            xau_cash = _xau_cash(conn,now)
        except Exception:
            xau_cash = None
        row["xau_verified_since_epoch_gbp"] = xau_cash
        if good_positions:
            # Preserve each XAU trade's original live risk basis alongside its
            # broker mark, so future integer-trade basket replay is possible.
            try:
                xau_risks = {
                    str(r["broker_trade_id"]): _number(r["estimated_risk_amount"])
                    for r in conn.execute(
                        "SELECT broker_trade_id,estimated_risk_amount "
                        "FROM metals_xau_live_trade_links "
                        "WHERE UPPER(COALESCE(status,'')) IN ('OPEN','OPEN_UNVERIFIED_STOP','CLOSE_RETRY')"
                    ).fetchall()
                }
            except Exception:
                xau_risks = {}
            xag = _xag_funding(conn,p,now)
            row.update({"xag_campaign_epoch_utc":xag.get("epoch"),
                "xag_campaign_entries":xag.get("count"),
                "xag_open_r":xag.get("open_r"),
                "xag_banked_r_proxy":xag.get("banked_r"),
                "xag_funding_r_proxy":xag.get("funding_r"),
                "xag_funding_status":xag["status"]})
            by_id = {str(m["trade_id"]):m for m in xag.get("marks") or []}
            for t in p.get("trades") or []:
                if t.get("instrument") == "XAU_USD":
                    tid=str(t.get("broker_trade_id"))
                    risk_basis=xau_risks.get(tid)
                    upl=_number(t.get("unrealized_gbp"))
                    by_id[tid] = {
                        "trade_id":tid,
                        "instrument":"XAU_USD","side":t.get("side"),
                        "opened_at_utc":t.get("opened_at_utc"),
                        "units":t.get("units"),"entry_price":t.get("entry_price"),
                        "unrealized_gbp":upl,
                        "estimated_risk_gbp":risk_basis,
                        "current_r":round(upl/risk_basis,6) if risk_basis and upl is not None and risk_basis>0 else None,
                        "stop_price":t.get("stop_price")}
            # If XAG R isn't calculable, still preserve raw observed marks.
            if len(by_id) != int(p.get("count") or 0):
                for t in p.get("trades") or []:
                    tid = str(t.get("broker_trade_id"))
                    by_id.setdefault(tid,{
                        "trade_id":tid,"instrument":t.get("instrument"),"side":t.get("side"),
                        "opened_at_utc":t.get("opened_at_utc"),
                        "units":t.get("units"),"entry_price":t.get("entry_price"),
                        "unrealized_gbp":_number(t.get("unrealized_gbp")),
                        "estimated_risk_gbp":None,"current_r":None,
                        "stop_price":t.get("stop_price")})
            row["position_marks_json"] = json.dumps(list(by_id.values()),separators=(",",":"),default=str)
        if good_positions and xau_cash is not None and xag_cash is not None:
            combined_cash = round(xau_cash+xag_cash,6)
            row["combined_realised_since_epochs_gbp"]=combined_cash
            row["cash_broker_verified"]=1
            existing = conn.execute("SELECT * FROM metals_forward_equity_state WHERE id=1").fetchone()
            state = dict(existing) if existing else None
            flat = int(p.get("count"))==0
            if state is None:
                state = {"campaign_id":"FAMILY_"+sample_time,
                         "baseline_realised_gbp":combined_cash,
                         "peak_equity_gbp":0.,
                         "started_at_utc":sample_time,
                         "was_flat":int(flat),
                         "bootstrap_partial":int(not flat)}
            elif flat and not bool(state.get("was_flat")):
                # Reset ONLY on matched verified broker-confirmed flat.
                state = {"campaign_id":"FAMILY_"+sample_time,
                         "baseline_realised_gbp":combined_cash,
                         "peak_equity_gbp":0.,
                         "started_at_utc":sample_time,
                         "was_flat":1,"bootstrap_partial":0}
            elif not flat and bool(state.get("was_flat")):
                state["was_flat"]=0
            baseline = _number(state.get("baseline_realised_gbp"))
            if baseline is not None:
                realised_since=combined_cash-baseline
                economic=realised_since+pnl
                previous=_number(state.get("peak_equity_gbp"))
                peak=max(0.,previous or 0.,economic)
                row.update({
                    "status":"COMPLETE",
                    "family_campaign_id":state["campaign_id"],
                    "family_bootstrap_partial":state["bootstrap_partial"],
                    "family_baseline_realised_gbp":baseline,
                    "realised_since_family_baseline_gbp":round(realised_since,6),
                    "economic_equity_gbp":round(economic,6),
                    "economic_high_water_gbp":round(peak,6),
                    "economic_giveback_gbp":round(max(0.,peak-economic),6),
                    "mark_to_market_complete":1})
                state["peak_equity_gbp"]=peak
                # Only change the shadow research state, never live campaign state.
                conn.execute("""INSERT INTO metals_forward_equity_state
                    (id,campaign_id,baseline_realised_gbp,peak_equity_gbp,
                     started_at_utc,was_flat,bootstrap_partial)
                    VALUES(1,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                    campaign_id=excluded.campaign_id,
                    baseline_realised_gbp=excluded.baseline_realised_gbp,
                    peak_equity_gbp=excluded.peak_equity_gbp,
                    started_at_utc=excluded.started_at_utc,
                    was_flat=excluded.was_flat,
                    bootstrap_partial=excluded.bootstrap_partial""",
                    (state["campaign_id"],baseline,peak,state["started_at_utc"],
                     state["was_flat"],state["bootstrap_partial"]))
        if row["status"]!="COMPLETE":
            reasons=[]
            if not good_positions: reasons.append("positions_or_risk_not_verified")
            if xau_cash is None: reasons.append("xau_cash_epoch_or_sync_unverified")
            if xag_cash is None: reasons.append("xag_cash_audit_unverified")
            row["reason"]=",".join(reasons) or "research_state_unavailable"
        cols=list(row)
        q="INSERT INTO metals_forward_equity_samples ("+",".join(cols)+") VALUES ("+",".join("?" for _ in cols)+")"
        conn.execute(q,tuple(row[c] for c in cols))
        conn.commit()
    return {"ok":row["status"]=="COMPLETE","status":row["status"],
            "sampled_at_utc":sample_time,
            "economic_equity_gbp":row["economic_equity_gbp"],
            "xag_funding_r_proxy":row["xag_funding_r_proxy"],
            "xag_funding_status":row["xag_funding_status"]}


def capture_once(core):
    from metals_reporting_snapshot import snapshot
    report = snapshot(core)
    result = _capture(core,report)
    # Independent, execution-disabled basket-full exit shadow. Any failure in
    # this research layer must NEVER impact the live economic-equity recorder,
    # order routing, stop managers, or broker reconciliation.
    try:
        from metals_basket_full_early_exit_shadow import capture as _shadow_capture
        result["basket_full_shadow"] = _shadow_capture(core, result["sampled_at_utc"])
    except Exception as exc:
        result["basket_full_shadow"] = {"ok":False,"research_only":True,
            "error_type":type(exc).__name__,"execution_authority":False}
        print("METALS_BASKET_FULL_SHADOW ERROR "+type(exc).__name__,flush=True)
    with _lock:
        _state["last_sample_at_utc"]=result["sampled_at_utc"]
        _state["last_status"]=result["status"]
        _state["last_error_class"]=None
        _state["sample_count_since_start"]+=1
        _state["last_economic_gbp"]=result["economic_equity_gbp"]
        _state["last_xag_funding_proxy_r"]=result["xag_funding_r_proxy"]
        if result["ok"]:
            _state["last_complete_at_utc"]=result["sampled_at_utc"]
    return result


def _loop(core):
    # Capture after initial deployment settles; don't compete with entry
    # preflight, startup reconciliation or synchronised broker audits.
    time.sleep(20)
    while True:
        delay=INTERVAL_SECONDS
        try:
            result=capture_once(core)
            if not result["ok"]:
                # Broker cash audit may still be warming up after deployment.
                # Retry research-only reads sooner, never alter live execution.
                delay=60
                print("METALS_FORWARD_CAPTURE incomplete "+str(result["status"]),flush=True)
        except Exception as exc:
            delay=60
            with _lock:
                _state["last_error_class"]=type(exc).__name__
                _state["last_status"]="CAPTURE_ERROR"
            print("METALS_FORWARD_CAPTURE ERROR "+type(exc).__name__,flush=True)
        time.sleep(delay)


def start(core):
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return dict(_state,thread_alive=True,version=VERSION)
        _worker=threading.Thread(target=_loop,args=(core,),name="metals-research-only-forward-capture",daemon=True)
        _worker.start()
        return dict(_state,thread_alive=_worker.is_alive(),version=VERSION,
                    interval_seconds=INTERVAL_SECONDS,broker_order_writes=False,
                    execution_authority=False)


def health():
    with _lock:
        return dict(_state,thread_alive=bool(_worker and _worker.is_alive()),
                    version=VERSION,interval_seconds=INTERVAL_SECONDS,
                    execution_authority=False,broker_order_writes=False)


def export_rows(core):
    """Recent-to-full forward rows; strict cap avoids silent truncation."""
    with core.get_conn() as conn:
        _ensure(conn)
        n=conn.execute("SELECT COUNT(*) AS n FROM metals_forward_equity_samples").fetchone()["n"]
        if int(n)>100000:
            raise ValueError("forward research exceeds export cap; never truncate silently")
        return [dict(r) for r in conn.execute(
            "SELECT * FROM metals_forward_equity_samples ORDER BY id ASC").fetchall()]
