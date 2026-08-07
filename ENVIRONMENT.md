# Environment Variables

## Database

```text
DATABASE_ENGINE=postgres
POSTGRES_RUNTIME_ENABLED=true
DATABASE_URL=<NEW METALS POSTGRES URL>
```

If the Postgres safety flag from the inherited runtime is still present, configure it consistently with the standalone Metals service.

## Disable generic/index broker lane

The standalone Metals project should explicitly keep the generic broker path safe:

```text
OANDA_ENABLED=false
BROKER_EXECUTION_ENABLED=false
BROKER_READ_ONLY=true
BROKER_KILL_SWITCH=true
```

These variables relate to the inherited generic/index broker path and are not the Metals practice lane.

## Metals OANDA practice lane

```text
METALS_DEMO_BROKER_ENABLED=true
METALS_DEMO_OANDA_ENV=practice
METALS_DEMO_OANDA_API_BASE=https://api-fxpractice.oanda.com
METALS_DEMO_OANDA_ACCOUNT_ID=<DEMO ACCOUNT ID>
METALS_DEMO_OANDA_API_TOKEN=<DEMO API TOKEN>
METALS_DEMO_ALLOWED_INSTRUMENTS=XAU_USD,XAG_USD
```

## Direction simulation

```text
METALS_DEMO_SIMULATE_LONGS=true
METALS_DEMO_SIMULATE_SHORTS=true
```

The system may research both directions. The same-metal opposite-direction guard should remain enabled so OANDA netting cannot corrupt the experiment.

```text
METALS_DEMO_BLOCK_OPPOSITE_DIRECTION_SAME_ASSET=true
```

## Risk and stops

Current demo settings:

```text
METALS_DEMO_XAU_RISK_AMOUNT=4
METALS_DEMO_XAG_RISK_AMOUNT=2

METALS_DEMO_XAU_SL_PCT=2.21
METALS_DEMO_XAG_SL_PCT=5.24
```

OANDA minimum size previously caused effective demo risk to exceed the nominal requested amount.

Accepted demo-only overage limits:

```text
METALS_DEMO_XAU_MAX_RISK_OVERAGE_PCT=150
METALS_DEMO_XAG_MAX_RISK_OVERAGE_PCT=75
```

These overage allowances are **not automatically approved for eventual live execution**.

## Basket manager

```text
METALS_DEMO_BASKET_MANAGER_ENABLED=true
METALS_DEMO_MANAGER_MIN_HOLD_CANDLES=48
METALS_DEMO_MANAGER_MAX_HOLD_CANDLES=0
```

`0` means there is no automatic maximum-age exit.

Protection milestones:

```text
METALS_DEMO_MANAGER_PROTECT_48=0.25
METALS_DEMO_MANAGER_PROTECT_72=0.50
METALS_DEMO_MANAGER_PROTECT_96=0.65
METALS_DEMO_MANAGER_PROTECT_120=0.75
```

These are management settings being forward-tested; they should not be casually re-tuned during the mechanics-proof window.

## Spread controls

Defaults inherited from the current metals build:

```text
METALS_DEMO_MAX_SPREAD_PCT_XAU=0.10
METALS_DEMO_MAX_SPREAD_PCT_XAG=0.20
```

## Basket staging

Current default structure:

```text
METALS_DEMO_BASKET_LIGHT_MIN_OPEN=5
METALS_DEMO_BASKET_NORMAL_MIN_OPEN=10
METALS_DEMO_BASKET_MATURE_MIN_OPEN=25

METALS_DEMO_BASKET_LIGHT_LOSS_R=-2.5
METALS_DEMO_BASKET_NORMAL_LOSS_R=-3.0
METALS_DEMO_BASKET_SEVERE_LOSS_R=-5.0
METALS_DEMO_BASKET_GIVEBACK_WARN_PCT=70
```

## Webhook

```text
WEBHOOK_SECRET=<SECRET>
```

Use a project-specific secret if practical.

Do not commit secrets to the repository.
