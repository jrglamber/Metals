# OANDA Safety

## Current environment

The Metals project is a **practice/demo** system.

The broker environment must be:

```text
METALS_DEMO_OANDA_ENV=practice
```

## Hard instrument scope

Allowed instruments:

```text
XAU_USD
XAG_USD
```

Nothing else should be writable by this project.

## Shared practice account

The Metals project may share an OANDA practice account with BCO research.

That is acceptable only because each service has an independent hard allowlist.

Metals must not reconcile, close, modify stops on, or otherwise manage BCO trades.

BCO must likewise remain restricted to its own instrument.

## Same-metal direction guard

OANDA account mode/netting behaviour can make simultaneous long/short positions on one instrument unsafe for experimental attribution.

Keep:

```text
METALS_DEMO_BLOCK_OPPOSITE_DIRECTION_SAME_ASSET=true
```

This still allows both long and short research over time without corrupting the active broker position.

## Minimum-size risk

At current OANDA minimum units and stops, previous previews showed approximate effective risk around:

- XAU: ~£9.4 despite £4 nominal requested risk
- XAG: ~£3.25 despite £2 nominal requested risk

This was accepted for **demo mechanics testing only**.

Do not automatically carry those effective risk levels into live promotion.

## First order checklist

For every newly promoted instrument/direction verify:

- account is practice
- instrument is correct
- sign/direction is correct
- units are correct
- emergency stop is attached immediately
- actual stop distance matches preview
- estimated risk is recorded
- broker trade ID is linked
- audit record exists
