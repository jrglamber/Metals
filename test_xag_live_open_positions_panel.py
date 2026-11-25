"""LIVE XAG broker-open positions must appear in Metals Open Trades section."""
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import metals_xag_live_open_positions_panel as panel
import xag_short_live_override as live


class FakeCore:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE metals_xag_live_trade_links(
                id INTEGER PRIMARY KEY,raw_signal_id INTEGER,broker_trade_id TEXT,
                side TEXT,status TEXT,current_stop_price REAL,stop_price REAL,
                active_exit_policy TEXT,manager_last_decision TEXT,
                manager_high_water_r REAL,estimated_risk_amount REAL,
                manager_mfe_r REAL,manager_mae_r REAL,
                manager_last_review_candles INTEGER,
                manager_last_review_signal_id INTEGER,manager_last_reason TEXT
            );
            CREATE TABLE raw_signals(id INTEGER PRIMARY KEY,pair TEXT);
        """)
        self.db.executemany(
            "INSERT INTO metals_xag_live_trade_links "
            "(id,raw_signal_id,broker_trade_id,side,status,current_stop_price,"
            "stop_price,active_exit_policy,estimated_risk_amount) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(1,3110,"107321","short","OPEN",47.5,47.5,"MFE_GIVEBACK_25",2.0),
             (2,3112,"107399","short","OPEN",47.6,47.6,"MFE_GIVEBACK_25",2.0),
             (3,3132,"107528","short","OPEN",47.7,47.7,"MFE_GIVEBACK_25",2.0)]
        )
        self.db.execute("INSERT INTO raw_signals(id,pair) VALUES(3539,'XAGUSD')")
        self.db.execute("""UPDATE metals_xag_live_trade_links SET
            manager_mfe_r=0.8,manager_mae_r=-0.25,manager_high_water_r=0.8,
            manager_last_review_candles=52,manager_last_review_signal_id=3539,
            manager_last_decision='EXTEND',manager_last_reason='XAG SHORT MFE25 active'
            WHERE id=1""")
        self.db.execute("""UPDATE metals_xag_live_trade_links SET
            manager_mfe_r=1.0,manager_mae_r=-0.50,
            manager_last_review_candles=50,manager_last_review_signal_id=3535
            WHERE id=2""")
        self.db.commit()
    @contextmanager
    def get_conn(self):
        yield self.db


def broker_open():
    return {"ok": True, "xag": [
        {"id": "107321", "instrument": "XAG_USD", "currentUnits": "-1",
         "price": "46.1", "unrealizedPL": "1.25",
         "openTime": "2026-11-05T20:00:07Z", "stopLossOrderID": "222"},
        {"id": "107399", "instrument": "XAG_USD", "currentUnits": "-1",
         "price": "46.2", "unrealizedPL": "1.50",
         "openTime": "2026-11-06T20:00:14Z", "stopLossOrderID": "223"},
        {"id": "107528", "instrument": "XAG_USD", "currentUnits": "-1",
         "price": "46.3", "unrealizedPL": "0.15",
         "openTime": "2026-11-12T15:00:07Z", "stopLossOrderID": "224"},
    ]}


class LiveXagOpenPositionsTests(unittest.TestCase):
    def setUp(self):
        self.core = FakeCore()
        self.addCleanup(self.core.db.close)

    def test_three_live_xag_shorts_are_displayed_beside_xau(self):
        with patch.object(live,"_open_trades",return_value=broker_open()):
            result = panel.snapshot(self.core)
            html = panel.render(self.core)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"],3)
        self.assertEqual(result["short_count"],3)
        self.assertEqual(result["tracked_count"],3)
        self.assertEqual(result["mature_manager_trade_count"],2)
        self.assertEqual(result["mature_manager_current_count"],1)
        self.assertEqual(result["mature_manager_stale_count"],1)
        first=result["positions"][0]
        self.assertEqual(first["manager_mfe_r"],0.8)
        self.assertEqual(first["manager_mae_r"],-0.25)
        self.assertEqual(first["manager_review_candles"],52)
        self.assertEqual(first["manager_review_status"],"CURRENT")
        self.assertEqual(first["manager_last_reason"],"XAG SHORT MFE25 active")
        self.assertAlmostEqual(first["current_r_approx"],0.625)
        self.assertAlmostEqual(first["mfe_giveback_r_approx"],0.175)
        self.assertAlmostEqual(first["mfe_giveback_pct_approx"],21.88,places=2)
        self.assertEqual(first["mfe25_floor_r"],0.6)
        self.assertEqual(result["positions"][1]["manager_review_status"],"PRIOR_SIGNAL")
        self.assertIsNone(result["positions"][2]["mfe_giveback_r_approx"])
        self.assertEqual(result["untracked_broker_count"],0)
        self.assertTrue(result["broker_link_reconciled"])
        self.assertEqual(result["unrealized_gbp"],2.90)
        self.assertIn("LIVE — XAG SHORT positions",html)
        self.assertIn("107528",html)
        self.assertIn("MFE_GIVEBACK_25",html)
        self.assertIn("£2.90",html)
        self.assertIn("Practice XAG positions are excluded",html)
        self.assertIn("MFE (R)",html)
        self.assertIn("MAE (R)",html)
        self.assertIn("Giveback from MFE",html)
        self.assertIn("21.9%",html)
        self.assertIn("52c / CURRENT",html)
        self.assertNotIn("No LIVE XAG trades currently open",html)

    def test_error_does_not_claim_zero_live_positions(self):
        with patch.object(live,"_open_trades",return_value={
            "ok":False,"error":"oanda_broker_unavailable","xag":[]
        }):
            result=panel.snapshot(self.core)
            html=panel.render(self.core)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["count"])
        self.assertIsNone(result["unrealized_gbp"])
        self.assertIn("broker read unavailable",html)
        self.assertIn("NOT a report of zero open trades",html)

    def test_unknown_broker_trade_visible_but_not_claimed_tracked(self):
        snap=broker_open()
        snap["xag"].append({
            "id":"999999","instrument":"XAG_USD","currentUnits":"-1",
            "price":"47","unrealizedPL":"-1.5"
        })
        with patch.object(live,"_open_trades",return_value=snap):
            result=panel.snapshot(self.core)
            html=panel.render(self.core)
        self.assertEqual(result["count"],4)
        self.assertEqual(result["tracked_count"],3)
        self.assertEqual(result["untracked_broker_count"],1)
        self.assertFalse(result["broker_link_reconciled"])
        self.assertIn("999999",html)
        self.assertIn("UNLINKED",html)

    def test_missing_pl_is_not_silently_zero(self):
        snap=broker_open()
        del snap["xag"][2]["unrealizedPL"]
        with patch.object(live,"_open_trades",return_value=snap):
            result=panel.snapshot(self.core)
            html=panel.render(self.core)
        self.assertFalse(result["unrealized_complete"])
        self.assertIsNone(result["unrealized_gbp"])
        self.assertIn("INCOMPLETE",html)

    def test_empty_broker_is_distinct_from_unavailable_and_orphan_warns(self):
        with patch.object(live,"_open_trades",return_value={"ok":True,"xag":[]}):
            result=panel.snapshot(self.core)
            html=panel.render(self.core)
        self.assertEqual(result["count"],0)
        self.assertEqual(result["orphan_local_count"],3)
        self.assertFalse(result["broker_link_reconciled"])
        self.assertIn("No LIVE XAG trades currently open",html)
        self.assertIn("MISMATCH",html)

    def test_broker_derived_content_html_escaped(self):
        snap=broker_open()
        snap["xag"][0]["id"]="<script>alert(1)</script>"
        with patch.object(live,"_open_trades",return_value=snap):
            html=panel.render(self.core)
        self.assertNotIn("<script>",html)
        self.assertIn("&lt;script&gt;",html)


if __name__ == "__main__":
    unittest.main()
