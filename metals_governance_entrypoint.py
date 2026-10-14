"""Project Exit Plan Metals production governance entrypoint."""
from __future__ import annotations

import metals_intrahour_entrypoint as base
import metals_strategy_governance_override as governance
import metals_adaptive_stacking_override as adaptive_stacking
import metals_harvest_ladder_override as harvest_ladder

GOVERNANCE_STATUS = governance.install(base.base.core, base.app)
ADAPTIVE_STACKING_STATUS = adaptive_stacking.install(base.base.core, base.app)
HARVEST_LADDER_STATUS = harvest_ladder.install(base.base.core, base.app)
GOVERNANCE_STATUS = dict(GOVERNANCE_STATUS or {})
GOVERNANCE_STATUS["live_xau_effective_stacking_policy"] = "ADAPTIVE_20_PLUS_2R_HALF_AFTER_32_NO_STRATEGIC_CAP"
GOVERNANCE_STATUS["live_xau_cap_24_superseded"] = True
GOVERNANCE_STATUS["live_xau_adaptive_version"] = ADAPTIVE_STACKING_STATUS.get("version")
GOVERNANCE_STATUS["live_xau_harvest_policy"] = "30R_FIRST_THEN_PLUS_50R_20_20_25"
GOVERNANCE_STATUS["live_xau_harvest_version"] = HARVEST_LADDER_STATUS.get("version")
ADAPTIVE_STACKING_STATUS = dict(ADAPTIVE_STACKING_STATUS or {})
ADAPTIVE_STACKING_STATUS["harvest_policy_post_install"] = {
    "first_r": HARVEST_LADDER_STATUS.get("first_level_r"),
    "step_r": HARVEST_LADDER_STATUS.get("step_r"),
    "fractions": HARVEST_LADDER_STATUS.get("fractions"),
    "version": HARVEST_LADDER_STATUS.get("version"),
}
print("METALS_STRATEGY_GOVERNANCE", GOVERNANCE_STATUS, flush=True)
print("METALS_XAU_ADAPTIVE_STACKING", ADAPTIVE_STACKING_STATUS, flush=True)
print("METALS_XAU_HARVEST_V2", HARVEST_LADDER_STATUS, flush=True)
app = base.app
