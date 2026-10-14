"""Project Exit Plan Metals production governance entrypoint."""
from __future__ import annotations

import metals_intrahour_entrypoint as base
import metals_strategy_governance_override as governance
import metals_adaptive_stacking_override as adaptive_stacking
import metals_harvest_ladder_override_v3 as harvest_ladder
import metals_adaptive_minimum_size_override as broker_safe_overflow
import metals_xau_risk_target_override as risk_target
import metals_short_candidate_side_fix as short_side_fix

GOVERNANCE_STATUS = governance.install(base.base.core, base.app)
ADAPTIVE_STACKING_STATUS = adaptive_stacking.install(base.base.core, base.app)
HARVEST_LADDER_STATUS = harvest_ladder.install(base.base.core, base.app)
BROKER_SAFE_OVERFLOW_STATUS = broker_safe_overflow.install(base.base.core, base.app)
RISK_TARGET_STATUS = risk_target.install(base.base.core, base.app)
SHORT_SIDE_FIX_STATUS = short_side_fix.install(base.base.core, base.app)
GOVERNANCE_STATUS = dict(GOVERNANCE_STATUS or {})
GOVERNANCE_STATUS["live_xau_effective_stacking_policy"] = "ADAPTIVE_20_PLUS_2R_HALF_EXPOSURE_AFTER_32_NO_STRATEGIC_CAP"
GOVERNANCE_STATUS["live_xau_cap_24_superseded"] = True
GOVERNANCE_STATUS["live_xau_adaptive_version"] = ADAPTIVE_STACKING_STATUS.get("version")
GOVERNANCE_STATUS["live_xau_overflow_execution_mode"] = BROKER_SAFE_OVERFLOW_STATUS.get("mode")
GOVERNANCE_STATUS["live_xau_overflow_broker_safe_version"] = BROKER_SAFE_OVERFLOW_STATUS.get("version")
GOVERNANCE_STATUS["live_xau_harvest_policy"] = "30R_FIRST_THEN_PLUS_50R_20_20_25"
GOVERNANCE_STATUS["live_xau_harvest_version"] = HARVEST_LADDER_STATUS.get("version")
GOVERNANCE_STATUS["live_xau_base_risk_pct_of_nav"] = RISK_TARGET_STATUS.get("base_risk_pct_of_nav")
GOVERNANCE_STATUS["live_xau_overflow_after_32_average_pct_of_nav"] = RISK_TARGET_STATUS.get("overflow_after_trade_32_pct_of_nav")
GOVERNANCE_STATUS["live_xau_risk_target_version"] = RISK_TARGET_STATUS.get("version")
GOVERNANCE_STATUS["short_candidate_side_fix_version"] = SHORT_SIDE_FIX_STATUS.get("version")
print("METALS_STRATEGY_GOVERNANCE", GOVERNANCE_STATUS, flush=True)
print("METALS_XAU_ADAPTIVE_STACKING", ADAPTIVE_STACKING_STATUS, flush=True)
print("METALS_XAU_BROKER_SAFE_OVERFLOW", BROKER_SAFE_OVERFLOW_STATUS, flush=True)
print("METALS_XAU_HARVEST_V3", HARVEST_LADDER_STATUS, flush=True)
print("METALS_XAU_RISK_TARGET", RISK_TARGET_STATUS, flush=True)
print("METALS_SHORT_CANDIDATE_SIDE_FIX", SHORT_SIDE_FIX_STATUS, flush=True)
app = base.app