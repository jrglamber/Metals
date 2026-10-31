"""Metals production entrypoint + read-only broker-backed risk telemetry.

Final-route guards are presentation/telemetry only. They do not alter entry,
exit, sizing, harvest, broker execution or strategy state.
"""
from __future__ import annotations

from typing import Any

import metals_governance_entrypoint as base
import portfolio_live_risk as live_risk
import metals_execution_contract_20261031 as execution_contract
import xag_short_live_probe as xag_live_probe

app = base.app
core = base.base.base.core
VERSION = "metals_live_risk_entrypoint_v5_2026_10_31"

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
    "execution_authority": False,
    "route_registered": bool(RISK_ROUTE.get("registered")),
    "route_index": RISK_ROUTE.get("route_index"),
    "live_xau_trade_tile_registered": bool(TRADE_TILE_ROUTE.get("registered")),
    "live_xau_trade_tile_route_index": TRADE_TILE_ROUTE.get("route_index"),
    "xag_short_live_sizing_probe_registered": bool(XAG_SIZING_PROBE_ROUTE.get("registered")),
    "xag_short_live_sizing_probe_route_index": XAG_SIZING_PROBE_ROUTE.get("route_index"),
    "xag_short_live_sizing_probe_execution_authority": False,
    "execution_contract_version": EXECUTION_CONTRACT_STATUS.get("version"),
    "xau_short_reporting_explicit": True,
    "xag_short_promotion_staged_not_armed": True,
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

print("METALS_EXECUTION_CONTRACT", EXECUTION_CONTRACT_STATUS, flush=True)
print("METALS_LIVE_RISK_TELEMETRY", LIVE_RISK_INSTALL, flush=True)
