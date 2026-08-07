# Architecture

## Project boundary

The standalone Metals project owns:

- XAU/XAG raw signal storage
- metals research outputs
- generated-short v1 evidence
- cleaned-short v2 evidence
- long research
- OANDA practice entries
- OANDA practice reconciliation
- metals trade links
- execution audit
- basket-manager reviews
- basket snapshots
- close action queue
- fixed-48h benchmark outcomes
- dashboard and exports for metals

It does **not** own:

- NAS100
- US500
- live index broker links
- index basket manager
- index harvest/protection
- BCO
- BCO basket manager

## External services

### TradingView
Sends hourly XAUUSD and XAGUSD webhook payloads.

### Railway
Hosts the FastAPI runtime and Postgres database.

### OANDA
Current mode: practice.

The Metals project may share the same OANDA practice account as another isolated research project, provided every project has a strict instrument allowlist.

## Data flow

```text
TradingView hourly alert
        |
        v
/webhook/tradingview
        |
        +--> hard asset scope check
        |
        +--> raw_signals
        |
        +--> long research
        |
        +--> generated-short v1
        |
        +--> cleaned-short v2
        |
        +--> fresh demo-entry evaluation
        |
        +--> OANDA practice order
        |
        +--> metals_demo_trade_links
        |
        +--> hourly reconciliation
        |
        +--> 48h+ manager review
                 |
                 +--> EXTEND
                 +--> PROTECT / tighten SL
                 +--> CLOSE
                 +--> persistent close retry
```

## Broker isolation

The metals broker lane uses its own configuration namespace:

```text
METALS_DEMO_*
```

This is separate from the legacy/index broker variables.

The standalone Metals runtime should keep the generic/index OANDA broker lane disabled.

## Database

Recommended:

```text
DATABASE_ENGINE=postgres
POSTGRES_RUNTIME_ENABLED=true
DATABASE_URL=<Metals-specific Railway Postgres URL>
```

Use a dedicated Postgres service for Metals rather than sharing the live index database.

## Main metals tables

Important runtime/audit tables include:

```text
raw_signals
metals_demo_trade_links
metals_demo_execution_audit
metals_demo_action_queue
metals_demo_manager_reviews
metals_demo_basket_snapshots
```

Existing metals research tables/derived exports are retained by the standalone runtime.
