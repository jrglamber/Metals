"""Standalone OFFLINE combined young-short exposure accounting.

Nothing here is imported by the running bot. No broker, orders, configuration,
entry/exit decisions, or action outputs. Measurements are estimates; coverage
is limited by sampled marks and closed trade links.
"""
from __future__ import annotations

import argparse
import json
import math
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from metals_young_basket_research import (
    ASSETS, MAX_GAP_SECONDS, index_unique, matched_delta, nonnegative_int,
    number, observation, read_csv, timestamp, write_csv,
)

VERSION = "young_exposure_observation_v1_2026_11_27"
# Retrospective descriptive bins only. Not policy, defaults, or recommended caps.
BINS_PCT_NAV = (1.0, 2.0, 3.0, 4.2)
CLOSED_STATES = {"CLOSED", "CLOSED_MANAGER", "CLOSED_BROKER", "CLOSED_RECONCILED"}
YOUNG_ELAPSED_SECONDS = 48 * 3600
# Data-quality tolerance for separately captured, same-account NAV evidence.
MAX_NAV_SPREAD_FRACTION = .001


def _stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _close(link, original, t):
    """A matched, already-observed close; no future information in a row."""
    if link is None or link.get("status") not in CLOSED_STATES:
        return None
    closed = timestamp(link.get("closed_at_utc"))
    pnl = number(link.get("realized_pl"))
    if (str(link.get("side", "")).upper() != "SHORT" or closed is None
            or closed <= original["last_seen"] or closed > t or pnl is None):
        return None
    opened = timestamp(link.get("opened_at_utc"))
    if link.get("opened_at_utc") and (opened is None or opened != original["opened"]):
        return None
    return closed, pnl


def analyse_exposure(samples, budgets, links):
    """Pair simultaneous verified metals samples; account for observed losses.

    Physical cycle starts at first paired observation (left censored unless
    verified flat) and ends at verified combined flat. Unknown intervals do
    not erase known losses. Only gross losses with definite under-48 elapsed
    age at close enter the young-loss estimate. Others remain separate.
    """
    sm = index_unique(samples, lambda r: timestamp(r.get("sampled_at_utc")))
    bm = index_unique(budgets, lambda r: (timestamp(r.get("sampled_at_utc")), r.get("asset")))
    lm = {a: index_unique(links.get(a, []), lambda r: str(r.get("broker_trade_id") or "").strip() or None)
          for a in ASSETS}
    out = []
    tracked, settled, history = {}, {}, []
    cycle, cycle_scope, last_t = None, "LEFT_CENSORED", None
    from_flat = False
    gaps = False
    for t, row in sorted(sm.items()):
        result = dict(version=VERSION, sampled_at_utc=_stamp(t), status="UNKNOWN",
                      reason="", execution_authority=0)
        def unknown(reason):
            result.update(reason=reason, cycle_start_utc=cycle,
                          coverage="GAP_OR_UNKNOWN")
            out.append(result)
        if last_t is not None and t - last_t > MAX_GAP_SECONDS:
            history = []
            gaps = True
            from_flat = False
        last_t = t
        if row is None:
            unknown("DUPLICATE_SOURCE_TIMESTAMP")
            gaps = True
            history = []
            from_flat = False
            continue
        points = {a: observation(row, bm.get((t, a)), a) for a in ASSETS}
        if any(o["status"] not in ("VERIFIED", "FLAT") for o, _ in points.values()):
            unknown("|".join(f"{a}:{o['reason']}" for a, (o, _) in points.items()
                             if o["status"] not in ("VERIFIED", "FLAT")))
            gaps = True
            history = []
            from_flat = False
            continue
        obs = [o for o, _ in points.values()]
        nav_min, nav_max = sorted(o["nav_gbp"] for o in obs)
        # Separate reads of the same account need not be penny-identical.
        # Use the smaller reported denominator and disclose the spread.
        nav = nav_min
        if (nav_max - nav_min) / nav_min > MAX_NAV_SPREAD_FRACTION:
            unknown("NAV_MISMATCH")
            gaps = True
            history = []
            from_flat = False
            continue
        count = sum(o["open_count"] for o in obs)
        if any(o["status"] == "FLAT" and (o["remaining_risk_gbp"] != 0
                   or o["manager_young_short_count"] != 0) for o in obs):
            unknown("INCONSISTENT_FLAT_EVIDENCE")
            gaps = True
            history = []
            from_flat = False
            continue
        if nonnegative_int(row.get("total_open_count")) != count:
            unknown("TOTAL_OPEN_COUNT_MISMATCH")
            gaps = True
            history = []
            from_flat = False
            continue
        if any(o["open_count"] != o["short_count"] for o in obs):
            unknown("LONG_MATURITY_NOT_EXPORTED")
            gaps = True
            history = []
            from_flat = False
            continue
        marks = json.loads(row["position_marks_json"])
        all_open = {(m["instrument"].split("_")[0], str(m["trade_id"]).strip()) for m in marks
                    if m["instrument"] in ("XAU_USD", "XAG_USD")}
        young = {(a, tid): m for a, (_, positions) in points.items() for tid, m in positions.items()}
        if cycle is None and count:
            cycle = _stamp(t)
            cycle_scope = "SAMPLED_FROM_VERIFIED_FLAT" if from_flat else "LEFT_CENSORED"
            gaps = not from_flat
        # Record previously young IDs through maturity; distinguish real
        # disappearance from leaving the young set. No double counting.
        if set(settled) & all_open:
            unknown("SETTLED_TRADE_REAPPEARED")
            gaps = True
            history = []
            from_flat = False
            continue
        if any(k in tracked and tracked[k]["opened"] != m["opened"] for k, m in young.items()):
            unknown("TRADE_ID_OPEN_TIME_CHANGED")
            gaps = True
            history = []
            from_flat = False
            continue
        for k, m in young.items():
            tracked.setdefault(k, {"opened": m["opened"], "last_seen": t})
        for k in all_open & tracked.keys():
            tracked[k]["last_seen"] = t
        pending = set(tracked) - all_open - settled.keys()
        for k in pending:
            close = _close(lm[k[0]].get(k[1]), tracked[k], t)
            if close is not None:
                closed, pnl = close
                age = closed - tracked[k]["opened"]
                settled[k] = (max(0., -pnl), age < YOUNG_ELAPSED_SECONDS)
        unresolved = len(set(tracked) - all_open - settled.keys())
        young_lost = sum(loss for loss, definite in settled.values() if definite)
        other_lost = sum(loss for loss, definite in settled.values() if not definite)
        risk = sum(m["risk"] for m in young.values())
        pnl = sum(m["pnl"] for m in young.values())
        remaining = sum(o["remaining_risk_gbp"] for o in obs)
        # Original risk already includes loss between entry and stop.
        # Do NOT add current unrealized loss to that risk a second time.
        # Current negative marks beyond original nominal risk are disclosed.
        beyond = sum(max(0., -m["pnl"] - m["risk"]) for m in young.values())
        result.update(
            status="VERIFIED_SAMPLE", reason="", cycle_start_utc=cycle,
            coverage=cycle_scope if cycle else "VERIFIED_FLAT",
            coverage_gaps=int(gaps and cycle is not None),
            nav_gbp=nav, xau_nav_gbp=points["XAU"][0]["nav_gbp"],
            xag_nav_gbp=points["XAG"][0]["nav_gbp"],
            nav_spread_gbp=nav_max-nav_min, open_count=count, young_short_count=len(young),
            xau_young_short_count=len(points["XAU"][1]),
            xag_young_short_count=len(points["XAG"][1]),
            young_original_risk_gbp=risk, young_original_risk_pct_nav=100*risk/nav,
            young_mark_gbp=pnl, young_mark_pct_nav=100*pnl/nav,
            young_mark_r=pnl/risk if risk else None,
            young_mark_beyond_original_gbp=beyond,
            combined_remaining_stop_risk_gbp=remaining,
            combined_remaining_stop_risk_pct_nav=100*remaining/nav,
            all_open_young=int(count > 0 and count == len(young)),
            observed_definite_young_closed_gross_loss_gbp=young_lost,
            observed_age_unproven_closed_gross_loss_gbp=other_lost,
            observed_previously_young_gross_loss_gbp=young_lost+other_lost,
            observed_unsettled_count=unresolved,
            observed_definite_young_closed_count=sum(definite for _, definite in settled.values()),
            observed_age_unproven_closed_count=sum(not definite for _, definite in settled.values()),
            observed_young_risk_plus_definite_gross_loss_gbp=risk+young_lost,
            observed_young_risk_plus_definite_gross_loss_pct_nav=100*(risk+young_lost)/nav,
            observed_young_risk_plus_previously_young_loss_gbp=risk+young_lost+other_lost,
            family_campaign_id=row.get("family_campaign_id"),
            family_economic_equity_gbp=number(row.get("economic_equity_gbp")),
            family_economic_high_water_gbp=number(row.get("economic_high_water_gbp")),
            family_economic_giveback_gbp=number(row.get("economic_giveback_gbp")),
        )
        if history and t - history[-1][0] > MAX_GAP_SECONDS:
            history = []
        history = [v for v in history if t-v[0] <= 3600+MAX_GAP_SECONDS]
        if history:
            prev_t, prev_young = history[-1]
            new = [m for k, m in young.items() if k not in prev_young and m["opened"] > prev_t]
            result["new_young_count_since_previous_sample"] = len(new)
            result["new_young_original_risk_since_previous_sample_gbp"] = sum(m["risk"] for m in new)
        for seconds, label in ((900, "15m"), (3600, "60m")):
            delta, n = matched_delta(history, t, young, seconds)
            result[f"matched_young_delta_{label}_r"] = delta
            result[f"matched_young_count_{label}"] = n
        history.append((t, young))
        out.append(result)
        if count == 0:
            # Terminal flat observation reports what happened BEFORE reset.
            # Historical accounting never resets on dashboard family HWM reset.
            tracked, settled, history = {}, {}, []
            cycle, cycle_scope = None, "LEFT_CENSORED"
            from_flat, gaps = True, False
        else:
            from_flat = False
    return out


def describe_bins(rows, bins=BINS_PCT_NAV):
    """Sample occupancy, not a replay of admission decisions or P&L."""
    measured = [r for r in rows if r["status"] == "VERIFIED_SAMPLE" and r["young_short_count"] > 0]
    active = {r["cycle_start_utc"] for r in measured if r["cycle_start_utc"]}
    results = []
    for band in bins:
        if not math.isfinite(band) or band <= 0:
            raise ValueError("Research bin must be finite and positive")
        above = [r for r in measured if r["young_original_risk_pct_nav"] >= band]
        plus = [r for r in measured if r["observed_young_risk_plus_definite_gross_loss_pct_nav"] >= band]
        results.append(dict(
            version=VERSION, illustrative_bin_pct_nav=band,
            verified_nonzero_young_samples=len(measured),
            unknown_samples=sum(r["status"] == "UNKNOWN" for r in rows),
            observed_physical_cycles=len(active),
            original_risk_at_or_above_samples=len(above),
            risk_plus_known_losses_at_or_above_samples=len(plus),
            above_original_risk_share_pct=100*len(above)/len(measured) if measured else None,
            above_risk_plus_known_loss_share_pct=100*len(plus)/len(measured) if measured else None,
            cycles_with_original_at_or_above=len({r["cycle_start_utc"] for r in above}),
            cycles_with_risk_plus_known_loss_at_or_above=len({r["cycle_start_utc"] for r in plus}),
            execution_authority=0,
        ))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("live_analysis_zip", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    ns = parser.parse_args()
    with zipfile.ZipFile(ns.live_analysis_zip) as z:
        samples = read_csv(z, "live-metals-forward-economic-equity.csv")
        budgets = read_csv(z, "live-metals-basket-full-shadow-budget.csv")
        links = {a: read_csv(z, n) for a,n in (
            ("XAU", "live-xau-trade-links.csv"), ("XAG", "live-xag-short-trade-links.csv"))}
    rows = analyse_exposure(samples, budgets, links)
    ns.output.mkdir(parents=True, exist_ok=True)
    write_csv(ns.output/"combined-young-exposure-observations.csv", rows,
              ("sampled_at_utc", "status", "reason", "execution_authority"))
    write_csv(ns.output/"combined-young-exposure-bin-summary.csv", describe_bins(rows),
              ("illustrative_bin_pct_nav", "execution_authority"))
    print(f"{len(rows)} observations: {dict(Counter(r['status'] for r in rows))}. OFFLINE ONLY.")


if __name__ == "__main__":
    main()
