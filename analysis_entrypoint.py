"""Project Exit Plan — read-only analysis interface v1.

Observability only. This module imports the existing runtime and attaches
read-only endpoints. It has no broker-write, sizing, entry, exit, stop,
harvest, or research-decision authority.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict

import app_postgres_runtime as core
from fastapi import FastAPI, Request
from fastapi.responses import Response

# Keep a stable outer app: the core runtime may rebuild/rebind its FastAPI
# object during import/startup. Analysis routes live on this wrapper and the
# unchanged production application is mounted only after those routes exist.
app = FastAPI(title="Project Exit Plan — Analysis Wrapper")
ANALYSIS_INTERFACE_VERSION = "2.9.0"
VISIBLE_RELEASE_VERSION = "v1.6.51"
PROJECT_NAME = os.getenv("PEP_ANALYSIS_PROJECT", "metals")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_attr(name: str, default: Any = None) -> Any:
    try:
        value = getattr(core, name, default)
        return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
    except Exception:
        return default


def _health_snapshot() -> Dict[str, Any]:
    # Keep v1 deliberately generic and non-invasive. Rich DB/trade research
    # fields are added per producer after the transport path is proven.
    return {
        "runtime_loaded": True,
        "database_configured": bool(os.getenv("DATABASE_URL")),
        "postgres_runtime_enabled": os.getenv("POSTGRES_RUNTIME_ENABLED"),
        "broker_execution_enabled": os.getenv("BROKER_EXECUTION_ENABLED"),
        "broker_read_only": os.getenv("BROKER_READ_ONLY"),
    }


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _read_conn():
    getter = getattr(core, "get_conn", None)
    if not callable(getter):
        raise RuntimeError("runtime does not expose get_conn")
    return getter()


def _table_inventory() -> Dict[str, Any]:
    """Read-only Postgres schema inventory used to build producer-specific v2 views."""
    try:
        with _read_conn() as conn:
            rows = conn.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema='public' AND table_type='BASE TABLE'
                ORDER BY table_name
            """).fetchall()
            tables = []
            for row in rows:
                if isinstance(row, dict):
                    tables.append(row.get("table_name"))
                else:
                    try:
                        tables.append(row[0])
                    except Exception:
                        tables.append(str(row))
            return {"ok": True, "tables": [t for t in tables if t]}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "tables": []}


def _safe_table_count(table: str) -> Any:
    # table names originate only from information_schema, never request input.
    try:
        with _read_conn() as conn:
            row = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()
            if isinstance(row, dict):
                return next(iter(row.values()), None)
            return row[0] if row else None
    except Exception:
        return None


ANALYSIS_TABLE_HINTS = ("signal", "trade", "hwm", "harvest", "manager", "exit", "challenger", "research", "execution", "basket", "highwater")


ANALYSIS_SLICE_TABLES = {
    "trades": ("closed_trades", "open_trades", "broker_trade_links"),
    "signals": ("raw_signals", "live_signal_pipeline_audit", "metals_signal_processing_audit", "index_pair_delivery_audit"),
    "execution": ("execution_audit_events", "metals_demo_execution_audit", "metals_xau_live_execution_audit"),
    "harvest": ("broker_harvest_events", "metals_demo_harvest_events", "metals_xau_live_harvest_events", "live_highwater_banking_research"),
    "hwm": ("metals_demo_hwm_events", "metals_xau_live_hwm_events", "active_basket_cycles", "active_family_basket_cycles"),
    "exits": ("post48_review_log", "dynamic_exit_shadow_trades", "atr2_exit_shadow_research", "metals_exit_challenger_shadow"),
    "research": ("index_entry_challenger_shadow", "index_directional_intelligence_research", "metals_directional_intelligence_research", "metals_focused_highwater", "metals_focused_recovery", "metals_focused_efficiency", "metals_focused_alignment"),
}

def _table_columns(table: str):
    with _read_conn() as conn:
        rows = conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=?
            ORDER BY ordinal_position
        """, (table,)).fetchall()
    return [r.get("column_name") if isinstance(r, dict) else r[0] for r in rows]

def _recent_rows(table: str, limit: int = 100):
    inv = set(_table_inventory().get("tables", []))
    if table not in inv:
        return []
    cols = _table_columns(table)
    order_col = next((x for x in ("created_at_utc","updated_at_utc","signal_time","entry_time","exit_time","id") if x in cols), None)
    sql = f'SELECT * FROM "{table}"'
    if order_col:
        sql += f' ORDER BY "{order_col}" DESC'
    sql += ' LIMIT ?'
    with _read_conn() as conn:
        rows = conn.execute(sql, (max(1, min(int(limit), 250)),)).fetchall()
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append({k: _jsonable(v) for k, v in row.items()})
        else:
            out.append({cols[i]: _jsonable(v) for i, v in enumerate(row)})
    return out

@app.get("/analysis/slice/{slice_name}")
def analysis_slice(slice_name: str, limit: int = 100) -> Dict[str, Any]:
    allowed = ANALYSIS_SLICE_TABLES.get(slice_name)
    if not allowed:
        return {"status":"error","error":"unknown analysis slice","allowed":sorted(ANALYSIS_SLICE_TABLES)}
    inv = set(_table_inventory().get("tables", []))
    data = {}
    for table in allowed:
        if table in inv:
            try:
                data[table] = _recent_rows(table, limit)
            except Exception as exc:
                data[table] = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "status":"ok","project":PROJECT_NAME,
        "analysis_interface_version":ANALYSIS_INTERFACE_VERSION,
        "app_version":VISIBLE_RELEASE_VERSION,
        "read_only_interface":True,"execution_authority":False,
        "time_utc":_now(),"slice":slice_name,"limit_per_table":max(1,min(int(limit),250)),
        "data":data,
    }


@app.get("/analysis/episode-index")
def analysis_episode_index(limit: int = 100) -> Dict[str, Any]:
    """Compact cycle-aware history for selecting episodes before drill-down."""
    bounded = max(1, min(int(limit), 250))
    data: Dict[str, Any] = {"demo_baskets": [], "xau_live_baskets": []}
    inv = set(_table_inventory().get("tables", []))
    try:
        with _read_conn() as conn:
            if "metals_demo_basket_snapshots" in inv:
                rows = conn.execute("""
                    SELECT basket_key, asset, side,
                           MIN(created_at_utc) AS first_seen_at,
                           MAX(created_at_utc) AS last_seen_at,
                           MAX(open_count) AS max_open_count,
                           MAX(high_water_r) AS max_high_water_r,
                           MAX(high_water_pnl_gbp) AS max_high_water_pnl_gbp,
                           MIN(basket_r) AS min_basket_r,
                           MAX(basket_r) AS max_basket_r,
                           MAX(giveback_pct) AS max_giveback_pct,
                           COUNT(*) AS snapshot_count
                    FROM metals_demo_basket_snapshots
                    GROUP BY basket_key, asset, side
                    ORDER BY max_high_water_pnl_gbp DESC NULLS LAST
                    LIMIT ?
                """, (bounded,)).fetchall()
                cols = ("basket_key","asset","side","first_seen_at","last_seen_at","max_open_count","max_high_water_r","max_high_water_pnl_gbp","min_basket_r","max_basket_r","max_giveback_pct","snapshot_count")
                data["demo_baskets"] = [
                    {k: _jsonable(v) for k, v in (row.items() if isinstance(row, dict) else zip(cols, row))}
                    for row in rows
                ]
                # Reconstruct economic family episodes only from observable lifecycle
                # boundaries. A new episode begins when a non-flat METALS_BASKET
                # follows a flat/zero-open snapshot, or when family direction changes.
                seq = conn.execute("""
                    SELECT id, created_at_utc, side, open_count, basket_r,
                           high_water_r, high_water_pnl_gbp, giveback_pct
                    FROM metals_demo_basket_snapshots
                    WHERE basket_key='METALS_BASKET'
                    ORDER BY created_at_utc, id
                """).fetchall()
                episodes, current, prev_side, prev_open = [], None, None, 0
                for r in seq:
                    x = dict(r) if isinstance(r, dict) else dict(zip(
                        ("id","created_at_utc","side","open_count","basket_r","high_water_r","high_water_pnl_gbp","giveback_pct"), r))
                    side = str(x.get("side") or "").upper()
                    oc = int(x.get("open_count") or 0)
                    active = oc > 0 and side not in ("", "FLAT", "MIXED")
                    boundary = active and (prev_open == 0 or (prev_side not in (None,"","FLAT","MIXED") and side != prev_side))
                    if boundary or (active and current is None):
                        if current:
                            episodes.append(current)
                        current = {"episode_id": f"METALS_{side}_{x.get('created_at_utc')}",
                                   "side": side, "first_seen_at": x.get("created_at_utc"),
                                   "last_seen_at": x.get("created_at_utc"), "max_open_count": oc,
                                   "max_high_water_r": x.get("high_water_r"), "max_high_water_pnl_gbp": x.get("high_water_pnl_gbp"),
                                   "min_basket_r": x.get("basket_r"), "max_basket_r": x.get("basket_r"),
                                   "max_giveback_pct": x.get("giveback_pct"), "snapshot_count": 0}
                    if current and active:
                        current["last_seen_at"] = x.get("created_at_utc")
                        current["snapshot_count"] += 1
                        for k, v in (("max_open_count",oc),("max_high_water_r",x.get("high_water_r")),
                                     ("max_high_water_pnl_gbp",x.get("high_water_pnl_gbp")),
                                     ("max_basket_r",x.get("basket_r")),("max_giveback_pct",x.get("giveback_pct"))):
                            if v is not None and (current.get(k) is None or v > current[k]): current[k] = v
                        v=x.get("basket_r")
                        if v is not None and (current.get("min_basket_r") is None or v < current["min_basket_r"]): current["min_basket_r"]=v
                    prev_open, prev_side = oc, side
                if current: episodes.append(current)
                data["economic_episodes"] = list(reversed(episodes[-bounded:]))
            if "metals_xau_live_hwm_events" in inv:
                row = conn.execute("""
                    SELECT MAX(high_water_gbp) AS max_high_water_gbp,
                           MAX(high_water_r) AS max_high_water_r,
                           MIN(created_at_utc) AS first_seen_at,
                           MAX(created_at_utc) AS last_seen_at,
                           COUNT(*) AS event_count
                    FROM metals_xau_live_hwm_events
                """).fetchone()
                if row:
                    cols = ("max_high_water_gbp","max_high_water_r","first_seen_at","last_seen_at","event_count")
                    data["xau_live_baskets"] = [{k: _jsonable(v) for k, v in (row.items() if isinstance(row, dict) else zip(cols, row))}]
        status, error = "ok", None
    except Exception as exc:
        status, error = "degraded", f"{type(exc).__name__}: {exc}"
    return {
        "status": status, "project": PROJECT_NAME,
        "analysis_interface_version": ANALYSIS_INTERFACE_VERSION,
        "app_version": VISIBLE_RELEASE_VERSION,
        "read_only_interface": True, "execution_authority": False,
        "time_utc": _now(), "limit": bounded, "error": error, "data": data,
    }



@app.get("/analysis/protection-generalisation-panel")
def metals_protection_generalisation_panel() -> Dict[str, Any]:
    """Read-only anti-curve-fit pullback panel across reconstructed Metals episodes."""
    try:
        with _read_conn() as conn:
            raw=conn.execute("""
              SELECT id,created_at_utc,side,open_count,basket_r,high_water_r,high_water_pnl_gbp,giveback_pct
              FROM metals_demo_basket_snapshots WHERE basket_key='METALS_BASKET'
              ORDER BY created_at_utc,id
            """).fetchall()
        seq=[dict(r) if isinstance(r,dict) else {} for r in raw]
        episodes=[]; cur=[]; prev_side=None; prev_open=0
        for x in seq:
            side=str(x.get("side") or "").upper(); oc=int(x.get("open_count") or 0)
            active=oc>0 and side not in ("","FLAT","MIXED")
            boundary=active and (prev_open==0 or (prev_side not in (None,"","FLAT","MIXED") and side!=prev_side))
            if boundary and cur: episodes.append(cur); cur=[]
            if active: cur.append(x)
            elif cur: episodes.append(cur); cur=[]
            prev_side,prev_open=side,oc
        if cur: episodes.append(cur)
        events=[]; thresholds=[25,40,50,60]
        for ep in episodes:
            if not ep: continue
            eid="METALS_"+str(ep[0].get("side"))+"_"+str(ep[0].get("created_at_utc"))
            seen=set()
            for i,x in enumerate(ep):
                hwm=float(x.get("high_water_r") or 0); br=float(x.get("basket_r") or 0)
                if hwm<10: continue
                gb=float(x.get("giveback_pct") or (100*(hwm-br)/hwm if hwm else 0))
                for t in thresholds:
                    if t in seen or gb<t: continue
                    seen.add(t); future=[float(y.get("basket_r") or 0) for y in ep[i:]]
                    events.append({"episode_id":eid,"side":x.get("side"),"event_at":_jsonable(x.get("created_at_utc")),
                      "hwm_r":hwm,"hwm_pnl_gbp":x.get("high_water_pnl_gbp"),"threshold_pct":t,
                      "observed_giveback_pct":gb,"basket_r":br,"open_count":x.get("open_count"),
                      "subsequent_max_r":max(future) if future else br,"subsequent_min_r":min(future) if future else br,
                      "subsequent_recovery_r":(max(future)-br) if future else 0,
                      "subsequent_new_hwm":(max(future)>hwm+1e-9) if future else False})
        return {"status":"ok","project":PROJECT_NAME,"analysis_interface_version":ANALYSIS_INTERFACE_VERSION,
          "app_version":VISIBLE_RELEASE_VERSION,"read_only_interface":True,"execution_authority":False,"time_utc":_now(),
          "study_version":"metals_protection_generalisation_v1","anti_curve_fit_design":{"minimum_hwm_r":10,
          "giveback_thresholds_pct":thresholds,"future_fields_are_labels_only":True,"thresholds_not_optimized":True},
          "events":events}
    except Exception as exc:
        return {"status":"error","project":PROJECT_NAME,"analysis_interface_version":ANALYSIS_INTERFACE_VERSION,
          "app_version":VISIBLE_RELEASE_VERSION,"read_only_interface":True,"execution_authority":False,"time_utc":_now(),
          "study_version":"metals_protection_generalisation_v1","events":[],"error":type(exc).__name__+": "+str(exc)}



@app.get("/analysis/adaptive-protection-context")
def metals_adaptive_protection_context(limit: int = 160) -> Dict[str, Any]:
    """Research-only state/repair ledger from canonical METALS_BASKET snapshots."""
    n=max(20,min(int(limit),250))
    try:
        with _read_conn() as conn:
            raw=conn.execute("""SELECT id,created_at_utc,side,open_count,basket_r,high_water_r,high_water_pnl_gbp,giveback_pct FROM metals_demo_basket_snapshots WHERE basket_key='METALS_BASKET' ORDER BY created_at_utc DESC,id DESC LIMIT ?""",(n,)).fetchall()
        rows=list(reversed([dict(r) if isinstance(r,dict) else {} for r in raw])); out=[]; prev=None; peak=None; prev_side=None
        for x in rows:
            side=str(x.get("side") or "").upper(); oc=int(x.get("open_count") or 0)
            if oc<=0 or side in ("","FLAT","MIXED"):
                prev=None; peak=None; prev_side=None; continue
            if prev_side is not None and side!=prev_side: prev=None; peak=None
            br=float(x.get("basket_r") or 0); persisted_h=float(x.get("high_water_r") or br); peak=max(float(peak if peak is not None else persisted_h),persisted_h,br)
            gb=(100*(peak-br)/peak) if peak>0 else 0; delta=(br-float(prev["basket_r"])) if prev else None
            out.append({"event_at":_jsonable(x.get("created_at_utc")),"side":side,"basket_r":br,"hwm_r":peak,"hwm_pnl_gbp":x.get("high_water_pnl_gbp"),"giveback_pct":gb,"open_count":oc,
              "delta_basket_r":delta,"repair_attempt":bool(delta is not None and delta>0 and br<peak),"state":{"giveback_accelerating":bool(prev and gb>prev["gb"]),"breadth_proxy_open_count":oc}})
            prev={"basket_r":br,"gb":gb}; prev_side=side
        return {"status":"ok","project":PROJECT_NAME,"analysis_interface_version":ANALYSIS_INTERFACE_VERSION,"app_version":VISIBLE_RELEASE_VERSION,"read_only_interface":True,"execution_authority":False,"time_utc":_now(),
          "study_version":"metals_adaptive_context_v2","canonical_snapshot_filter":"basket_key=METALS_BASKET","news_layer_included":False,"observations":out}
    except Exception as exc:
        return {"status":"error","project":PROJECT_NAME,"analysis_interface_version":ANALYSIS_INTERFACE_VERSION,"app_version":VISIBLE_RELEASE_VERSION,"observations":[],"error":type(exc).__name__+": "+str(exc)}


@app.get("/analysis/repair-quality-study")
def metals_repair_quality_study(limit: int = 250) -> Dict[str, Any]:
    """Consolidated deterioration/repair episodes from canonical aggregate snapshots."""
    try:
        obs=(metals_adaptive_protection_context(limit).get("observations") or []); episodes=[]; active=None; active_side=None
        for x in obs:
            side=x.get("side"); gb=float(x.get("giveback_pct") or 0); br=float(x.get("basket_r") or 0); h=float(x.get("hwm_r") or 0)
            if active is not None and side!=active_side:
                active["outcome"]="ENDED_ON_DIRECTION_CHANGE"; episodes.append(active); active=None
            if active is None:
                if h>=10 and gb>=20:
                    active={"side":side,"start_at":x.get("event_at"),"start_giveback_pct":gb,"start_r":br,"hwm_r":h,"worst_giveback_pct":gb,"best_repair_r":br,"repair_observations":0,"new_hwm_after_start":False,"crossed_60pct":gb>=60,"first_60pct_at":x.get("event_at") if gb>=60 else None}; active_side=side
                continue
            active["worst_giveback_pct"]=max(active["worst_giveback_pct"],gb)
            if br>active["best_repair_r"]: active["best_repair_r"]=br; active["repair_observations"]+=1
            if gb>=60 and not active["crossed_60pct"]: active["crossed_60pct"]=True; active["first_60pct_at"]=x.get("event_at")
            if gb<=0.5 and h>=active["hwm_r"]:
                active["new_hwm_after_start"]=True; active["end_at"]=x.get("event_at"); active["outcome"]="REPAIRED_TO_HWM"; denom=max(1e-9,active["hwm_r"]-active["start_r"]); active["repair_fraction_of_initial_loss"]=max(0.0,min(1.0,(active["best_repair_r"]-active["start_r"])/denom)); episodes.append(active); active=None
        if active:
            denom=max(1e-9,active["hwm_r"]-active["start_r"]); active["repair_fraction_of_initial_loss"]=max(0.0,min(1.0,(active["best_repair_r"]-active["start_r"])/denom)); active["outcome"]="OPEN_FAILED_REPAIR_PATH" if active["crossed_60pct"] else "OPEN_SEQUENCE"; episodes.append(active)
        return {"status":"ok","project":PROJECT_NAME,"analysis_interface_version":ANALYSIS_INTERFACE_VERSION,"app_version":VISIBLE_RELEASE_VERSION,"read_only_interface":True,"execution_authority":False,"time_utc":_now(),"study_version":"metals_repair_quality_v3","canonical_snapshot_filter":"basket_key=METALS_BASKET","minimum_hwm_r":10,"thresholds_are_descriptive_not_optimized":True,"episodes":episodes}
    except Exception as exc:
        return {"status":"error","error":type(exc).__name__+": "+str(exc),"episodes":[]}


@app.get("/analysis/catalog")
def analysis_catalog() -> Dict[str, Any]:
    """Compact research-table catalog: columns only, no row data."""
    inv = _table_inventory()
    tables = [t for t in inv.get("tables", []) if any(h in t.lower() for h in ANALYSIS_TABLE_HINTS)]
    catalog: Dict[str, Any] = {}
    try:
        with _read_conn() as conn:
            for table in tables:
                rows = conn.execute("""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=?
                    ORDER BY ordinal_position
                """, (table,)).fetchall()
                cols = []
                for row in rows:
                    if isinstance(row, dict):
                        cols.append({"name": row.get("column_name"), "type": row.get("data_type")})
                    else:
                        cols.append({"name": row[0], "type": row[1]})
                catalog[table] = cols
        status, error = "ok", None
    except Exception as exc:
        status, error = "degraded", f"{type(exc).__name__}: {exc}"
    return {
        "status": status,
        "project": PROJECT_NAME,
        "analysis_interface_version": ANALYSIS_INTERFACE_VERSION,
        "app_version": VISIBLE_RELEASE_VERSION,
        "read_only_interface": True,
        "execution_authority": False,
        "time_utc": _now(),
        "data": {"catalog": catalog, "error": error},
    }


@app.get("/analysis/schema")
def analysis_schema() -> Dict[str, Any]:
    inv = _table_inventory()
    return {
        "status": "ok" if inv.get("ok") else "degraded",
        "project": PROJECT_NAME,
        "analysis_interface_version": ANALYSIS_INTERFACE_VERSION,
        "app_version": VISIBLE_RELEASE_VERSION,
        "read_only_interface": True,
        "execution_authority": False,
        "time_utc": _now(),
        "data": inv,
    }


@app.get("/analysis/summary")
def analysis_summary() -> Dict[str, Any]:
    inv = _table_inventory()
    tables = inv.get("tables", [])
    # Compact counts only: enough to map the live research dataset without
    # leaking credentials or shipping a giant database dump.
    counts = {t: _safe_table_count(t) for t in tables}
    return {
        "status": "ok" if inv.get("ok") else "degraded",
        "project": PROJECT_NAME,
        "analysis_interface_version": ANALYSIS_INTERFACE_VERSION,
        "app_version": VISIBLE_RELEASE_VERSION,
        "read_only_interface": True,
        "execution_authority": False,
        "time_utc": _now(),
        "data": {"table_counts": counts},
    }


@app.get("/analysis/performance")
def analysis_performance() -> Dict[str, Any]:
    """Canonical read-only performance data for automated reviews."""
    top = core.metals_standard_top_snapshot(force=False)
    account = top.get("account") or {}; accounting = top.get("accounting") or {}; strategy = top.get("strategy") or {}
    return {"status":"ok","project":PROJECT_NAME,"contract_version":1,"read_only_interface":True,"execution_authority":False,"time_utc":_now(),"mode":"live","live_capital":True,"scope":"XAUUSD LONG live lane; practice lanes excluded from live totals","realised":{"week_gbp":accounting.get("week_pnl"),"month_gbp":accounting.get("month_pnl")},"open":{"unrealised_gbp":strategy.get("headline_pnl"),"basket_r":strategy.get("basket_r"),"open_trades":strategy.get("open_trades")},"nav_gbp":account.get("nav")}


@app.get("/analysis/status")
def analysis_status() -> Dict[str, Any]:
    return {
        "status": "ok",
        "project": PROJECT_NAME,
        "analysis_interface_version": ANALYSIS_INTERFACE_VERSION,
        "app_name": _safe_attr("APP_NAME"),
        "app_version": VISIBLE_RELEASE_VERSION,
        "policy_version": _safe_attr("POLICY_VERSION"),
        "environment": os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("OANDA_ENV") or os.getenv("METALS_DEMO_OANDA_ENV"),
        "read_only_interface": True,
        "execution_authority": False,
        "time_utc": _now(),
        "operational_health": {"status": "ok", "checks": _health_snapshot()},
        "data": {
            "producer_contract": "analysis-v2",
            "rich_analysis": "schema-and-summary-ready",
        },
    }


@app.get("/analysis/quality")
def analysis_quality() -> Dict[str, Any]:
    checks = _health_snapshot()
    return {
        "status": "ok" if checks["runtime_loaded"] and checks["database_configured"] else "degraded",
        "project": PROJECT_NAME,
        "read_only_interface": True,
        "execution_authority": False,
        "time_utc": _now(),
        "checks": checks,
    }


def _rewrite_dashboard_version(body: bytes, content_type: str) -> bytes:
    """Keep the legacy dashboard intact while making this release visible."""
    if "text/html" not in (content_type or "").lower():
        return body
    try:
        text = body.decode("utf-8")
        current = _safe_attr("METALS_APP_VERSION") or _safe_attr("BUILD_VERSION") or _safe_attr("APP_VERSION")
        if current and str(current) != VISIBLE_RELEASE_VERSION:
            text = text.replace(str(current), VISIBLE_RELEASE_VERSION)
        return text.encode("utf-8")
    except Exception:
        return body


async def _dashboard_passthrough(request: Request, path: str) -> Response:
    scope = dict(request.scope)
    scope["path"] = path
    scope["raw_path"] = path.encode("utf-8")
    messages = []
    async def receive():
        return await request.receive()
    async def send(message):
        messages.append(message)
    await core.app(scope, receive, send)
    start = next((m for m in messages if m["type"] == "http.response.start"), None)
    chunks = [m.get("body", b"") for m in messages if m["type"] == "http.response.body"]
    if not start:
        return Response(status_code=500)
    headers = dict(start.get("headers", []))
    content_type = headers.get(b"content-type", b"").decode("latin-1")
    body = _rewrite_dashboard_version(b"".join(chunks), content_type)
    out_headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in start.get("headers", []) if k.lower() not in (b"content-length", b"content-encoding")}
    return Response(content=body, status_code=start["status"], headers=out_headers, media_type=None)


@app.get("/")
async def visible_root(request: Request):
    return await _dashboard_passthrough(request, "/")


@app.get("/dashboard")
async def visible_dashboard(request: Request):
    return await _dashboard_passthrough(request, "/dashboard")


@app.get("/dashboard/top")
async def visible_dashboard_top(request: Request):
    return await _dashboard_passthrough(request, "/dashboard/top")


# Catch-all mount must remain last so the explicit /analysis/* routes above win.
app.mount("/", core.app)
