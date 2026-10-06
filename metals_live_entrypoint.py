import app_postgres_runtime as core
import xau_short_live_override

XAU_SHORT_LIVE_OVERRIDE_STATUS = xau_short_live_override.install(core)
app = core.app
