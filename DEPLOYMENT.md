# Railway Deployment

## 1. Repository

Create a standalone repository for Metals.

The production runtime file should be:

```text
app_postgres_runtime.py
```

Keep this repository independent from the live NAS100/US500 repository.

## 2. Railway project

Create a new Railway project/service for Metals.

Add a dedicated Railway Postgres database.

The Metals service should reference that database through `DATABASE_URL`.

## 3. Start command

Use the same FastAPI/uvicorn convention as the other Project Exit Plan services, for example:

```text
uvicorn app_postgres_runtime:app --host 0.0.0.0 --port $PORT
```

If the repository already uses a Procfile or Railway start command, keep the existing convention.

## 4. Python dependencies

At minimum the runtime requires FastAPI/uvicorn plus a PostgreSQL driver when running on Postgres.

Typical requirements include:

```text
fastapi
uvicorn
psycopg[binary]
```

Preserve any dependency already required by the runtime.

## 5. Environment variables

Configure the variables listed in `ENVIRONMENT.md`.

Do not copy the live index OANDA configuration into an active generic broker lane.

## 6. Deploy safely

Initial deployment sequence:

1. Deploy with metals broker execution disabled.
2. Confirm `/health`.
3. Confirm database initialization succeeds.
4. Confirm dashboard loads.
5. Confirm XAU/XAG webhooks are stored.
6. Confirm NAS100/US500 webhook test is ignored.
7. Confirm OANDA practice preview for XAU and XAG.
8. Enable `METALS_DEMO_BROKER_ENABLED=true`.
9. Wait for a genuinely fresh metal candidate.
10. Inspect the first real practice order manually.

## 7. TradingView

Redirect only the XAUUSD and XAGUSD alerts to:

```text
https://<metals-service-domain>/webhook/tradingview
```

Leave NAS100 and US500 alerts pointing to the live indices project.

## 8. First-trade validation

For the first XAU and first XAG practice trades verify:

- correct OANDA practice account
- correct instrument
- correct direction
- correct units
- immediate emergency stop exists
- estimated risk matches the pre-entry preview
- trade is present in `metals_demo_trade_links`
- audit row exists
- live NAS100/US500 account is unaffected
