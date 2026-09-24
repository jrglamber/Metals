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
from fastapi import FastAPI

# Keep a stable outer app: the core runtime may rebuild/rebind its FastAPI
# object during import/startup. Analysis routes live on this wrapper and the
# unchanged production application is mounted only after those routes exist.
app = FastAPI(title="Project Exit Plan — Analysis Wrapper")
ANALYSIS_INTERFACE_VERSION = "1.1.0"
VISIBLE_RELEASE_VERSION = "v1.6.39"
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
            "producer_contract": "status-v1",
            "rich_analysis": "pending-v2",
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


# Catch-all mount must remain last so the explicit /analysis/* routes above win.
app.mount("/", core.app)
