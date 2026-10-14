"""Safe runtime probe for live-XAU risk sizing authority.

Read-only: no broker action, no variable mutation, no credentials emitted.
Used to identify the authoritative risk-control/sizing path before changing live risk.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Dict

VERSION = "metals_xau_risk_probe_2026_10_14"
SAFE_PREVIEW_KEYS = (
    "risk_amount", "requested_risk_gbp", "estimated_risk_gbp", "estimated_risk_overage_pct",
    "units", "entry_price", "stop_price", "sl_pct", "executable", "blocking_reasons",
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
    # No runtime values are emitted. Source text is restricted to lines relevant to risk sizing.
    keep = []
    for line in src.splitlines():
        low = line.lower()
        if any(token in low for token in ("risk", "sizing", "nav", "balance", "units", "control")):
            keep.append(line.strip())
    return {"name": getattr(fn, "__name__", None), "relevant_source_lines": keep[:80]}


def install(core: Any, app: Any) -> Dict[str, Any]:
    route_rows = []
    for route in list(getattr(app, "routes", [])):
        path = str(getattr(route, "path", ""))
        if "risk-per-trade" not in path:
            continue
        endpoint = getattr(route, "endpoint", None)
        route_rows.append({
            "path": path,
            "methods": sorted(list(getattr(route, "methods", []) or [])),
            "endpoint": getattr(endpoint, "__name__", None),
            "source": _source_summary(endpoint) if callable(endpoint) else {},
        })
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
