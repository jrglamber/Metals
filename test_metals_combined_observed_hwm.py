"""Ensure combined Metals high-water persists, never sums separate lane peaks."""
import sqlite3
import unittest
from datetime import datetime, timezone, timedelta
from metals_combined_observed_hwm import observe

class FakeCore:
    def __init__(self):
        self.conn=sqlite3.connect(":memory:")
        self.conn.row_factory=sqlite3.Row
    def get_conn(self):
        return self.conn

def proof(pnl, at=None):
    return {"ok":True,"scope":"LIVE_XAU_LONG_SHORT_PLUS_LIVE_XAG_SHORT",
            "open_pnl_gbp":pnl,
            "observed_at_utc":at or datetime.now(timezone.utc).isoformat()}

class CombinedObservedHighWaterTests(unittest.TestCase):
    def setUp(self):
        self.core=FakeCore()

    def tearDown(self):
        self.core.conn.close()

    def test_peak_and_giveback_are_combined_and_persistent(self):
        t1=datetime.now(timezone.utc)
        a=observe(self.core,proof(30.67,t1.isoformat()))
        self.assertEqual(a["high_water_status"],"OBSERVED_COMBINED_LIVE")
        self.assertAlmostEqual(a["high_water_gbp"],30.67)
        self.assertEqual(a["giveback_gbp"],0)
        t2=(t1+timedelta(seconds=20)).isoformat()
        b=observe(self.core,proof(61.50,t2))
        self.assertAlmostEqual(b["high_water_gbp"],61.50)
        self.assertEqual(b["high_water_at_utc"],t2)
        c=observe(self.core,proof(21.25,(t1+timedelta(seconds=30)).isoformat()))
        self.assertAlmostEqual(c["high_water_gbp"],61.50)
        self.assertAlmostEqual(c["giveback_gbp"],40.25)
        self.assertAlmostEqual(c["giveback_pct"],65.45)
        self.assertEqual(c["high_water_tracking_started_at_utc"],t1.isoformat())
        self.assertFalse(c["historical_backfill_complete"])
        self.assertFalse(c["affects_trading_or_xau_protection"])

    def test_negative_first_observation_does_not_set_negative_peak(self):
        a=observe(self.core,proof(-12.00))
        self.assertEqual(a["high_water_gbp"],0)
        self.assertEqual(a["giveback_gbp"],12)
        self.assertIsNone(a["giveback_pct"])

    def test_bad_broker_data_does_not_mutate_high_water(self):
        first=observe(self.core,proof(25))
        for invalid in ({"ok":False},proof(None),dict(proof(100),scope="LIVE_XAU_ONLY")):
            with self.assertRaises(ValueError):
                observe(self.core,invalid)
        later=observe(self.core,proof(20))
        self.assertEqual(first["high_water_gbp"],later["high_water_gbp"])

if __name__=="__main__":
    unittest.main()
