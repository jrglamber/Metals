"""Read-only research on actual Metals exports (2026-11-18).

Tests three predefined hypotheses without touching live trading. Outputs only
aggregates, no broker trade IDs or account numbers. Makes no claim of a
broker-executable replay when intratrade live marks are absent.
"""
from __future__ import annotations
import csv
import io
import json
import math
import statistics
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone, date

BASE="https://metals-production-bb81.up.railway.app"
RESEARCH_VERSION="metals-three-challengers-2026-11-18-v1"

def retrieve(path):
    req=urllib.request.Request(BASE+path,headers={
        "User-Agent":"Metals-Read-Only-Challenger-Study/1.0"})
    with urllib.request.urlopen(req,timeout=75) as response:
        blob=response.read(55_000_000)
        if response.status!=200 or not zipfile.is_zipfile(io.BytesIO(blob)):
            raise RuntimeError("Research export unavailable: "+path)
    return zipfile.ZipFile(io.BytesIO(blob))

def rows(zf,name):
    return list(csv.DictReader(io.TextIOWrapper(zf.open(name),encoding="utf-8-sig")))

def num(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (ValueError,TypeError):
        return None

def timestamp(value):
    if not value:return None
    try:
        x=datetime.fromisoformat(value.replace("Z","+00:00"))
        return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x
    except Exception:return None

def stats(values):
    a=[v for v in values if v is not None and math.isfinite(v)]
    if not a:return {"n":0}
    return {"n":len(a),"mean":round(statistics.mean(a),4),
            "median":round(statistics.median(a),4),
            "sum":round(sum(a),4),
            "win_rate":round(sum(v>0 for v in a)/len(a),4),
            "negative_rate":round(sum(v<0 for v in a)/len(a),4)}

def xag_stack_diagnostic(live):
    links=rows(live,"live-xag-short-trade-links.csv")
    # OANDA numeric trade IDs are monotone but the export has no opening
    # timestamp or active campaign ID. Order here is lifetime ordinal only.
    known=[r for r in links if str(r.get("broker_trade_id") or "").isdigit()]
    known.sort(key=lambda r:int(r["broker_trade_id"]))
    early=[];later=[];incomplete=0;observations=[]
    for ix,r in enumerate(known,1):
        closed=str(r.get("status") or "").upper().startswith("CLOSED")
        pnl=num(r.get("realized_pl"));risk=num(r.get("estimated_risk_amount"))
        if closed and pnl is not None and risk is not None and risk>0:
            R=pnl/risk
            (early if ix<=20 else later).append(R)
            observations.append((ix,R,str(r.get("status"))))
        elif closed:incomplete+=1
    return {
        "source":"actual LIVE XAG Short trade-link records",
        "as_of":len(known),
        "total_links":len(links),
        "open_count":sum(r.get("status")=="OPEN" for r in links),
        "closed_count":sum(str(r.get("status") or "").startswith("CLOSED") for r in links),
        "closed_r_available":len(early)+len(later),
        "closed_r_missing":incomplete,
        "closed_ordinal_1_to_20":stats(early),
        "closed_lifetime_ordinal_21_plus":stats(later),
        "next_gate_target":"+2R own XAG campaign, after 20 campaign entries",
        "can_exactly_reconstruct_20_plus_campaign_gate":False,
        "blockers":["entry timestamps absent in live link export",
                    "intratrade open-XAG-R snapshots absent",
                    "historical active campaign boundaries not exported",
                    "entry counterfactual changes later positions and management"],
        "note":"Lifetime ordinal cohorts are DESCRIPTIVE, NOT proof that the live +2R gate improves ROI. Link R excludes any financing not present on the link."
    }

def xag_demo_plus_2r_gate(research):
    """Indicative historical entry-admission gate on DEMO, not live execution.

    Uses only information timestamped before each entry; a 5-minute embargo
    avoids processing a same-signal basket observation after the hypothetical
    decision. This is a one-pass no-feedback diagnostic (not an equity replay).
    """
    from bisect import bisect_right
    from datetime import timedelta
    all_links=rows(research,"metals-demo-broker-links.csv")
    links=[]
    for r in all_links:
        if r.get("asset")!="XAGUSD" or str(r.get("side") or "").lower()!="short":
            continue
        opened=timestamp(r.get("created_at_utc"))
        if not opened:continue
        r=dict(r)
        r["_opened"]=opened
        r["_closed"]=timestamp(r.get("closed_at_utc"))
        links.append(r)
    links.sort(key=lambda r:r["_opened"])
    snaps=[]
    for r in rows(research,"metals-demo-basket-snapshots.csv"):
        if r.get("asset")!="XAGUSD" or str(r.get("side") or "").lower()!="short" or r.get("scope")!="ASSET_SIDE":
            continue
        stamp=timestamp(r.get("created_at_utc"))
        R=num(r.get("basket_r"))
        if stamp and R is not None:
            snaps.append((stamp,R))
    snaps.sort(key=lambda x:x[0])
    snap_times=[v[0] for v in snaps]
    cycles=[]
    cycle_start=None
    cycle_ordinal=0
    eligible=[]
    blocked=[]
    passed=[]
    missing=0
    max_ordinal=0
    live_before=0
    for r in links:
        now=r["_opened"]
        existing=[p for p in cycles if p["_opened"]<now and
                  (p["_closed"] is None or p["_closed"]>now)]
        if not existing:
            cycles=[]
            cycle_start=now
            cycle_ordinal=0
        cycle_ordinal+=1
        max_ordinal=max(max_ordinal,cycle_ordinal)
        if cycle_ordinal>20:
            idx=bisect_right(snap_times,now-timedelta(minutes=5))-1
            valid=idx>=0 and timedelta(0)<=now-snaps[idx][0]<=timedelta(hours=4)
            banked=0.0
            banked_valid=True
            for prior in cycles:
                if prior["_closed"] and prior["_closed"]<=now:
                    pnl=num(prior.get("realized_pl"))
                    risk=num(prior.get("estimated_risk_amount"))
                    if pnl is None or risk is None or risk<=0:
                        banked_valid=False
                    else: banked+=pnl/risk
            actual_pnl=num(r.get("realized_pl"))
            actual_risk=num(r.get("estimated_risk_amount"))
            actual_r=actual_pnl/actual_risk if actual_pnl is not None and actual_risk and actual_risk>0 else None
            if not valid or not banked_valid:
                missing+=1
            else:
                proxy_R=snaps[idx][1]+banked
                (passed if proxy_R>=2 else blocked).append(actual_r)
                eligible.append(proxy_R)
        cycles.append(r)
    return {
        "source":"PRACTICE XAG Short entries joined to prior DEMO basket-R snapshots",
        "demo_xag_short_entries":len(links),
        "max_reconstructed_campaign_ordinal":max_ordinal,
        "post_20_with_fresh_prior_R_proxy":len(passed)+len(blocked),
        "post_20_unknown_funding":missing,
        "pass_plus_2r_proxy":len(passed),
        "blocked_plus_2r_proxy":len(blocked),
        "passed_actual_subsequent_closed_r":stats(passed),
        "blocked_actual_subsequent_closed_r":stats(blocked),
        "observed_proxy_funding_r":stats(eligible),
        "can_claim_live_counterfactual_return":False,
        "model":"latest pre-entry demo side basket R + prior closed trade R (not exact XAU banked-harvest definition)",
        "biases":["demo policy and entries differ from live",
                  "no-feedback: blocked entries would change subsequent basket states",
                  "historical demo basket observations can be incomplete",
                  "risk and financing estimates differ from broker full cash proof"],
    }


def xag_demo_exit_study(research):
    r=rows(research,"metals-exit-challenger-shadow.csv")
    subset=[x for x in r if x.get("asset")=="XAGUSD" and x.get("side","").lower()=="short"]
    counts=Counter(x.get("challenger","UNKNOWN") for x in subset)
    paired=defaultdict(list)
    for x in subset:
        actual=num(x.get("actual_r"));hypo=num(x.get("hypothetical_exit_r"))
        if actual is None or hypo is None:continue
        if str(x.get("paired_complete","")).strip().lower() not in ("1","true","yes"):continue
        key=x.get("challenger") or "UNKNOWN"
        paired[key].append((hypo,actual))
    paired_results={}
    for challenger,a in sorted(paired.items()):
        if len(a)<5:continue
        diffs=[h-real for h,real in a]
        paired_results[challenger]={
            "n":len(a),"challenger_r":stats([h for h,_ in a]),
            "actual_r":stats([v for _,v in a]),"delta_r":stats(diffs),
            "challenger_won_pairs":sum(h>v for h,v in a),
            "actual_won_pairs":sum(h<v for h,v in a)}
    # Pair like-for-like challenger outcomes for the SAME underlying demo link.
    # A shadow-vs-actual comparison is not necessarily today's MFE25 live policy:
    # label the historical actual exit policy and isolate matched candidates.
    actual_policy_by_challenger=defaultdict(Counter)
    by_link=defaultdict(dict)
    link_entry_time={}
    for x in subset:
        name=x.get("challenger") or "UNKNOWN"
        ap=x.get("actual_policy") or "UNKNOWN"
        actual_policy_by_challenger[name][ap]+=1
        key=str(x.get("link_id") or "")
        hypo=num(x.get("hypothetical_exit_r"))
        if key and hypo is not None and str(x.get("paired_complete","")).strip().lower() in ("1","true","yes"):
            by_link[key][name]=hypo
            link_entry_time[key]=str(x.get("entry_time") or "")
    heads={}
    for a,b in [("MFE_GIVEBACK_25","ATR2_CHANDELIER"),
                ("MFE_GIVEBACK_25","MFE_GIVEBACK_50"),
                ("ATR2_CHANDELIER","MFE_GIVEBACK_50")]:
        matched=[d[a]-d[b] for d in by_link.values() if a in d and b in d]
        chronological=sorted(
            [(link_entry_time[k],d[a]-d[b]) for k,d in by_link.items()
             if a in d and b in d],key=lambda x:x[0])
        mid=len(chronological)//2
        first=[v for _,v in chronological[:mid]]
        second=[v for _,v in chronological[mid:]]
        sd=statistics.stdev(matched) if len(matched)>1 else None
        ci=(1.96*sd/math.sqrt(len(matched))) if sd is not None else None
        heads[a+"_minus_"+b]={
            "paired_candidates":len(matched),"delta_r":stats(matched),
            "naive_independent_95pct_mean_delta_interval":
                [round(statistics.mean(matched)-ci,4),
                 round(statistics.mean(matched)+ci,4)] if ci is not None else None,
            "earlier_half_delta_r":stats(first),
            "later_half_delta_r":stats(second),
            "a_wins":sum(v>0 for v in matched),
            "b_wins":sum(v<0 for v in matched)}
    bs=rows(research,"metals-demo-basket-snapshots.csv")
    xag=[x for x in bs if x.get("asset")=="XAGUSD" and
         (x.get("side") or "").upper()=="SHORT"]
    by_scope=Counter((x.get("scope") or "UNKNOWN") for x in xag)
    group_max=defaultdict(float)
    for x in xag:
        name=str(x.get("basket_key") or "UNKNOWN")
        val=num(x.get("high_water_r"))
        if val is not None:group_max[name]=max(group_max[name],val)
    vals=[num(x.get("basket_r")) for x in xag]
    vals=[x for x in vals if x is not None]
    hws=[num(x.get("high_water_r")) for x in xag]
    hws=[x for x in hws if x is not None]
    return {
        "source":"PRACTICE/DEMO XAG Short exit shadows and basket snapshots",
        "all_demo_xag_short_shadow_rows":len(subset),
        "challenger_rows":dict(counts.most_common(25)),
        "paired_exit_r_comparisons":paired_results,
        "historical_actual_policy_by_challenger": {
            k:dict(v.most_common(12)) for k,v in actual_policy_by_challenger.items()},
        "same_demo_link_exit_policy_head_to_head":heads,
        "xag_demo_basket_snapshots":len(xag),"scopes":dict(by_scope),
        "distinct_basket_keys":len(group_max),
        "basket_key_peak_at_least_30r":sum(v>=30 for v in group_max.values()),
        "demo_basket_r":stats(vals),"demo_high_water_r":stats(hws),
        "demo_snapshot_exceedance_not_independent_harvests":{
            f"{n}R":sum(x>=n for x in hws) for n in (10,20,30,40,50,80)},
        "can_reconstruct_live_XAG_30R_harvest_exit":False,
        "limits":["demo positions are not live XAG Short holdings",
                  "same demo campaign repeated in snapshots",
                  "exposure and transaction costs change after hypothetical harvest",
                  "live intratrade XAG R path missing"],
    }

def combined_cash_study(live):
    daily=rows(live,"live-metals-daily-realised-gbp.csv")
    eligible=sorted([r for r in daily if r.get("period_london") and
         r.get("period_london")>="2026-11-02"],key=lambda x:x["period_london"])
    initial=0.;running=0.;peak=0.;max_dd=0.;peak_day=None;dd_day=None
    day_returns=[]
    for row in eligible:
        v=num(row.get("combined_live_net_gbp"))
        if v is None:continue
        running+=v;day_returns.append(v)
        if running>peak:peak=running;peak_day=row["period_london"]
        dd=peak-running
        if dd>max_dd:max_dd=dd;dd_day=row["period_london"]
    xau=rows(live,"live-xau-hwm-events.csv")
    manifest=json.loads(live.read("manifest.json"))
    return {
        "source":"verified actual LIVE XAU+XAG cash ledger; NOT mark-to-market portfolio",
        "days_since_xag_first_cash":len(day_returns),
        "nov_2_onward_net_realised_gbp":round(running,2),
        "nov_2_onward_cash_peak_gbp":round(peak,2),
        "max_realised_cash_drawdown_gbp":round(max_dd,2),
        "max_realised_cash_drawdown_date":dd_day,
        "date_cash_peak":peak_day,
        "daily_cash_change":stats(day_returns),
        "xau_hwm_events":len(xau),
        "combined_open_hwm":{k:v for k,v in (manifest.get("combined_open_basket_hwm") or {}).items()
                             if k in ("started_at_utc","high_water_at_utc","high_water_gbp")},
        "can_exactly_replay_family_equity_lock":False,
        "limits":["realised-only series excludes floating XAU/XAG P&L",
                  "combined open HWM is a single peak and began 16 Nov",
                  "no historical timestamped combined broker open P&L path",
                  "a hypothetical family lock changes positions and subsequent signals"],
    }

def main():
    live=retrieve("/export/metals-live-analysis.zip")
    research=retrieve("/export/metals-research.zip")
    m=json.loads(live.read("manifest.json"))
    if not m.get("combined_cash_totals_available"):
        raise AssertionError("Live combined cash audit not verified")
    result={
        "version":RESEARCH_VERSION,
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "source_live_export_time":m.get("generated_at_utc"),
        "source_research_export":"metals-research.zip",
        "baseline_realised_this_week_gbp":m.get("this_week_net_gbp"),
        "baseline_realised_this_month_gbp":m.get("this_month_net_gbp"),
        "baseline_realised_all_time_gbp":m.get("all_time_net_gbp"),
        "xag_plus_2r_stacking":xag_stack_diagnostic(live),
        "xag_plus_2r_demo_gate":xag_demo_plus_2r_gate(research),
        "xag_basket_harvest":xag_demo_exit_study(research),
        "combined_family_profit_lock":combined_cash_study(live),
        "trading_changes":False,
        "interpretation":"OBSERVATIONAL AND SHADOW-RESEARCH ONLY; no broker-executable portfolio counterfactual yet."
    }
    print("STUDY_JSON",json.dumps(result,default=str,sort_keys=True))
if __name__=="__main__":
    main()
