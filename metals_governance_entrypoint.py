"""Project Exit Plan Metals production governance entrypoint."""
from __future__ import annotations

import metals_intrahour_entrypoint as base
import metals_strategy_governance_override as governance
import metals_adaptive_stacking_override as adaptive_stacking

GOVERNANCE_STATUS = governance.install(base.base.core, base.app)
ADAPTIVE_STACKING_STATUS = adaptive_stacking.install(base.base.core, base.app)
GOVERNANCE_STATUS = dict(GOVERNANCE_STATUS or {})
GOVERNANCE_STATUS["live_xau_effective_stacking_policy"] = "ADAPTIVE_20_PLUS_2R_HALF_AFTER_32_NO_STRATEGIC_CAP"
GOVERNANCE_STATUS["live_xau_cap_24_superseded"] = True
GOVERNANCE_STATUS["live_xau_adaptive_version"] = ADAPTIVE_STACKING_STATUS.get("version")
print("METALS_STRATEGY_GOVERNANCE", GOVERNANCE_STATUS, flush=True)
print("METALS_XAU_ADAPTIVE_STACKING", ADAPTIVE_STACKING_STATUS, flush=True)
app = base.app
