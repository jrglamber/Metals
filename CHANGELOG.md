# Changelog

## v1.0.0 — Standalone Metals Project

Created from the combined Project Exit Plan v10.1.27 indices + metals runtime.

Major architectural change:

- Metals moved out of the live indices project.
- XAU/XAG now belong to their own Railway repository/service/database.
- Project is hard-scoped to metals.
- NAS100/US500 generic broker execution is disabled in the standalone Metals runtime.
- XAU/XAG use the isolated OANDA practice lane.
- Both long and short simulation are supported.
- Generated-short v1 remains a research baseline.
- Cleaned-short v2 remains a challenger.
- 48h fixed result retained as benchmark.
- Actual practice trades are not blindly closed at 48h.
- First manager review at 48h.
- Hourly review on every new metal signal after 48h.
- 72/96/120h are protection milestones, not forced exits.
- Added manager-review evidence.
- Added basket snapshots.
- Added persistent close queue evidence.
- XAU/XAG managed separately.
- METALS_BASKET family overlay advisory only.
- Same-metal opposite-direction guard retained.
- Designed to coexist safely with BCO on the same OANDA practice account via strict instrument allowlists.

## Promotion philosophy

When a metal is ready for live trading, promote the standalone Metals project/lane rather than reintegrating it into the live index codebase.
