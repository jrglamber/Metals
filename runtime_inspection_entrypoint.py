import app_postgres_runtime as core
import broker_source_probe as source_probe

source_probe.install(core)
app = core.app
