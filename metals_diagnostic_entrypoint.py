"""Temporary wrapper around the unified Metals app for read-only XAU short diagnostics."""
from __future__ import annotations

import metals_unified_entrypoint as base
import xau_short_runtime_diagnostic

@base.app.on_event("startup")
def _xau_short_runtime_diagnostic_once() -> None:
    try:
        xau_short_runtime_diagnostic.run(base.analysis.core)
    except Exception as exc:
        print(f"XAU_SHORT_RUNTIME_DIAGNOSTIC_ERROR {type(exc).__name__}: {exc}", flush=True)

app = base.app
