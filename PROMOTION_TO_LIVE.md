# Promotion to Live

## Principle

A metal should be promoted by changing the standalone Metals project's broker lane, not by merging the strategy back into the NAS100/US500 project.

## What the demo period must prove

Before live-money promotion, confirm:

- no wrong-account orders
- no wrong-instrument orders
- no duplicate entries
- no stale/backfilled execution
- correct long/short direction
- emergency SL always attached
- previewed units match submitted units
- risk effects of minimum OANDA size are understood
- spreads/costs are acceptable
- broker trade links reconcile correctly
- 48h benchmark is retained
- hourly post-48h manager executes reliably
- stop updates never loosen a protected stop accidentally
- close-until-flat works
- no BCO interference on a shared practice account

## Strategy evidence

Broker mechanics and strategy quality are different tests.

Current research stance:

- v1 generated short has the stronger historical evidence
- v2 is a challenger requiring fresh comparison
- longs continue under their existing research signal
- post-48h management must be compared against fixed 48h

A clean two-week mechanics test can justify moving toward a tiny live v1 trial; it does not prove every v2/manager optimisation.

## Live promotion design

Do not simply change environment variables on a Friday afternoon and hope for the best.

Before promotion:

1. freeze a reviewed build
2. create explicit live metals configuration
3. hard allowlist only the promoted instruments
4. reconsider actual minimum-size risk
5. verify live-account currency and sizing
6. verify stop distances
7. start at the smallest practical live exposure
8. keep demo/research logging available
9. confirm the first live order manually

If only one asset is approved, allowlist only that asset.

Example:

```text
XAU_USD
```

rather than automatically enabling both XAU and XAG.

## Management promotion

If entry rules are ready before active manager rules are sufficiently validated, it is acceptable to promote the entry model with a simpler validated exit policy while the manager continues shadow testing.

Do not promote unvalidated management complexity merely because the entry model is strong.
