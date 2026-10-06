import app_postgres_runtime as core
import broker_source_probe as source_probe
import candidate_line_probe

source_probe.install(core)
candidate_line_probe.emit(core)
app = core.app
