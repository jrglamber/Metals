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
ANALYSIS_INTERFACE_VERSION = "2.1.0"
VISIBLE_RELEASE_VERSION = "v1.6.42"
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
