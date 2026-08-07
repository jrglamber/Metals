# Operations Runbook

## Daily checks

Review:

- service `/health`
- latest XAU signal timestamp
- latest XAG signal timestamp
- latest candidate states
- OANDA practice lane state
- open metals demo trades
- manager decisions
- action queue retries
- broker audit
- database connectivity

## If no signals arrive

Check in order:

1. TradingView alert still enabled
2. correct webhook URL
3. `WEBHOOK_SECRET`
4. Railway deployment health
5. latest `raw_signals`
6. signal symbol is XAUUSD/XAGUSD
7. source timestamp format

Do not redirect NAS100/US500 into this service to test signal receipt.

## If a valid candidate does not open

Check:

1. `METALS_DEMO_BROKER_ENABLED`
2. OANDA practice credentials
3. `METALS_DEMO_ALLOWED_INSTRUMENTS`
4. sizing preview
5. spread blocker
6. minimum-size overage guard
7. opposite-direction-open guard
8. duplicate raw signal/link
9. execution audit message

## If a trade should close but remains open

Check:

```text
metals_demo_action_queue
metals_demo_execution_audit
metals_demo_trade_links
```

Expected behaviour is persistent retry rather than a one-shot close request.

## If a trade disappears from OANDA

Reconciliation should classify a broker-closed trade and update the local link.

Possible causes:

- emergency stop
- manual OANDA close
- manager close
- external action

The local database should not continue treating a confirmed broker-closed trade as open.

## Do not react to normal P&L noise by changing strategy code

The demo period is intended to test:

- correct signals
- correct execution
- risk sizing
- stop placement
- reconciliation
- post-48h reviews
- stop modifications
- close retry
- data integrity

Ordinary winning and losing trades are evidence, not incidents.
