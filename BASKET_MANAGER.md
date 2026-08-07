# Metals Basket Manager

## Objective

The manager is designed to avoid a blind fixed 48-hour exit while preserving the 48h result as a clean benchmark.

The core philosophy is:

> 48h is the start of active management, not an automatic close.

## Before 48h

Normal basket-manager exits are not expected before 48 hourly candles.

The broker emergency stop remains active at all times.

This protects the original strategy structure from over-management while a trade is still young.

## At 48h

The first formal decision is made.

Possible outcomes include:

- `EXTEND`
- managed stop protection
- close for deterioration
- close for reversal
- close for excessive giveback

The fixed-48h price/R is stored independently as the control result.

## After 48h

A fresh review occurs on **every new hourly metal signal**.

There is no requirement to wait until 72h, 96h or 120h.

Those ages are milestones used for stronger protection logic and reporting.

## Managed protection milestones

Current default protection fractions:

| Age | Protection fraction |
|---|---:|
| 48h+ | 25% |
| 72h+ | 50% |
| 96h+ | 65% |
| 120h+ | 75% |

These percentages relate to progressively protecting the favourable move rather than mechanically closing at the milestone.

## Trade-level considerations

The manager tracks:

- current R
- MFE in R
- MAE in R
- trade high-water R
- giveback from high-water
- current directional support
- adverse reversal / bullish rebound evidence
- current stop price
- fixed-48h result
- latest manager decision

## Basket-level logic

XAU and XAG are not treated as one inseparable position.

Primary execution scopes are:

```text
XAUUSD:LONG
XAUUSD:SHORT
XAGUSD:LONG
XAGUSD:SHORT
```

Basket defence is staged by basket size.

Young/small baskets are deliberately harder to trim.

Only mature 48h+ trades should be eligible for normal basket-level defensive closures.

## Metals family overlay

`METALS_BASKET` combines the overall XAU/XAG state for context.

It is **advisory only**.

A weak XAG basket should not automatically force XAU trades closed, and vice versa.

## Close-until-flat

When a manager close is requested:

1. close action is written to `metals_demo_action_queue`
2. immediate OANDA close attempts are made
3. success marks the link closed
4. failure leaves the action in `RETRY`
5. subsequent processing retries the close

A single failed REST request must not silently leave a trade open indefinitely.
