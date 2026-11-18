# Metals — three proposed challengers: initial retrospective study

**Study date:** 18 November 2026 (as-of live exports approximately 18:28 UTC).  
**Research status:** Complete retrospective diagnostic; **not** a full portfolio-executable counterfactual.  
**Trading changes:** None. No new live entries, closes, sizing changes or stop changes.

## Sources and constraints
- Broker-scoped live ZIP: `/export/metals-live-analysis.zip`, verifying XAU cash since accounting epoch and all independently attributed live XAG Short cash.
- Research ZIP: `/export/metals-research.zip`, including demo/practice broker links, basket snapshots and independent exit-shadow outcomes.
- Reproducible script: [metals_three_challengers_20261118.py](metals_three_challengers_20261118.py).
- Live XAG export has 85 links: 62 CLOSED and 23 OPEN; 61 closed links have sufficient risk/P&L fields for a descriptive R ratio. The link export lacks historical opening timestamps, exact campaign boundaries, and marked-to-market R at each entry.
- Live XAU/XAG combined open P&L high-water tracking began 16 November. The available export supplies the persistent peak, not a full time series.

## Challenger 1 — XAG own-campaign +2R required after entry 20

**Current live baseline:** Up to 10 XAG Short campaign entries independently; thereafter a qualifying XAU Short signal within six hours; max 24 open XAG Short trades. XAU's own after-20 funding policy is *not* currently applied to XAG.

**Live closed-trade cohort diagnostic (not campaign gate replay):**
- First 20 lifetime XAG trade IDs: 19 valid closed outcomes, cumulative **−16.21 estimated trade-R**, win rate 0%.
- Lifetime ordinal 21 onward: 42 valid closed outcomes, cumulative **+3.98 estimated trade-R**, win rate 57.1%.
- Open trades and trade selection make these non-equivalent cohorts. The classification is lifetime ordinal, not a reconstructed active-campaign entry number.

**Demo pre-entry funding proxy:** Chronologically reconstruct 313 XAG Short practice entries and use only the latest XAG basket-R snapshot at least five minutes before entry, up to four hours old, plus banked R on trades already closed in that reconstructed campaign. This is deliberately *not* the XAU exact harvested-profit definition.
- 246 post-20 entry opportunities had eligible pre-entry data.
- A +2R proxy gate admitted **210** and rejected **36** (14.6%).
- Of those 36 rejected, 23 subsequently closed in the actual *unfiltered* demo history, and their recorded outcomes total **+2.58R**, average +0.112R/trade, win rate 56.5%.
- Thus the rule did not merely eliminate losing entries. Historical policy differences and portfolio-feedback prevent treating +2.58R as causal lost live profit.

**Decision:** Do not promote +2R XAG gate; no evidence yet of improved broker-executable risk-adjusted returns.

## Challenger 2 — separate XAG basket harvest

**Current live baseline:** XAG Short uses minimum 48-hour hold plus MFE_GIVEBACK_25, with 24 simultaneous open positions maximum. XAU's 30R, 80R, 130R harvest ladder acts on XAU alone.

**Paired practice/demo exit outcomes, SAME demo link under both challenger policies:**
- **MFE25 vs MFE50:** 157 matched trades; MFE25 averaged **+0.129R more**, winning 145 comparisons and losing 2 (remaining ties). Earlier chronological half +0.082R; later half +0.175R.
- **MFE25 vs ATR2 chandelier:** 159 matched trades; MFE25 averaged **+0.038R more** overall, but the earlier half was +0.079R and the later half **−0.002R**.
- These values are shadow exit-R differences, **not broker-executable GBP gains**. Pairs may share market conditions and exclude actual slippage/financing differences.
- 2,157 XAG Short demo basket snapshots belong to just **one observed basket key**. The 30R high-water was reached, but 509 snapshot readings above 30R are repeated observations, **not 509 independent harvest opportunities**. No sampled 40R high-water.
- XAG's one-unit live minimum means *per-trade* fractional 20% harvesting may not be broker-feasible. Any future basket harvest requires an integer whole-trade selection/replay.

**Decision:** Keep live MFE25. Neither copying XAU's 30R harvest rule nor activating a new XAG basket exit is justified by the present replay.

## Challenger 3 — family-level XAU+XAG GBP profit lock

**Verified realised-cash path only, starting 2 November:**
- 17 daily observations, realised-cash net **+£56.37** over the window.
- Cash-only cumulative peak **+£126.84** (3 November).
- Largest subsequent peak-to-trough decline in *cumulative realised cash*: **£175.16** (trough 10 November).
- The combined OPEN P&L observed peak was **£151.44**, but tracking started only 16 November and the historical intraday floating P&L series is not present.
- The realised-cash decline is **not** a mark-to-market portfolio drawdown, and cannot be converted into a feasible family-wide profit-lock return.

**Decision:** Family lock remains a research hypothesis. Use verified GBP economic equity (realised since a documented campaign baseline + open P&L), not naive addition of XAU and XAG R. Exact replay needs timestamped independent exposure/mark series and a broker-feasible integer trade-close selector.

## Governance and next evidence
1. No changes to live XAU or XAG entry/exit/risk/harvest rules.
2. Keep live XAG +2R gate as unpromoted hypothesis until active-campaign entry-time open R and banked profits are captured.
3. Keep proposed XAG basket harvesting and family profit-lock *shadow-only* until complete timestamped LIVE position paths, bid/ask execution assumptions, minimum trade size, fees/financing and counterfactual portfolio feedback are available.
4. Re-run this [research workflow](../.github/workflows/metals-three-challengers-replay.yml) against later exports; separate forward observations from the hindsight analysis.

**Important:** These are descriptive and demo-shadow diagnostics, not proof that a new trading rule would have made money. Promoting any challenger from the above numbers would be premature.
