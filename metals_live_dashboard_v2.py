"""Compatibility loader for the complete Metals dashboard v2 implementation.

The observability edit that previously replaced this module was too broad and
removed dashboard presentation functionality. Keep this production import path
stable by loading the complete pre-observability implementation verbatim from
its preserved recovery module. Observability is installed separately by the
production entrypoint so it cannot replace dashboard behaviour.
"""
from metals_live_dashboard_v2_recovery_20261010 import *  # noqa: F401,F403
