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
ANALYSIS_INTERFACE_VERSION = "2.4.0"
VISIBLE_RELEASE_VERSION = "v1.6.45"
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


@app.get("/analysis/status")
def analysis_status() -> Dict[str, Any]:
    return {
        "status": "ok",
        "project": PROJECT_NAME,
        "analysis_interface_version": ANALYSIS_INTERFACE_VERSION,
        "app_name": _safe_attr("APP_NAME"),
        "app_version": _safe_attr("APP_VERSION") or _safe_attr("METALS_APP_VERSION") or _safe_attr("BUILD_VERSION") or VISIBLE_RELEASE_VERSION,
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
