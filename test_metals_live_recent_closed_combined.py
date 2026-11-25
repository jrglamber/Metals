"""LIVE recently closed Metals must combine XAU and XAG, never practice."""
import sqlite3
import unittest
from contextlib import contextmanager
import pathlib

import metals_live_recent_closed_combined as recent
import metals_recent_closed_dom_override as dom
import metals_recent_closed_live_patch as legacy


class Core:
    def __init__(self):
        self.db=sqlite3.connect(":memory:")
        self.db.row_factory=sqlite3.Row
        self.db.executescript("""
        CREATE TABLE metals_xau_live_trade_links(
          id INTEGER PRIMARY KEY,status TEXT,broker_trade_id TEXT,
          closed_at_utc TEXT,side TEXT,entry_price REAL,closed_price REAL,
          filled_units REAL,estimated_risk_amount REAL,
          realized_pl REAL,close_reason TEXT);
        CREATE TABLE metals_xag_live_trade_links(
          id INTEGER PRIMARY KEY,status TEXT,broker_trade_id TEXT,
          closed_at_utc TEXT,side TEXT,entry_price REAL,
          filled_units REAL,estimated_risk_amount REAL,
          realized_pl REAL,close_reason TEXT);
        INSERT INTO metals_xau_live_trade_links VALUES
          (1,'CLOSED_VERIFIED','xau1','2026-11-25T12:20:00Z','short',3600,3511,-0.1,8,10,'MFE25'),
          (2,'OPEN','xau2',NULL,'long',3450,NULL,0.1,8,NULL,NULL),
          (3,'CLOSE_RETRY','xau3','2026-11-25T12:50:00Z','short',3600,NULL,-0.1,8,NULL,'PENDING');
        INSERT INTO metals_xag_live_trade_links VALUES
          (1,'CLOSED_RECONCILED','xag1','2026-11-25T12:40:00Z','short',43.1,-1,2,1.5,'BROKER_RECONCILED_CLOSED'),
          (2,'CLOSED_VERIFIED','xag2','2026-11-25T12:30:00Z','short',42.8,-1,2,NULL,'MFE25'),
          (3,'OPEN','xag3',NULL,'short',44.4,-1,2,NULL,NULL);
        """)
    @contextmanager
    def get_conn(self):
        yield self.db


class ClosedLiveTests(unittest.TestCase):
    def setUp(self):
        self.core=Core()
        self.addCleanup(self.core.db.close)

    def test_mixed_live_rows_sorted_with_xag_closed_and_null_pl_preserved(self):
        snap=recent.snapshot(self.core,limit=20)
        self.assertTrue(snap["ok"])
        self.assertEqual((snap["xau_count"],snap["xag_count"]), (1,2))
        self.assertEqual([x["broker_trade_id"] for x in snap["rows"]],
                         ["xag1","xag2","xau1"])
        self.assertIsNone(snap["rows"][1]["realized_pl"])
        self.assertIsNone(snap["rows"][1]["approx_r"])
        self.assertIsNone(snap["rows"][0]["exit_price"])
        self.assertEqual(snap["rows"][2]["approx_r"],1.25)
        self.assertTrue(snap["trade_pl_not_net_broker_cash"])
        self.assertTrue(snap["practice_excluded"])
        self.assertFalse(snap["broker_order_writes"])

    def test_limit_applies_after_both_assets_are_merged(self):
        self.assertEqual([x["asset"] for x in recent.rows(self.core,2)],
                         ["XAGUSD","XAGUSD"])
        self.assertEqual(dom._rows(self.core,30),recent.rows(self.core,30))

    def test_backend_fallback_and_dashboard_use_combined_labels(self):
        html=legacy._live_closed_html(self.core)
        self.assertIn("XAGUSD",html)
        self.assertIn("XAUUSD",html)
        self.assertIn("Trade P/L",html)
        self.assertNotIn("LIVE XAU ONLY",html)
        self.assertEqual(dom.ROUTE,"/api/live-metals-recently-closed")
        self.assertIn("LIVE XAU LONG / SHORT + XAG SHORT",dom.UI)
        self.assertIn("pep-closed-asset-filter",dom.UI)
        self.assertNotIn("LIVE XAU ONLY",dom.UI)

    def test_no_unverified_closure_rows_pass(self):
        with self.core.get_conn() as conn:
            conn.execute("DELETE FROM metals_xau_live_trade_links")
            conn.execute("DELETE FROM metals_xag_live_trade_links")
            conn.commit()
        snap=recent.snapshot(self.core)
        self.assertEqual(snap["rows"],[])
        self.assertEqual(snap["count"],0)

if __name__=="__main__":
    unittest.main()
