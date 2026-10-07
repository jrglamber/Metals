"""Production wrapper enabling intrahour ATR2/MFE25 XAU protection."""
from __future__ import annotations

# The dashboard-v2 presentation layer still contains the one-shot reset call that
# was used during the live/demo separation migration. That migration is complete;
# repeated process starts must never rebase the live XAU HWM. Suppress only that
# import-time call, then restore the real reset function for explicit/manual use.
import metals_live_basket_entrypoint as basket_base

_real_reset = basket_base._reset_live_xau_protection_cycle

def _retired_startup_reset(reset_id: str):
    return {
        "ok": True,
        "skipped": True,
        "reason": "one_shot_dashboard_reset_retired",
        "reset_id": reset_id,
    }

basket_base._reset_live_xau_protection_cycle = _retired_startup_reset
try:
    import metals_live_dashboard_v2 as base
finally:
    basket_base._reset_live_xau_protection_cycle = _real_reset

import metals_intrahour_exit_override as intrahour
import metals_intrahour_force_diag as force_diag

INTRAHOUR_EXIT_STATUS = intrahour.install(base.core, base.app)
force_diag.run(base.core)
app = base.app
