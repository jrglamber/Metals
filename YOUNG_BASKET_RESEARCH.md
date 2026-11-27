# Young basket adverse-excursion research (offline)
Version: `young_basket_observation_v1_2026_11_27`

This is a standalone, read-only historical study for XAU and XAG shorts. It
has no runtime installation hook, network or broker client, credential
handling, live configuration, or order/exit/stop/entry functionality. It
does not change 48-candle maturity, MFE25, ATR2, harvests, sizing, existing
NAV caps, or PR #79. All rows explicitly carry `execution_authority=0`.

## Existing data
Uses the existing live-analysis ZIP (forward economic-equity marks, same-time
verified broker budget evidence and closed trade links). No export change, new
database table, or extra live broker requests are necessary. Each subsequent
ZIP can be analysed again, including any newly closed actual matched cohorts.
Do not commit personal broker exports or generated results to this repository.

Run locally using Python 3.11+ (standard library only):

```sh
python -m unittest -v test_metals_young_basket_research
python metals_young_basket_research.py /path/to/live-analysis.zip --output /path/to/local-results
```

Outputs:
- `young-basket-observations.csv`: verified/unknown/flat samples, age
  evidence, young original risk as a share of asset initial risk and NAV,
  remaining stop risk separately, and matched-cohort 15m/60m changes.
- `young-basket-paper-comparisons.csv`: first observed adverse excursions,
  with fixed-cohort 25%, 50% and whole-cohort paper comparisons, later matched
  actual gross realized P&L, and unsettled/unknown cases.
- `young-basket-study-summary.csv`: grouped comparisons preserving
  missing/unsettled outcomes and eventual winners.

## Method and limitations
A verified source and same-timestamp budget snapshot with consistent position
counts are mandatory. Manager young-short counts identify the whole set if
all shorts are young; otherwise they must match the definite under-48-elapsed
subset. Mixed unidentified candle ages, duplicate records, invalid risk,
stale/unverified observations or inconsistent counts remain UNKNOWN, not
false safe or zero. The study does not pretend elapsed hours equal candles.
Long observations may contribute to total asset risk but are not simulated.
Original estimated per-trade risk is distinguished from current remaining
mark-to-stop risk, which can decline while unrealized losses rise.

The illustrative bands (-0.25R, -0.50R, -0.75R) are retrospective research
bins, not approved thresholds. To qualify, the observed young cohort has
at least eight shorts and at least 80% of asset original risk. Compare first
observed band, persistent band across two verified snapshots with matched
identity/risk, and simultaneous peer-metal band. Each variant independently
deduplicates overlapping cohorts; comparison variants are not independent
and must not be summed. 25%/50% comparisons use whole worst-mark-R trades at the
observation; selection is deterministic, not chosen with future results.
A matched actual close must be at or after the observation. Incomplete
matches leave aggregate outcome blank. Fixed-path marks are not executable
fills, do not account for slippage, financing, re-entry/portfolio feedback,
and cannot establish expected live performance. One market reversal yields
multiple highly correlated observations, not multiple independent events.

## Validation boundary
Extend the independent episode sample, including ordinary recovering baskets,
before making any production risk-policy decision. Preserve missing coverage
and compare foregone winners, not just losses avoided during the Thanksgiving
reversal. This study does not issue live trade instructions, modify any live
strategy, or activate the draft early-2R proposal.
