# Important Exports

The standalone Metals runtime exposes or packages evidence including:

```text
metals-demo-broker-links.csv
metals-demo-execution-audit.csv
metals-demo-manager-reviews.csv
metals-demo-basket-snapshots.csv
metals-demo-action-queue.csv
metals-demo-open-trades.csv
metals-demo-summary.json
```

Short-v2 research includes:

```text
metals-short-shadow-v2-all.csv
metals-short-shadow-v2-candidates.csv
metals-short-shadow-v2-watch.csv
metals-short-shadow-v2-blocked.csv
metals-short-shadow-v2-summary.csv
metals-short-shadow-v2-summary.json
```

The metals research ZIP should include the demo/basket-manager evidence as well as the strategy research data.

## Key fields to preserve

When analysing fresh forward data, retain:

- signal ID/time
- asset
- side
- v1 state
- v2 state
- v2 blockers
- long candidate state
- entry price
- stop
- units
- requested risk
- estimated/effective risk
- fixed 48h R
- managed current R
- manager high-water R
- MFE / MAE
- manager decision
- broker realised P&L
