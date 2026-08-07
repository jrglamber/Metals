# Research Framework

## Research-first principle

The purpose of this project is not merely to generate OANDA demo orders. It must preserve enough evidence to determine whether the strategy and management rules genuinely add value.

Avoid changing rules in response to a handful of fresh winners or losers.

## Short model hierarchy

### v1 — generated short

This remains the historically best-supported short baseline.

Previous historical work produced a large number of matured signals and strong aggregate R over the June-August research window.

Treat v1 as the baseline hypothesis.

### v2 — cleaned generated short

v2 requires the v1 structure and adds filters intended to avoid:

- high execution/context RSI
- strong RSI rebound
- bullish reclaim
- obvious bounce conditions

These filters were designed after inspecting historical behaviour, so v2 must be treated as an incremental hypothesis that requires forward validation.

Do **not** discard v1 evidence or reset the entire research clock simply because v2 exists.

## Long model

The demo long side should continue to use the existing metals forward-test candidate path.

Do not create new long entry rules simply to increase demo trade frequency.

## Required comparisons

Fresh data should allow us to compare:

```text
v1 short signals
v2 accepted shorts
v1 signals blocked by v2
long forward candidates
fixed-48h outcomes
managed demo outcomes
actual broker execution results
```

## Management research

For each managed trade retain:

- theoretical fixed-48h result
- actual demo exit result
- maximum favourable excursion
- maximum adverse excursion
- manager high-water
- giveback at decision
- manager decision history
- stop updates
- broker P&L
- execution/slippage information where available

This allows us to answer two separate questions:

1. Which entry model is better?
2. Does active post-48h management improve the entry model?

## Anti-overfitting discipline

During the initial mechanics/forward-test period:

- do not tune thresholds because of ordinary outcomes
- fix only genuine implementation defects
- preserve rule/version identifiers
- distinguish historical/backfilled rows from fresh forward rows where practical
- evaluate blocked v1 trades as well as v2 accepted trades

A filter that removes losers but also removes the strategy's strongest winners may not improve the system.
