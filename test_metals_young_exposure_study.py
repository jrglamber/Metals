import unittest

from metals_young_exposure_study import analyse_exposure, describe_bins
from test_metals_young_basket_research import marks, sample, budget, stamp


def pair(minutes, gold, silver, gold_young=None, silver_young=None, family="FAMILY_A"):
    s = sample(minutes, gold, silver)
    s.update(total_open_count=len(gold)+len(silver), family_campaign_id=family)
    b = [budget(minutes, "XAU", len(gold), gold_young),
         budget(minutes, "XAG", len(silver), silver_young)]
    for x in b:
        if not x["broker_open_count"]:
            x["broker_risk_used_gbp"] = 0
    return s, b


def close(tid, minutes, pnl, **overrides):
    return dict(dict(broker_trade_id=tid, status="CLOSED_BROKER", side="short",
                     closed_at_utc=stamp(minutes), realized_pl=pnl), **overrides)


def study(pairs, links=None):
    samples = [s for s,b in pairs]
    budgets = [r for s,b in pairs for r in b]
    return analyse_exposure(samples, budgets, links or {})


class ExposureTests(unittest.TestCase):
    def test_combined_nav_not_summed_and_risk_not_double_counted(self):
        p = pair(0, marks("XAU", -4, 2), marks("XAG", -3, 3))
        p[1][1]["nav_gbp"] = 999.5
        r = study([p])[0]
        self.assertEqual(r["status"], "VERIFIED_SAMPLE")
        self.assertEqual(r["nav_gbp"], 999.5)
        self.assertEqual(r["nav_spread_gbp"], .5)
        self.assertEqual(r["young_original_risk_gbp"], 50)
        self.assertEqual(r["young_mark_gbp"], -17)
        self.assertEqual(r["combined_remaining_stop_risk_gbp"], 40)
        self.assertEqual(r["observed_young_risk_plus_definite_gross_loss_gbp"], 50)
        self.assertAlmostEqual(r["young_original_risk_pct_nav"], 50/999.5*100)
    def test_quote_mark_can_exceed_nominal_risk(self):
        r = study([pair(0, marks("XAU", -15, 1), [])])[0]
        self.assertEqual(r["young_mark_beyond_original_gbp"], 5)
        self.assertEqual(r["young_original_risk_gbp"], 10)
    def test_mismatch_unknown_not_zero(self):
        p = pair(0, marks("XAU", 1, 2), marks("XAG", 1, 3))
        p[1][1]["nav_gbp"] = 990
        r = study([p])[0]
        self.assertEqual(r["reason"], "NAV_MISMATCH")
        self.assertNotIn("young_original_risk_gbp", r)
    def test_missing_budget_or_total_count_unknown(self):
        p = pair(0, marks("XAU", 1, 2), [])
        p[1].pop()
        self.assertEqual(study([p])[0]["status"], "UNKNOWN")
        p = pair(0, [], [])
        p[0]["total_open_count"] = 1
        self.assertEqual(study([p])[0]["reason"], "TOTAL_OPEN_COUNT_MISMATCH")
    def test_maturity_of_longs_not_assumed(self):
        p = pair(0, marks("XAU", 1, 2, side="LONG"), [], gold_young=0)
        self.assertEqual(study([p])[0]["reason"], "LONG_MATURITY_NOT_EXPORTED")
    def test_closing_loser_does_not_release_its_loss_from_estimate(self):
        g, s = marks("XAU", -8, 2), marks("XAG", -3, 1)
        p = pair(0, g, s)
        q = pair(5, g[1:], s)
        q[1][0]["broker_risk_used_gbp"] = 5
        r = study([p,q], {"XAU":[close("XAU0", 4, -10)]})
        self.assertEqual(r[0]["observed_young_risk_plus_definite_gross_loss_gbp"], 30)
        self.assertEqual(r[1]["observed_young_risk_plus_definite_gross_loss_gbp"], 30)
        self.assertLess(r[1]["combined_remaining_stop_risk_gbp"], r[0]["combined_remaining_stop_risk_gbp"])
        self.assertEqual(r[1]["observed_definite_young_closed_gross_loss_gbp"], 10)
    def test_positive_closes_are_not_credited(self):
        g = marks("XAU", -3, 3)
        p = pair(0, g, [])
        q = pair(5, g[2:], [])
        r = study([p,q], {"XAU":[close("XAU0", 4, -7), close("XAU1", 4, 50)]})
        self.assertEqual(r[1]["observed_young_risk_plus_definite_gross_loss_gbp"], 17)
        self.assertEqual(r[1]["observed_definite_young_closed_count"], 2)
    def test_future_close_is_not_consumed(self):
        g = marks("XAU", -3, 2)
        p = pair(0, g, [])
        q = pair(5, g[1:], [])
        r = study([p,q], {"XAU":[close("XAU0", 10, -7)]})
        self.assertEqual(r[1]["observed_definite_young_closed_gross_loss_gbp"], 0)
        self.assertEqual(r[1]["observed_unsettled_count"], 1)
    def test_maturity_is_not_a_close_and_old_age_kept_separate(self):
        g = marks("XAU", -3, 1, opened=-60*50)
        p = pair(0, g, [])
        q = pair(5, g, [], gold_young=0)
        r = pair(10, [], [])
        rows = study([p,q,r], {"XAU":[close("XAU0", 9, -7)]})
        self.assertEqual(rows[1]["observed_unsettled_count"], 0)
        self.assertEqual(rows[1]["young_short_count"], 0)
        self.assertEqual(rows[2]["observed_definite_young_closed_gross_loss_gbp"], 0)
        self.assertEqual(rows[2]["observed_age_unproven_closed_gross_loss_gbp"], 7)
    def test_dashboard_family_reset_does_not_erase_known_loss(self):
        g = marks("XAU", -3, 2)
        r = study([pair(0,g,[]), pair(5,g[1:],[]), pair(10,g[1:],[],family="FAMILY_B")],
                  {"XAU":[close("XAU0",4,-7)]})
        self.assertEqual(r[2]["observed_definite_young_closed_gross_loss_gbp"], 7)
        self.assertEqual(r[0]["cycle_start_utc"], r[2]["cycle_start_utc"])
    def test_terminal_flat_records_losses_then_resets(self):
        g = marks("XAU", -3, 1)
        r = study([pair(0,g,[]),pair(5,[],[]),pair(10,marks("XAU",0,1,prefix="NEW"),[])],
                  {"XAU":[close("XAU0",4,-7)]})
        self.assertEqual(r[1]["observed_definite_young_closed_gross_loss_gbp"], 7)
        self.assertEqual(r[2]["observed_definite_young_closed_gross_loss_gbp"], 0)
        self.assertEqual(r[2]["coverage"], "SAMPLED_FROM_VERIFIED_FLAT")
    def test_unknown_gap_preserves_losses_and_breaks_deltas(self):
        g = marks("XAU",-3,3)
        q = pair(5,g[1:],[])
        bad = pair(10,g[1:],[])
        bad[0]["status"] = "INCOMPLETE"
        r = study([pair(0,g,[]),q,bad,pair(20,g[1:],[])],
                  {"XAU":[close("XAU0",4,-7)]})
        self.assertEqual(r[3]["observed_definite_young_closed_gross_loss_gbp"], 7)
        self.assertEqual(r[3]["coverage_gaps"], 1)
        self.assertIsNone(r[3]["matched_young_delta_15m_r"])
    def test_flat_evidence_cannot_be_inconsistent(self):
        p = pair(0,[],[])
        p[1][0]["broker_risk_used_gbp"] = 1
        self.assertEqual(study([p])[0]["reason"], "INCONSISTENT_FLAT_EVIDENCE")
    def test_duplicate_link_unsettled(self):
        p = pair(0,marks("XAU", -3,1),[])
        c = close("XAU0",4,-7)
        r = study([p,pair(5,[],[])], {"XAU":[c,c]})
        self.assertEqual(r[1]["observed_unsettled_count"], 1)
        self.assertEqual(r[1]["observed_definite_young_closed_gross_loss_gbp"], 0)
    def test_cohort_deterioration_combined_identical_risk(self):
        p = pair(0,marks("XAU",1,2),marks("XAG",2,1))
        q = pair(15,marks("XAU",-4,2),marks("XAG",-3,1))
        r = study([p,q])
        self.assertEqual(r[1]["matched_young_delta_15m_r"], -.5)
        self.assertEqual(r[1]["matched_young_count_15m"], 3)
    def test_new_entries_require_opening_after_adjacent_sample(self):
        g = marks("XAU",0,1)
        new = marks("XAU",0,1,opened=2,prefix="NEW")
        late_seen = marks("XAG",0,1,opened=-3)
        r = study([pair(0,g,[]),pair(5,g+new,late_seen)])
        self.assertEqual(r[1]["new_young_count_since_previous_sample"],1)
        self.assertEqual(r[1]["new_young_original_risk_since_previous_sample_gbp"],10)
    def test_bin_counts_observations_not_entry_decisions(self):
        p = pair(0,marks("XAU", -3,2),[])
        q = pair(5,marks("XAU", -3,1),[])
        r = study([p,q], {"XAU":[close("XAU1",4,-10)]})
        b = describe_bins(r, [2])[0]
        self.assertEqual(b["original_risk_at_or_above_samples"], 1)
        self.assertEqual(b["risk_plus_known_losses_at_or_above_samples"], 2)
        self.assertEqual(b["observed_physical_cycles"], 1)
        self.assertTrue(all(x["execution_authority"]==0 for x in r+[b]))
    def test_settled_id_reappearance_unknown(self):
        g = marks("XAU", -3,2)
        r = study([pair(0,g,[]),pair(5,g[1:],[]),pair(10,g,[])],
                  {"XAU":[close("XAU0",4,-7)]})
        self.assertEqual(r[2]["reason"],"SETTLED_TRADE_REAPPEARED")
    def test_duplicate_sample_and_unsettled_age(self):
        p = pair(0,marks("XAU",-1,1),[])
        r = study([p,p])
        self.assertEqual(r[0]["reason"],"DUPLICATE_SOURCE_TIMESTAMP")
    def test_already_closed_before_last_seen_not_admitted(self):
        p = pair(0,marks("XAU",-1,1),[])
        r = study([p,pair(5,[],[])], {"XAU":[close("XAU0",-1,-9)]})
        self.assertEqual(r[1]["observed_unsettled_count"],1)


if __name__ == "__main__":
    unittest.main()
