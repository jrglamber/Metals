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

## Combined young exposure accounting (offline companion)

`metals_young_exposure_study.py` adds family-level historical measurements,
without modifying or importing the live manager. Run:

```sh
python -m unittest -v test_metals_young_basket_research test_metals_young_exposure_study
python metals_young_exposure_study.py /path/to/live-analysis.zip --output /path/to/local-results
```

The companion emits `combined-young-exposure-observations.csv` and
`combined-young-exposure-bin-summary.csv`.

- Same real sample, verified position populations, manager short-age evidence,
  linked budget snapshots and matching account NAV required. Separately
  captured NAVs may differ slightly; both are recorded, the smaller denominator
  is used, and differences exceeding 0.1% of the smaller NAV are UNKNOWN.
  That tolerance is a data-quality convention, not a trading threshold.
- Original estimated young-short risk, mark P&L (£ / NAV / weighted R),
  nominal risk already exceeded in negative marks, and combined remaining
  stop risk (all open positions, including mature positions) are separate.
  Floating losses must not be added to original risk a second time.
- Matched 15m/60m R changes use the same trade IDs and unchanged risk evidence;
  new young positions are identified only between adjacent verified samples
  by opening timestamps. Existing family economic equity, peak and giveback
  are displayed separately, not mislabelled as young-cohort P&L.
- Realised **gross losses** on previously observed young shorts are counted
  once, only after a matching recorded close time no later than the sample.
  Under-48 elapsed age at close is treated as definite young under the hourly
  maturity convention; longer elapsed ages may correspond to immature trades
  across closures and are listed separately as age-unproven, not assumed young.
  A still-open mature trade is not a closed trade. Positive realised P&L is
  not credited against the loss measure. Original young risk plus known
  definite-young gross losses measures observed risk already spent or still
  exposed, not a guaranteed loss ceiling or broker margin calculation.
- The observed physical cycle ends at *verified combined flat*, with the
  terminal row reporting losses before the next cycle resets. Dashboard
  family/HWM changes and unknown rows do not erase observed losses. Unknown
  intervals interrupt deterioration comparisons. The first observed cycle
  without a preceding verified flat is left-censored. Even apparently
  continuous sampled coverage can miss short-lived/partially closed trades.
  Disappeared but unmatched or ambiguous IDs remain unsettled. Closed-link
  event times do not establish when the manager actually received the
  reconciliation. Financing and execution costs are not reconstructed.
- The manager export identifies young shorts only. Any paired sample with a
  long or ambiguous age population is UNKNOWN for the combined measure.
  No assumption about young-long maturity or protection is made.
- Research-only exposure bins (1%, 2%, 3%, 4.2% NAV) summarize the number of
  measured active observations at or above each nominal exposure band, with
  and without known losses. These are descriptive sensitivity points, **not
  proposed or approved limits**. Adjacent snapshots are highly correlated;
  physical-cycle counts, unknown samples and coverage qualifications accompany
  the occupancy statistics. This is not a counterfactual entry-path replay:
  a complete timestamped candidate/entry/exit/quote sequence, multiple
  independent cycles and mature/long age evidence are needed to evaluate
  foregone winners, execution effects and an admissible exposure policy.
  Do not infer a safe limit or saved profit from the bins.

Both scripts are standard-library offline analysis only; every output row has
`execution_authority=0`. No live hooks, broker writes, runtime variables,
order code, strategy thresholds, or deployment changes are included.
