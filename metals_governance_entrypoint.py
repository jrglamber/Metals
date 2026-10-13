"""Project Exit Plan Metals production governance entrypoint."""
from __future__ import annotations

import metals_intrahour_entrypoint as base
import metals_strategy_governance_override as governance

GOVERNANCE_STATUS = governance.install(base.base.core, base.app)
print("METALS_STRATEGY_GOVERNANCE", GOVERNANCE_STATUS, flush=True)
app = base.app
