"""Safe runtime probe for live-XAU risk sizing authority.

Read-only: no broker action, no variable mutation, no credentials emitted.
Used to identify the authoritative risk-control/sizing path before changing live risk.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Dict

VERSION = "metals_xau_risk_probe_2026_10_14_v2"
SAFE_PREVIEW_KEYS = (
    "risk_amount", "requested_risk_gbp", "estimated_risk_gbp", "estimated_risk_overage_pct",
    "units", "entry_price", "stop_price", "sl_pct", "executable", "blocking_reasons",
)
SAFE_RISK_KEYS = (
    "status", "strategy", "fixed_risk_pct_of_nav", "nav_gbp", "raw_calculated_risk_gbp",
    "calculated_risk_per_trade_gbp", "current_risk_per_trade_gbp", "minimum_trade_size",
    "estimated_risk_gbp", "effective_risk_pct_of_nav", "next_review_due_utc", "note", "error",
)


def _safe_preview(core: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        raw = dict(core.metals_xau_live_sizing_preview() or {})
        for key in SAFE_PREVIEW_KEYS:
            if key in raw:
                out[key] = raw.get(key)
    except Exception as exc:
        out = {"error": f"{type(exc).__name__}:{exc}"}
    return out


def _source_summary(fn: Any) -> Dict[str, Any]:
    try:
        src = inspect.getsource(fn)
    except Exception as exc:
        return {"name": getattr(fn, "__name__", None), "source_error": f"{type(exc).__name__}:{exc}"}
    keep = []
    for line in src.splitlines():
        low = line.lower()
        if any(token in low for token in ("risk", "sizing", "nav", "balance", "units", "control", "fixed", "override")):
            keep.append(line.strip())
    return {"name": getattr(fn, "__name__", None), "relevant_source_lines": keep[:120]}


def _route_authority(endpoint: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "endpoint": getattr(endpoint, "__name__", None),
        "module": getattr(endpoint, "__module__", None),
        "source": _source_summary(endpoint) if callable(endpoint) else {},
    }
    glb = getattr(endpoint, "__globals__", {}) if callable(endpoint) else {}
    for key in ("METALS_XAU_FIXED_RISK_PCT_OF_NAV", "_METALS_XAU_RISK_OVERRIDE_KEY"):
        if key in glb:
            value = glb.get(key)
            # Both are non-secret policy/config identifiers or numeric policy values.
            row[key] = value
    snapshot_fn = glb.get("_metals_fixed_risk_snapshot")
    if callable(snapshot_fn):
        row["snapshot_function"] = _source_summary(snapshot_fn)
        try:
            snap = dict(snapshot_fn() or {})
            row["snapshot"] = {k: snap.get(k) for k in SAFE_RISK_KEYS if k in snap}
        except Exception as exc:
            row["snapshot"] = {"error": f"{type(exc).__name__}:{exc}"}
    return row


def install(core: Any, app: Any) -> Dict[str, Any]:
    route_rows = []
    for route in list(getattr(app, "routes", [])):
        path = str(getattr(route, "path", ""))
        if "risk-per-trade" not in path:
            continue
        endpoint = getattr(route, "endpoint", None)
        row = {
            "path": path,
            "methods": sorted(list(getattr(route, "methods", []) or [])),
        }
        row.update(_route_authority(endpoint))
        route_rows.append(row)
    status = {
        "installed": True,
        "version": VERSION,
        "read_only": True,
        "execution_authority": False,
        "preview": _safe_preview(core),
        "sizing_function": _source_summary(getattr(core, "metals_xau_live_sizing_preview", None)),
        "risk_control_routes": route_rows,
        "candidate_constant_names": sorted([
            name for name in dir(core)
            if ("RISK" in name.upper() or "SIZ" in name.upper()) and "TOKEN" not in name.upper() and "SECRET" not in name.upper()
        ])[:200],
    }
    print("METALS_XAU_RISK_PROBE " + json.dumps(status, default=str, separators=(",", ":")), flush=True)

    @app.get("/analysis/risk-sizing-probe")
    def risk_sizing_probe() -> Dict[str, Any]:
        fresh = dict(status)
        fresh["preview"] = _safe_preview(core)
        return fresh

    return status
