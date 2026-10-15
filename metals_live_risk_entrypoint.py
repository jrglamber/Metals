"""Metals production entrypoint + read-only broker-backed risk telemetry."""
from __future__ import annotations
import metals_governance_entrypoint as base
import portfolio_live_risk as live_risk
app = base.app
LIVE_RISK_INSTALL = live_risk.install(app, "metals")
try:
    print("METALS_LIVE_RISK_TELEMETRY", LIVE_RISK_INSTALL, flush=True)
except Exception:
    pass
