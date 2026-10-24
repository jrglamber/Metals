"""Metals production daily-accounting app plus zero-authority monetisation research."""
import metals_daily_summary_entrypoint as base
import metals_monetisation_research_v1 as research

app = base.app
STATUS = research.install(base.core, app)

print(
    "METALS_RESEARCH_DAILY_ENTRYPOINT installed=True "
    f"research_version={research.VERSION} execution_authority=False",
    flush=True,
)
