"""Metals production wrapper with UK-day realised P&L telemetry.

Read-only accounting adapter layered on the existing production analysis wrapper.
No execution, sizing, entry, exit, stop, harvest or research authority changes.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict
from zoneinfo import ZoneInfo

import analysis_entrypoint as base

app = base.app
core = base.core
VERSION = "metals_daily_realised_v1_2026_10_23"
_original_summary = core.aggregate_portfolio_summary


def _today_realised() -> float:
    now_local = datetime.now(ZoneInfo("Europe/London"))
    start_utc = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    end_utc = now_local.astimezone(timezone.utc).isoformat()
    with core.get_conn() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(net_realized_gbp),0) AS realised_today_gbp
            FROM metals_xau_live_broker_transactions
            WHERE transaction_time>=? AND transaction_time<?
            """,
            (start_utc, end_utc),
        ).fetchone()
    if isinstance(row, dict):
        value = row.get("realised_today_gbp")
    else:
        try:
            value = row[0] if row else 0.0
        except Exception:
            value = 0.0
    return float(core.safe_float(value) or 0.0)


def daily_portfolio_summary() -> Dict[str, Any]:
    payload = copy.deepcopy(
        _original_summary(x_aggregate_secret=(getattr(core, "AGGREGATE_SOURCE_SECRET", "") or None))
    )
    accounting = payload.setdefault("accounting", {})
    try:
        accounting["realised_today_gbp"] = round(_today_realised(), 2)
        accounting["realised_today_basis"] = "LIVE_XAU_BROKER_TRANSACTIONS_EUROPE_LONDON_DAY"
        accounting.pop("realised_today_error", None)
    except Exception as exc:
        accounting["realised_today_gbp"] = None
        accounting["realised_today_error"] = f"{type(exc).__name__}: {exc}"
    payload["daily_accounting_adapter"] = VERSION
    return payload


def _install() -> None:
    app.add_api_route(
        "/api/portfolio-summary",
        daily_portfolio_summary,
        methods=["GET"],
        name="metals_daily_portfolio_summary",
        include_in_schema=False,
    )
    route = next(
        (r for r in reversed(list(getattr(app, "routes", [])))
         if str(getattr(r, "path", "")) == "/api/portfolio-summary"
         and getattr(r, "endpoint", None) is daily_portfolio_summary),
        None,
    )
    if route is None:
        raise RuntimeError("Metals daily portfolio-summary route failed to register")
    app.routes.remove(route)
    app.routes.insert(0, route)


_install()
print(f"METALS_DAILY_REALISED installed=True version={VERSION} execution_authority=False", flush=True)
