"""Regression for the ACTUAL live Metals open-trades browser loader."""
import pathlib
import unittest
from metals_live_positions_combined import combine

class CombinedLivePositionsTests(unittest.TestCase):
    def xau(self):
        return {"ok":True,"trades":[
            {"id":"201","instrument":"XAU_USD","side":"short","units":-0.1,"price":"3871.410","unrealized_pl":0.05,"open_time":"2026-11-12T15:00:00Z"},
            {"id":"202","instrument":"XAU_USD","side":"short","units":-0.1,"price":"4039.130","unrealized_pl":12.6,"open_time":"2026-11-11T14:00:00Z"}]}
    def xag(self):
        return {"ok":True,"count":3,"short_count":3,"broker_link_reconciled":True,"untracked_broker_count":0,
            "positions":[
              {"asset":"XAG_USD","side":"SHORT","units":-1,"entry_price":47.1,"broker_trade_id":"301","tracked":True,"opened_at_utc":"2026-11-05T20:00:00Z","manager":"MFE_GIVEBACK_25"},
              {"asset":"XAG_USD","side":"SHORT","units":-1,"entry_price":47.2,"broker_trade_id":"302","tracked":True,"opened_at_utc":"2026-11-06T20:00:00Z","manager":"MFE_GIVEBACK_25"},
              {"asset":"XAG_USD","side":"SHORT","units":-1,"entry_price":47.3,"broker_trade_id":"303","tracked":True,"opened_at_utc":"2026-11-12T15:00:07Z","manager":"MFE_GIVEBACK_25"}]}
    def test_both_live_lanes_and_five_total(self):
        out=combine(self.xau(),self.xag())
        self.assertTrue(out["ok"])
        self.assertEqual((out["xau_count"],out["xag_short_count"],out["count"]),(2,3,5))
        self.assertEqual([x["instrument"] for x in out["trades"]],["XAU_USD"]*2+["XAG_USD"]*3)
        self.assertTrue(out["practice_excluded"])
    def test_missing_broker_data_not_false_zero(self):
        out=combine(self.xau(),{"ok":False,"reason":"OANDA down"})
        self.assertTrue(out["partial"])
        self.assertIsNone(out["count"])
        self.assertIsNone(out["xag_count"])
        self.assertEqual(len(out["trades"]),2)
        self.assertIn("OANDA down",out["xag_error"])
    def test_missing_xau_still_shows_xag(self):
        out=combine({"ok":False,"trades":[]},self.xag())
        self.assertFalse(out["ok"])
        self.assertEqual(len(out["trades"]),3)
    def test_unlinked_broker_trade_visible_not_masked(self):
        x=self.xag()
        x["positions"][2]["tracked"]=False
        x["broker_link_reconciled"]=False
        out=combine(self.xau(),x)
        self.assertEqual(out["trades"][-1]["tracking"],"UNLINKED — CHECK")
        self.assertFalse(out["xag_broker_link_reconciled"])
    def test_native_loader_and_top_tile_use_combined_route(self):
        src=pathlib.Path("metals_dashboard_clean_entrypoint.py").read_text()
        self.assertIn('@core.app.get("/api/live-metals-open-positions")',src)
        self.assertIn("fetch('/api/live-metals-open-positions'",src)
        self.assertNotIn("fetch('/api/live-xau-open-positions'",src)
        self.assertIn("xag_open.snapshot(core)",src)
        tile=pathlib.Path("metals_live_candidate_support_patch.py").read_text()
        self.assertIn("fetch('/api/live-metals-open-positions'",tile)
        self.assertIn("LIVE XAG SHORT",tile)

if __name__=="__main__":
    unittest.main()
