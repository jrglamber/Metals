"""Observed-entry contraction challengers; no execution authority or broker calls.

Uses ONLY marks strictly before each actual live entry; outcome is attached
afterwards for matched-entry attribution, not a portfolio counterfactual.
"""
from bisect import bisect_left, bisect_right
from datetime import datetime
import json
import math

VERSION = "contraction_entry_research_v1_2026_11_25"
POLICIES = ("BASELINE", "C1_1H_IMPROVING", "C2_3H_IMPROVING",
            "C3_SIGNIFICANT_CONTRACTION", "C4_PROTECTIVE_RELEASE_EXCEPTION")
FIELDS = ("asset","side","broker_trade_id","opened_at_utc","status","closed_at_utc",
          "actual_gross_realised_gbp","latest_pre_entry_utc","matched_1h","matched_3h",
          "delta_1h_gbp","delta_3h_gbp","matched_peak_3h_gbp","giveback_3h_fraction",
          "protective_stop_moved","harvest_observed","release_evidence","BASELINE",
          "C1_1H_IMPROVING","C2_3H_IMPROVING","C3_SIGNIFICANT_CONTRACTION",
          "C4_PROTECTIVE_RELEASE_EXCEPTION","execution_authority")
MAX_GAP = 900
GIVEBACK = .25

def _time(x):
    try:
        d = datetime.fromisoformat(str(x).replace("Z","+00:00"))
        return d.timestamp() if d.tzinfo else None
    except (ValueError,TypeError,OverflowError):
        return None

def _num(x):
    try:
        n = float(x)
        return n if not isinstance(x,bool) and math.isfinite(n) else None
    except (ValueError,TypeError):
        return None

def _mark_map(row, asset):
    if row.get("status") != "COMPLETE":
        return None
    try:
        marks = json.loads(row.get("position_marks_json") or "null")
        if not isinstance(marks,list):
            return None
        result = {}
        for m in marks:
            if m.get("instrument") != asset+"_USD":
                continue
            tid = str(m.get("trade_id") or "")
            pnl = _num(m.get("unrealized_gbp"))
            if not tid or tid in result or pnl is None:
                return None
            result[tid] = (pnl,_num(m.get("stop_price")),str(m.get("side") or "").upper())
        count = _num(row.get(asset.lower()+"_open_count"))
        return result if count is not None and count==len(result) else None
    except (TypeError,ValueError,AttributeError):
        return None

def _matched(old,new):
    ids = old.keys() & new.keys()
    if not ids:
        return None,0
    return sum(new[i][0]-old[i][0] for i in ids),len(ids)

def _flag(delta):
    return "UNKNOWN" if delta is None else "ALLOW" if delta>0 else "BLOCK"

def analyse(samples, links):
    rows = sorted(((t,r) for r in samples if (t:=_time(r.get("sampled_at_utc"))) is not None),key=lambda x:x[0])
    times = [x[0] for x in rows]
    out = []
    for asset, trades in links.items():
        maps = [_mark_map(r,asset) for _,r in rows]
        closed = sorted(((t,str(r.get("close_reason") or "").lower())
                         for r in trades if (t:=_time(r.get("closed_at_utc"))) is not None))
        close_times = [x[0] for x in closed]
        for r in trades:
            entry = _time(r.get("opened_at_utc"))
            tid = str(r.get("broker_trade_id") or "")
            if entry is None or not tid:
                continue
            last = bisect_left(times,entry)-1
            if last < 0:
                continue
            result = {k:None for k in FIELDS}
            result.update(asset=asset,side=r.get("side"),broker_trade_id=tid,
                          opened_at_utc=r.get("opened_at_utc"),status=r.get("status"),
                          closed_at_utc=r.get("closed_at_utc"),
                          actual_gross_realised_gbp=_num(r.get("realized_pl")) if
                          str(r.get("status") or "").upper().startswith("CLOSED") else None,
                          BASELINE="ALLOW",execution_authority=0)
            for policy in POLICIES[1:]:
                result[policy]="UNKNOWN"
            current = maps[last]
            if entry-times[last]>MAX_GAP or current is None or tid in current:
                out.append(result)
                continue
            result["latest_pre_entry_utc"]=rows[last][1]["sampled_at_utc"]
            if not current:
                # No incumbent basket to contract: allow an observed flat start.
                for policy in POLICIES[1:]:
                    result[policy]="ALLOW"
                result["release_evidence"]="FLAT_START"
                out.append(result)
                continue
            prior = {}
            for hours in (1,3):
                target = times[last]-hours*3600
                j = bisect_right(times,target)-1
                if j>=0 and target-times[j]<=MAX_GAP and maps[j] is not None:
                    prior[hours]=j
                    delta,n=_matched(maps[j],current)
                    result["delta_%dh_gbp"%hours]=delta
                    result["matched_%dh"%hours]=n
                    result[POLICIES[1 if hours==1 else 2]]=_flag(delta)
            j=prior.get(3)
            if j is not None:
                # Fix the incumbent cohort across all samples, requiring
                # continuous observed marks and no gap longer than 15 minutes.
                window=maps[j:last+1]
                if all(m is not None for m in window) and all(
                    b-a<=MAX_GAP for a,b in zip(times[j:last],times[j+1:last+1])):
                    common=set(current)
                    for m in window:
                        common.intersection_update(m)
                    if common:
                        path=[sum(m[i][0] for i in common) for m in window]
                        peak=max(path)
                        frac=(peak-path[-1])/peak if peak>0 else None
                        result["matched_peak_3h_gbp"]=peak
                        result["giveback_3h_fraction"]=frac
                        # Significant = positive incumbent peak, >=25% giveback.
                        # No positive peak is unknown, not evidence of safety.
                        if frac is not None:
                            result[POLICIES[3]]="BLOCK" if frac>=GIVEBACK else "ALLOW"
            j=prior.get(1)
            if j is not None:
                old=maps[j]
                common=old.keys() & current.keys()
                known=bool(common) and all(old[i][1] is not None and current[i][1] is not None
                    and old[i][2]==current[i][2] and current[i][2] in ("LONG","SHORT") for i in common)
                protected=any(old[i][1] is not None and current[i][1] is not None and
                    old[i][2]==current[i][2] and
                    ((current[i][2]=="LONG" and current[i][1]>old[i][1]) or
                     (current[i][2]=="SHORT" and current[i][1]<old[i][1])) for i in common)
                events=closed[bisect_right(close_times,times[j]):bisect_right(close_times,times[last])]
                harvest=any("harvest" in reason for _,reason in events)
                unknown_close=any(not reason for _,reason in events)
                result["protective_stop_moved"]=True if protected else False if known else None
                result["harvest_observed"]=True if harvest else None if unknown_close else False
                result["release_evidence"]=("PROTECTIVE_OR_HARVEST_OBSERVED" if protected or harvest else
                    "NO_OBSERVED_PROTECTIVE_RELEASE" if known and not unknown_close else "UNKNOWN")
            if result[POLICIES[3]]=="ALLOW":
                result[POLICIES[4]]="ALLOW"
            elif result[POLICIES[3]]=="BLOCK":
                if result["release_evidence"]=="PROTECTIVE_OR_HARVEST_OBSERVED":
                    result[POLICIES[4]]="ALLOW"
                elif result["release_evidence"]=="NO_OBSERVED_PROTECTIVE_RELEASE":
                    result[POLICIES[4]]="BLOCK"
            out.append(result)
    return sorted(out,key=lambda r:(r["opened_at_utc"],r["asset"],r["broker_trade_id"]))

def summarize(rows):
    output=[]
    for asset in ("XAU","XAG"):
        for side in ("LONG","SHORT"):
            lane=[r for r in rows if r["asset"]==asset and str(r["side"]).upper()==side]
            if not lane:
                continue
            for policy in POLICIES:
                blocked=[r for r in lane if r[policy]=="BLOCK"]
                settled=[r for r in blocked if r["actual_gross_realised_gbp"] is not None]
                vals=[r["actual_gross_realised_gbp"] for r in settled]
                output.append(dict(asset=asset,side=side,policy=policy,observed_entries=len(lane),
                    allowed=sum(r[policy]=="ALLOW" for r in lane),blocked=len(blocked),
                    unknown=sum(r[policy]=="UNKNOWN" for r in lane),blocked_closed=len(settled),
                    blocked_unsettled=len(blocked)-len(settled),blocked_winners=sum(v>0 for v in vals),
                    blocked_losers=sum(v<0 for v in vals),
                    blocked_actual_gross_gbp=round(sum(vals),2) if vals else None,
                    execution_authority=0))
    return output

SUMMARY_FIELDS=("asset","side","policy","observed_entries","allowed","blocked","unknown",
                "blocked_closed","blocked_unsettled","blocked_winners","blocked_losers",
                "blocked_actual_gross_gbp","execution_authority")
