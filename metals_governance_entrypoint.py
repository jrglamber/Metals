"""Project Exit Plan Metals governance wrapper.

Phase 1 is deliberately read-only: load the existing production stack unchanged,
then emit execution call signatures before the approved strategy-governance
patch is installed in a follow-up commit.
"""
from __future__ import annotations

import metals_intrahour_entrypoint as base
import metals_governance_probe

GOVERNANCE_PROBE_STATUS = metals_governance_probe.install(base.base.core)
app = base.app
