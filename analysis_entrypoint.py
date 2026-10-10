"""Compatibility shim restoring the known-good analysis entrypoint.

The previous reporting-only edit to this path was malformed. Keep the default
branch safe by loading the complete known-good implementation from the recovery
module. Trading authority remains in the existing production wrappers.
"""
from analysis_entrypoint_recovery_20261010 import *  # noqa: F401,F403
