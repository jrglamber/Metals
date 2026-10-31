"""Metals production entrypoint + broker-backed live execution telemetry.

The XAG SHORT executor is installed fail-closed. New XAG entries require its
separate promotion/ready/armed gates; management of already-open XAG trades is
kept separate from the new-entry arm so disarming entries cannot strand risk.
"""
from __future__ import annotations

from typing import Any

import metals_governance_entrypoint as base
import portfolio_live_risk as live_risk
import metals_execution_contract_20261031 as execution_contract
import xag_short_live_probe as xag_live_probe
import xag_short_live_override as xag_live_execution

app = base.app
core = base.base.base.core
VERSION = "metals_live_risk_entrypoint_v7_2026_10_31"

XAG_SHORT_LIVE_INSTALL = xag_live_execution.install(core, app)
EXECUTION_CONTRACT_STATUS = execution_contract.install(core, app)


def _portfolio_risk_endpoint():
    return live_risk._read("metals")


def _live_xau_trade_tile_endpoint():
    snapshot = getattr(core, "live_xau_trade_tile_snapshot", None)
    if not callable(snapshot):
        raise RuntimeError("live_xau_trade_tile_snapshot unavailable")
    return snapshot()


def _xag_short_live_sizing_endpoint():
    return xag_live_probe.snapshot(core)


def _route_matches(route: Any, path: str) -> bool:
    return (
        str(getattr(route, "path", "")) == path
        and "GET" in set(getattr(route, "methods", []) or [])
    )


def _ensure_front_route(path: str, endpoint: Any, name: str) -> dict:
    routes = getattr(getattr(app, "router", None), "routes", None)
    if routes is None:
        raise RuntimeError(f"Metals route table unavailable for {path}")

    matches = [r for r in list(routes) if _route_matches(r, path)]
    if matches:
        route = matches[-1]
    else:
        app.add_api_route(path, endpoint, methods=["GET"], include_in_schema=False, name=name)
        matches = [r for r in list(routes) if _route_matches(r, path)]
        if not matches:
            raise RuntimeError(f"Metals {path} route failed to register")
        route = matches[-1]

    try:
        routes.remove(route)
        routes.insert(0, route)
    except Exception as exc:
        raise RuntimeError(f"Metals {path} route precedence failed: {type(exc).__name__}: {exc}")

    return {"registered": True, "route_index": 0, "duplicates": len(matches)}


RISK_ROUTE = _ensure_front_route(
    "/api/portfolio-risk",
    _portfolio_risk_endpoint,
    "portfolio_risk_guard",
)
TRADE_TILE_ROUTE = _ensure_front_route(
    "/api/live-xau-trade-tile",
    _live_xau_trade_tile_endpoint,
    "live_xau_trade_tile_guard",
)
XAG_SIZING_PROBE_ROUTE = _ensure_front_route(
    "/api/xag-short-live-sizing-probe",
    _xag_short_live_sizing_endpoint,
    "xag_short_live_sizing_probe_guard",
)

LIVE_RISK_INSTALL = {
    "installed": True,
    "version": live_risk.VERSION,
    "entrypoint_version": VERSION,
    "mode": "metals",
    "risk_telemetry_execution_authority": False,
    "route_registered": bool(RISK_ROUTE.get("registered")),
    "route_index": RISK_ROUTE.get("route_index"),
    "live_xau_trade_tile_registered": bool(TRADE_TILE_ROUTE.get("registered")),
    "live_xau_trade_tile_route_index": TRADE_TILE_ROUTE.get("route_index"),
    "xag_short_live_sizing_probe_registered": bool(XAG_SIZING_PROBE_ROUTE.get("registered")),
    "xag_short_live_sizing_probe_route_index": XAG_SIZING_PROBE_ROUTE.get("route_index"),
    "xag_short_live_sizing_probe_execution_authority": False,
    "xag_short_live_executor_installed": bool(XAG_SHORT_LIVE_INSTALL.get("installed")),
    "xag_short_live_executor_version": XAG_SHORT_LIVE_INSTALL.get("version"),
    "xag_short_live_executor_fail_closed": bool(XAG_SHORT_LIVE_INSTALL.get("fail_closed")),
    "xag_short_live_executor_execution_authority": bool(XAG_SHORT_LIVE_INSTALL.get("execution_authority")),
    "execution_contract_version": EXECUTION_CONTRACT_STATUS.get("version"),
    "xau_short_reporting_explicit": True,
}

try:
    _probe = _live_xau_trade_tile_endpoint()
    print(
        "METALS_LIVE_XAU_TILE_PROBE "
        f"ok={bool(_probe.get('ok'))} "
        f"open_count={_probe.get('open_count')} "
        f"long_count={_probe.get('long_count')} "
        f"short_count={_probe.get('short_count')} "
        f"last_opened_at_utc={_probe.get('last_opened_at_utc')}",
        flush=True,
    )
except Exception as exc:
    print(f"METALS_LIVE_XAU_TILE_PROBE ok=False error={type(exc).__name__}: {exc}", flush=True)

try:
    _xag = _xag_short_live_sizing_endpoint()
    print(
        "METALS_XAG_SHORT_LIVE_SIZING_PROBE "
        f"ok={bool(_xag.get('ok'))} "
        f"currency={_xag.get('oanda_account_currency')} "
        f"price_status={_xag.get('price_status')} "
        f"target_risk_gbp={_xag.get('target_risk_gbp')} "
        f"sl_pct={_xag.get('sl_pct')} "
        f"minimum_trade_size={_xag.get('minimum_trade_size')} "
        f"trade_units_precision={_xag.get('trade_units_precision')} "
        f"raw_target_units={_xag.get('raw_target_units')} "
        f"recommended_units={_xag.get('recommended_units')} "
        f"minimum_size_effective_risk_gbp={_xag.get('minimum_size_effective_risk_gbp')} "
        f"recommended_effective_risk_gbp={_xag.get('recommended_effective_risk_gbp')} "
        f"risk_overage_pct={_xag.get('risk_overage_pct')} "
        f"estimated_margin_gbp={_xag.get('estimated_margin_gbp')} "
        f"blocking_reasons={_xag.get('blocking_reasons')}",
        flush=True,
    )
except Exception as exc:
    print(f"METALS_XAG_SHORT_LIVE_SIZING_PROBE ok=False error={type(exc).__name__}: {exc}", flush=True)

try:
    _xag_runtime = core.xag_short_live_runtime_status()
    print(
        "METALS_XAG_SHORT_LIVE_EXECUTOR "
        f"installed={bool(_xag_runtime.get('installed'))} "
        f"tables_ok={bool(_xag_runtime.get('tables_ok'))} "
        f"promotion_enabled={bool(_xag_runtime.get('promotion_enabled'))} "
        f"executor_ready={bool(_xag_runtime.get('executor_ready'))} "
        f"execution_armed={bool(_xag_runtime.get('execution_armed'))} "
        f"orders_allowed={bool(_xag_runtime.get('orders_allowed'))} "
        f"management_writes_allowed={bool(_xag_runtime.get('management_writes_allowed'))} "
        f"pilot_units={_xag_runtime.get('pilot_units')} "
        f"target_risk_gbp={_xag_runtime.get('target_risk_gbp')} "
        f"sl_pct={_xag_runtime.get('sl_pct')} "
        f"active_exit_policy={_xag_runtime.get('active_exit_policy')} "
        f"open_count={_xag_runtime.get('open_live_xag_short_count')} "
        f"safe_to_mark_executor_ready={bool(_xag_runtime.get('safe_to_mark_executor_ready'))}",
        flush=True,
    )
except Exception as exc:
    print(f"METALS_XAG_SHORT_LIVE_EXECUTOR installed=False error={type(exc).__name__}: {exc}", flush=True)

print("METALS_EXECUTION_CONTRACT", EXECUTION_CONTRACT_STATUS, flush=True)
print("METALS_LIVE_RISK_TELEMETRY", LIVE_RISK_INSTALL, flush=True)
