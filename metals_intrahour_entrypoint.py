"""Production wrapper enabling intrahour ATR2/MFE25 XAU protection."""
from __future__ import annotations

import metals_live_dashboard_v2 as base
import metals_intrahour_exit_override as intrahour
import metals_intrahour_runtime_diag as intrahour_diag

INTRAHOUR_EXIT_STATUS = intrahour.install(base.core, base.app)
intrahour_diag.run(base.core)

app = base.app
