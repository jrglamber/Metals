"""Temporary wrapper around the unified Metals app for read-only XAU short diagnostics."""
from __future__ import annotations

import metals_unified_entrypoint as base
import xau_short_runtime_diagnostic as diag

@base.app.on_event("startup")
def _xau_short_runtime_diagnostic_once() -> None:
    try:
        diag.run(base.analysis.core)
    except Exception as exc:
        print(f"XAU_SHORT_RUNTIME_DIAGNOSTIC_ERROR {type(exc).__name__}: {exc}", flush=True)


# analysis_entrypoint already mounts core.app at "/" as a catch-all. Register
# this temporary route on the core app so it remains reachable through that mount.
@base.analysis.core.app.post("/analysis/xau-short-runtime-diagnostic")
def xau_short_runtime_diagnostic_route():
    return diag.run(base.analysis.core)


app = base.app
