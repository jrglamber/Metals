"""Production Metals entrypoint with explicit LIVE XAU long+short basket protection.

Dashboard layout remains provided by metals_dashboard_clean_entrypoint. This
layer only installs the fail-closed live basket-manager/harvest scope adapter.
"""
from __future__ import annotations

import metals_dashboard_clean_entrypoint as base
import xau_live_basket_manager_override

LIVE_XAU_BASKET_MANAGER_STATUS = xau_live_basket_manager_override.install(base.core)

app = base.app
