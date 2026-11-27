import json
import unittest
from datetime import datetime, timedelta, timezone
from metals_young_basket_research import analyse, observation, summarize

START = datetime(2026,11,27,tzinfo=timezone.utc)
def stamp(minutes):
    return (START+timedelta(minutes=minutes)).isoformat()

def marks(asset, pnl, count=8, opened=-60, side="SHORT", prefix=None):
    return [dict(trade_id=f"{prefix or asset}{i}", instrument=asset+"_USD",
                 side=side, opened_at_utc=stamp(opened), estimated_risk_gbp=10,
                 unrealized_gbp=pnl) for i in range(count)]

def sample(minutes, gold, silver, status="COMPLETE"):
    return dict(sampled_at_utc=stamp(minutes),status=status,mark_to_market_complete=1,
                broker_population_matched=1,cash_broker_verified=1,
                xau_open_count=len(gold),xag_open_count=len(silver),
                position_marks_json=json.dumps(gold+silver))

def budget(minutes,asset,count,young=None,status="VERIFIED"):
    return dict(sampled_at_utc=stamp(minutes),asset=asset,status=status,
                broker_open_count=count,young_short_count=count if young is None else young,
                nav_gbp=1000,broker_risk_used_gbp=20,broker_risk_cap_gbp=100)

def inputs(minutes=0,pnl=-3,silver=-3):
    g,s=marks("XAU",pnl),marks("XAG",silver)
    return sample(minutes,g,s),[budget(minutes,"XAU",len(g)),budget(minutes,"XAG",len(s))]

def links():
    return {a:[dict(broker_trade_id=f"{a}{i}",status="CLOSED",side="short",
                    closed_at_utc=stamp(120),realized_pl=-10) for i in range(8)]
            for a in ("XAU","XAG")}

def events_of(events,variant="FIRST_OBSERVATION", band=-.25, trim="ENTIRE_COHORT"):
    return [e for e in events if e["variant"]==variant and e["band_r"]==band and e["comparison"]==trim]

class ResearchTests(unittest.TestCase):
    def test_verified_cohort_and_exact_r(self):
        s,b=inputs()
        o,y=observation(s,b[0],"XAU")
        self.assertEqual((o["status"],len(y),o["young_risk_weighted_r"]),("VERIFIED",8,-.3))
        self.assertEqual(o["young_initial_risk_pct_nav"],8)
        self.assertEqual(o["remaining_risk_pct_nav"],2)
    def test_mismatched_quote_unverified(self):
        s,b=inputs()
        b[0]["status"]="INCOMPLETE"
        o,y=observation(s,b[0],"XAU")
        self.assertEqual((o["status"],len(y)),("UNKNOWN",0))
    def test_stale_snapshot_and_bad_position_proof(self):
        s,b=inputs()
        b[0]["sampled_at_utc"]=stamp(-5)
        self.assertEqual(observation(s,b[0],"XAU")[0]["reason"],"BUDGET_SAMPLE_MISMATCH")
        s["broker_population_matched"]=0
        self.assertEqual(observation(s,b[0],"XAU")[0]["reason"],"INCOMPLETE_POSITION_PROOF")
    def test_malformed_mark_unknown(self):
        g=marks("XAU",-3); g[0]["estimated_risk_gbp"]=0
        self.assertEqual(observation(sample(0,g,[]),budget(0,"XAU",8),"XAU")[0]["reason"],"INVALID_POSITION_MARK")
    def test_weekend_all_manager_young(self):
        g=marks("XAU",-3,opened=-60*80)
        o,y=observation(sample(0,g,[]),budget(0,"XAU",8),"XAU")
        self.assertEqual(len(y),8)
        self.assertEqual(o["age_basis"],"ALL_SHORTS_MANAGER_YOUNG")
    def test_ambiguous_mixed_candle_ages(self):
        g=marks("XAU",-3,opened=-60*80)
        g[0]["opened_at_utc"]=stamp(-60)
        o,y=observation(sample(0,g,[]),budget(0,"XAU",8,young=3),"XAU")
        self.assertEqual(o["reason"],"MIXED_CANDLE_AGES_NOT_IDENTIFIABLE")
        self.assertEqual(len(y),0)
    def test_no_long_or_small_cohort_simulation(self):
        g=marks("XAU",-3,count=7)
        s=sample(0,g,marks("XAG",-3,side="LONG"))
        _,e=analyse([s],[budget(0,"XAU",7),budget(0,"XAG",8,0)],links())
        self.assertEqual(e,[])
    def test_persistence_and_peer_at_exact_sample(self):
        a,ba=inputs(0,silver=0)
        b,bb=inputs(5,silver=-3)
        _,e=analyse([a,b],ba+bb,links())
        self.assertEqual(len(events_of(e)),2)
        p=events_of(e,"PERSISTENT_2")
        self.assertEqual([(x["asset"],x["observed_at_utc"]) for x in p],[("XAU",stamp(5))])
        self.assertEqual(len(events_of(e,"PEER_AT_SAME_BAND")),2)
    def test_gap_and_unknown_reset_persistence(self):
        a,ba=inputs(0);b,bb=inputs(20)
        _,e=analyse([a,b],ba+bb,links())
        self.assertEqual(events_of(e,"PERSISTENT_2"),[])
        c,bc=inputs(5);c["status"]="INCOMPLETE"
        d,bd=inputs(10)
        _,e=analyse([a,c,d],ba+bc+bd,links())
        self.assertEqual(events_of(e,"PERSISTENT_2"),[])
    def test_no_overlapping_cohorts_each_variant(self):
        a,ba=inputs(0);b,bb=inputs(5,pnl=-4)
        _,e=analyse([a,b],ba+bb,links())
        self.assertEqual(len(events_of(e)),2)
        r=events_of(e,trim="WORST_R_25")[0]
        self.assertEqual(r["selected_count"],2)
        self.assertEqual(r["marked_difference_gbp"],14)
    def test_outcome_unknown_duplicate_or_earlier_close(self):
        a,ba=inputs()
        l=links();l["XAU"][0]["closed_at_utc"]=stamp(-1)
        l["XAG"].append(dict(l["XAG"][0]))
        _,e=analyse([a],ba,l)
        for r in events_of(e):
            self.assertEqual(r["comparison_status"],"UNSETTLED_OR_UNVERIFIED")
            self.assertIsNone(r["actual_gross_realised_gbp"])
        self.assertTrue(all(x["execution_authority"]==0 for x in e))
        self.assertTrue(all(x["marked_difference_gbp"] is None for x in summarize(events_of(e))))
    def test_future_marks_cannot_change_first_observation(self):
        a,ba=inputs();b,bb=inputs(5,pnl=-7,silver=-7)
        _,early=analyse([a],ba,links());_,later=analyse([a,b],ba+bb,links())
        self.assertEqual(events_of(early),events_of(later))
    def test_duplicate_source_unknown(self):
        a,ba=inputs()
        o,e=analyse([a,a],ba,links())
        self.assertEqual(len(o),2)
        self.assertTrue(all(x["reason"]=="DUPLICATE_SOURCE_TIMESTAMP" for x in o))
        self.assertEqual(e,[])
    def test_matched_delta_does_not_use_replacement_cohort(self):
        a,ba=inputs(0,pnl=1);b,bb=inputs(15,pnl=-3)
        o,_=analyse([a,b],ba+bb,links())
        self.assertEqual(o[2]["matched_delta_15m_r"],-.4)
        g=marks("XAU",-3,prefix="new")
        b=sample(15,g,marks("XAG",-3))
        o,_=analyse([a,b],ba+bb,links())
        self.assertIsNone(o[2]["matched_delta_15m_r"])

if __name__=="__main__":
    unittest.main()
