"""Offline research of recorded young XAU/XAG short cohorts.

Reads existing analysis ZIPs only. No runtime hooks, broker clients, network,
credentials, live state, actions, orders, stops or execution authority.
First observed mark comparisons are not executable fills or portfolio replays.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import zipfile
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

VERSION = "young_basket_observation_v1_2026_11_27"
ASSETS = ("XAU", "XAG")
BANDS = (-.25, -.50, -.75)
MAX_GAP_SECONDS = 900
MIN_COHORT = 8
MIN_SHARE = .8
FLAGS = ("mark_to_market_complete", "broker_population_matched", "cash_broker_verified")
VARIANTS = ("FIRST_OBSERVATION", "PERSISTENT_2", "PEER_AT_SAME_BAND")
TRIMS = (("WORST_R_25", .25), ("WORST_R_50", .50), ("ENTIRE_COHORT", 1.0))


def number(value):
    try:
        n = float(value)
        return n if not isinstance(value, bool) and math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def timestamp(value):
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d.astimezone(timezone.utc).timestamp() if d.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def nonnegative_int(value):
    n = number(value)
    return int(n) if n is not None and n >= 0 and n.is_integer() else None


def truth(value):
    return str(value).strip().lower() in ("1", "true", "1.0")


def index_unique(rows, key):
    result = {}
    dup = set()
    for row in rows:
        k = key(row)
        if k is None:
            continue
        if k in result:
            dup.add(k)
        result[k] = row
    for k in dup:
        result[k] = None  # Ambiguity is unknown, not last-write-wins.
    return result


def observation(row, budget, asset):
    """Classify age only when aggregate manager count identifies the cohort."""
    raw_time = row.get("sampled_at_utc")
    t = timestamp(raw_time)
    result = dict(version=VERSION, sampled_at_utc=raw_time, asset=asset,
                  status="UNKNOWN", reason="", execution_authority=0)
    def unknown(reason):
        result["reason"] = reason
        return result, {}
    if t is None or row.get("status") != "COMPLETE" or not all(truth(row.get(k)) for k in FLAGS):
        return unknown("INCOMPLETE_POSITION_PROOF")
    if budget is None or budget.get("status") != "VERIFIED":
        return unknown("NO_MATCHED_VERIFIED_BUDGET")
    if timestamp(budget.get("sampled_at_utc")) != t or budget.get("asset") != asset:
        return unknown("BUDGET_SAMPLE_MISMATCH")
    count = nonnegative_int(row.get(asset.lower()+"_open_count"))
    bcount = nonnegative_int(budget.get("broker_open_count"))
    nav = number(budget.get("nav_gbp"))
    remaining = number(budget.get("broker_risk_used_gbp"))
    cap = number(budget.get("broker_risk_cap_gbp"))
    young_count = nonnegative_int(budget.get("young_short_count"))
    if count is None or count != bcount or nav is None or nav <= 0 or remaining is None or remaining < 0 or cap is None or cap <= 0:
        return unknown("INVALID_OR_INCONSISTENT_BUDGET")
    try:
        marks = json.loads(row.get("position_marks_json") or "null")
        if not isinstance(marks, list) or not all(isinstance(m, dict) for m in marks):
            return unknown("MALFORMED_POSITION_MARKS")
        marks = [m for m in marks if m.get("instrument") == asset+"_USD"]
    except (ValueError, TypeError):
        return unknown("MALFORMED_POSITION_MARKS")
    if len(marks) != count:
        return unknown("OPEN_COUNT_MISMATCH")
    positions = {}
    for m in marks:
        tid = str(m.get("trade_id") or "").strip()
        opened = timestamp(m.get("opened_at_utc"))
        risk, pnl = number(m.get("estimated_risk_gbp")), number(m.get("unrealized_gbp"))
        side = str(m.get("side") or "").upper()
        if not tid or tid in positions or opened is None or opened > t or risk is None or risk <= 0 or pnl is None or side not in ("SHORT", "LONG"):
            return unknown("INVALID_POSITION_MARK")
        positions[tid] = dict(opened=opened, risk=risk, pnl=pnl, side=side)
    total_risk = sum(m["risk"] for m in positions.values())
    shorts = {k:v for k,v in positions.items() if v["side"] == "SHORT"}
    result.update(open_count=count, short_count=len(shorts), manager_young_short_count=young_count,
                  initial_asset_risk_gbp=total_risk, remaining_risk_gbp=remaining,
                  nav_gbp=nav, nav_budget_gbp=cap, remaining_risk_pct_nav=remaining/nav*100)
    if not count:
        result.update(status="FLAT", reason="VERIFIED_FLAT", young_count=0)
        return result, {}
    elapsed_young = {k:v for k,v in shorts.items() if t-v["opened"] < 48*3600}
    # If all shorts were reviewed as young, identities are known even over a
    # weekend. Otherwise infer ONLY when the count agrees with definite
    # under-48-elapsed-hour positions; do not assign mixed unknown identities.
    if young_count is None or young_count > len(shorts) or young_count < len(elapsed_young):
        return unknown("AGE_COUNT_UNVERIFIED")
    if young_count == len(shorts):
        young = shorts
        age_basis = "ALL_SHORTS_MANAGER_YOUNG"
    elif young_count == len(elapsed_young):
        young = elapsed_young
        age_basis = "MANAGER_COUNT_MATCHES_ELAPSED_SUBSET"
    else:
        return unknown("MIXED_CANDLE_AGES_NOT_IDENTIFIABLE")
    risk = sum(m["risk"] for m in young.values())
    pnl = sum(m["pnl"] for m in young.values())
    result.update(status="VERIFIED", reason="", age_basis=age_basis,
                  young_count=len(young), young_initial_risk_gbp=risk,
                  young_risk_share=risk/total_risk, young_initial_risk_pct_nav=risk/nav*100,
                  young_mark_gbp=pnl, young_risk_weighted_r=pnl/risk if risk else None,
                  research_eligible=int(len(young)>=MIN_COHORT and risk/total_risk>=MIN_SHARE))
    return result, young


def matched_delta(history, t, cohort, lookback):
    if not history or not cohort:
        return None, 0
    j = bisect_right([x[0] for x in history], t-lookback)-1
    if j < 0 or t-lookback-history[j][0] > MAX_GAP_SECONDS:
        return None, 0
    window = history[j:]
    if any(b[0]-a[0]>MAX_GAP_SECONDS for a,b in zip(window,window[1:])) or t-window[-1][0]>MAX_GAP_SECONDS:
        return None, 0
    old = window[0][1]
    common = set(cohort)
    for _,marks in window:
        common.intersection_update(marks)
    risk = sum(cohort[i]["risk"] for i in common)
    if risk <= 0 or risk < MIN_SHARE*sum(x["risk"] for x in cohort.values()):
        return None, len(common)
    # Same identities AND same original risk. Changed size/basis is unknown.
    if any(abs(marks[i]["risk"]-cohort[i]["risk"])>1e-6 for _,marks in window for i in common):
        return None, len(common)
    return sum(cohort[i]["pnl"]-old[i]["pnl"] for i in common)/risk, len(common)


def analyse(samples, budgets, links):
    """Returns sample diagnostics and non-overlapping paper cohort comparisons.

    Each band/variant/asset has its own no-overlap accounting. Variants overlap
    with one another, so results must not be added across them.
    """
    sm = index_unique(samples, lambda r: timestamp(r.get("sampled_at_utc")))
    bm = index_unique(budgets, lambda r: (timestamp(r.get("sampled_at_utc")), r.get("asset")))
    lm = {a:index_unique(links.get(a, []), lambda r: str(r.get("broker_trade_id") or "").strip() or None)
          for a in ASSETS}
    history = {a:[] for a in ASSETS}
    prior = {}
    seen = defaultdict(set)
    out, events = [], []
    for t,row in sorted(sm.items()):
        if row is None:
            history = {a:[] for a in ASSETS}
            prior.clear()
            for a in ASSETS:
                out.append(dict(version=VERSION,sampled_at_utc=datetime.fromtimestamp(t,timezone.utc).isoformat(),
                                asset=a,status="UNKNOWN",reason="DUPLICATE_SOURCE_TIMESTAMP",execution_authority=0))
            continue
        points = {a:observation(row,bm.get((t,a)),a) for a in ASSETS}
        for a,(o,cohort) in points.items():
            if o["status"] != "VERIFIED":
                history[a] = []
                prior.pop(a, None)
                out.append(o)
                continue
            # Keep only enough verified history for point-in-time comparisons.
            if history[a] and t-history[a][-1][0]>MAX_GAP_SECONDS:
                history[a] = []
                prior.pop(a, None)
            history[a] = [v for v in history[a] if t-v[0]<=3600+MAX_GAP_SECONDS]
            for seconds,label in ((900,"15m"),(3600,"60m")):
                delta,n = matched_delta(history[a],t,cohort,seconds)
                o["matched_delta_"+label+"_r"] = delta
                o["matched_count_"+label] = n
            history[a].append((t,cohort))
            out.append(o)
            r = o["young_risk_weighted_r"]
            other = points["XAG" if a=="XAU" else "XAU"][0]
            for band in BANDS:
                if not o["research_eligible"] or r is None or r>band:
                    continue
                p = prior.get(a)
                common = set(cohort)&set(p[1]) if p else set()
                shared_risk = sum(cohort[i]["risk"] for i in common)
                persistent = bool(p and t-p[0]<=MAX_GAP_SECONDS and shared_risk>0
                    and shared_risk>=MIN_SHARE*sum(m["risk"] for m in cohort.values())
                    and all(abs(cohort[i]["risk"]-p[1][i]["risk"])<=1e-6 for i in common)
                    and sum(p[1][i]["pnl"] for i in common)/shared_risk<=band
                    and sum(cohort[i]["pnl"] for i in common)/shared_risk<=band)
                peer = bool(other.get("status")=="VERIFIED" and other.get("research_eligible")
                            and other.get("young_risk_weighted_r") is not None
                            and other["young_risk_weighted_r"]<=band)
                for variant,ok in zip(VARIANTS,(True,persistent,peer)):
                    key=(a,band,variant)
                    if not ok or seen[key]&set(cohort):
                        continue
                    seen[key].update(cohort)
                    ordered=sorted(cohort, key=lambda i:(cohort[i]["pnl"]/cohort[i]["risk"],i))
                    for label,frac in TRIMS:
                        chosen=ordered[:math.ceil(frac*len(ordered))]
                        matched=[]
                        for i in chosen:
                            link=lm[a].get(i)
                            if not link or not str(link.get("status","")).upper().startswith("CLOSED"):
                                continue
                            at=timestamp(link.get("closed_at_utc")); actual=number(link.get("realized_pl"))
                            if at is not None and at>=t and actual is not None and str(link.get("side","")).upper()=="SHORT":
                                matched.append(actual)
                        mark=sum(cohort[i]["pnl"] for i in chosen)
                        complete=len(matched)==len(chosen)
                        realized=sum(matched) if complete else None
                        events.append(dict(version=VERSION,asset=a,observed_at_utc=o["sampled_at_utc"],
                            band_r=band,variant=variant,comparison=label,cohort_count=len(cohort),
                            cohort_r=r,selected_count=len(chosen),matched_count=len(matched),
                            cohort_ids=json.dumps(sorted(cohort)),selected_ids=json.dumps(chosen),
                            observed_mark_gbp=mark,actual_gross_realised_gbp=realized,
                            marked_difference_gbp=mark-realized if complete else None,
                            eventual_winners=sum(x>0 for x in matched) if complete else None,
                            comparison_status="MATCHED" if complete else "UNSETTLED_OR_UNVERIFIED",
                            execution_authority=0))
            # A prior observation must itself satisfy cohort eligibility.
            if o["research_eligible"] and r is not None:
                prior[a]=(t,cohort)
            else:
                prior.pop(a,None)
    return out,events


def summarize(events):
    groups=defaultdict(list)
    for e in events:
        groups[(e["asset"],e["band_r"],e["variant"],e["comparison"])].append(e)
    out=[]
    for (a,b,v,c),rows in sorted(groups.items()):
        matched=[r for r in rows if r["comparison_status"]=="MATCHED"]
        out.append(dict(asset=a,band_r=b,variant=v,comparison=c,episodes=len(rows),
                        matched_episodes=len(matched),unsettled_episodes=len(rows)-len(matched),
                        selected_trades=sum(r["selected_count"] for r in rows),
                        actual_gross_realised_gbp=sum(r["actual_gross_realised_gbp"] for r in matched) if matched else None,
                        observed_mark_gbp=sum(r["observed_mark_gbp"] for r in matched) if matched else None,
                        marked_difference_gbp=sum(r["marked_difference_gbp"] for r in matched) if matched else None,
                        eventual_winners=sum(r["eventual_winners"] for r in matched) if matched else None,
                        execution_authority=0))
    return out


def read_csv(z,name):
    with z.open(name) as f:
        return list(csv.DictReader(io.TextIOWrapper(f,encoding="utf-8-sig")))


def write_csv(path,rows,minimum_fields):
    fields=list(dict.fromkeys([*minimum_fields,*(k for row in rows for k in row)]))
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("live_analysis_zip",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    ns=parser.parse_args()
    with zipfile.ZipFile(ns.live_analysis_zip) as z:
        samples=read_csv(z,"live-metals-forward-economic-equity.csv")
        budgets=read_csv(z,"live-metals-basket-full-shadow-budget.csv")
        links={a:read_csv(z,n) for a,n in (
            ("XAU","live-xau-trade-links.csv"),("XAG","live-xag-short-trade-links.csv"))}
    obs,events=analyse(samples,budgets,links)
    ns.output.mkdir(parents=True,exist_ok=True)
    write_csv(ns.output/"young-basket-observations.csv",obs,("sampled_at_utc","asset","status","reason","execution_authority"))
    write_csv(ns.output/"young-basket-paper-comparisons.csv",events,("asset","observed_at_utc","band_r","variant","comparison","comparison_status","execution_authority"))
    write_csv(ns.output/"young-basket-study-summary.csv",summarize(events),("asset","band_r","variant","comparison","episodes","execution_authority"))
    print(f"{len(obs)} asset observations; {len(events)} overlapping variant comparisons. OFFLINE RESEARCH ONLY.")


if __name__=="__main__":
    main()
