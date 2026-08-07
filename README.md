# Project Exit Plan — Metals

Standalone XAU/XAG research and OANDA practice-forward-testing project.

## Scope

This repository is deliberately isolated from the live indices bot.

**Assets**
- XAUUSD / `XAU_USD`
- XAGUSD / `XAG_USD`

**Current operating mode**
- Research collection: enabled
- OANDA environment: practice/demo
- Long simulation: enabled
- Short simulation: enabled
- Metals basket manager: enabled
- Live-money execution: not enabled

The project is intended to remain standalone when promoted. A metal does **not** get merged back into the NAS100/US500 project. When evidence is sufficient, the relevant metals lane can be pointed at the live OANDA environment under explicit live safeguards.

## Current strategy research

The project retains several distinct evidence streams:

### Long research
Uses the existing forward-test candidate path already present in the historical metals research.

### Generated Short v1
Original generated-short model. This has the strongest historical evidence so far and remains the baseline short model.

### Cleaned Short v2
A refinement of v1 which applies additional rebound/high-RSI/bullish-reclaim filters. It remains a challenger rather than a replacement for v1 until fresh evidence demonstrates that the filtering genuinely improves outcomes.

### Pine explicit shorts
Comparison-only. These are not a positive broker execution trigger.

## Demo basket manager

The intended live behaviour is being exercised on the OANDA practice account.

- Minimum normal hold: **48 hourly candles**
- First formal management review: **48h**
- Review frequency after 48h: **every new hourly metal signal**
- 72h / 96h / 120h: reporting/protection milestones, **not forced exits**
- Strong runners may remain open beyond 120h
- Managed stops may progressively protect profitable mature trades
- Weakening/reversing/giveback trades may be closed
- Failed closes remain in a persistent close-until-flat queue
- XAU and XAG are managed separately
- `METALS_BASKET` family overlay is advisory only

The fixed-48h outcome is still recorded as the benchmark so active management can be judged against the clean historical baseline.

## Repository entry point

The Railway runtime should be named:

```text
app_postgres_runtime.py
```

Use the standalone metals runtime generated from the v10.1.27 combined indices/metals build.

## Safety principle

This repository must never manage NAS100, US500 or BCO.

Unexpected non-metal TradingView webhooks should be ignored before normal strategy processing.

The OANDA practice allowlist must remain:

```text
XAU_USD,XAG_USD
```

See the remaining documents in this pack before deployment.
