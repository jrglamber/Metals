"""Read-only XAU live both-side manager data must be broker/ledger linked."""
import sqlite3
import unittest
from contextlib import contextmanager

import metals_xau_live_open_positions_panel as panel


class Core:
    def __init__(self):
        self.db=sqlite3.connect(":memory:")
        self.db.row_factory=sqlite3.Row
        self.db.executescript("""
        CREATE TABLE raw_signals(id INTEGER PRIMARY KEY,pair TEXT);
        INSERT INTO raw_signals VALUES (900,'XAUUSD');
        CREATE TABLE metals_xau_live_trade_links(
            broker_trade_id TEXT, side TEXT, status TEXT, entry_price REAL,
            stop_price REAL, current_stop_price REAL,
            estimated_risk_amount REAL, requested_risk_amount REAL,
            active_exit_policy TEXT, manager_current_r REAL,
            manager_high_water_r REAL, manager_mfe_r REAL,
            manager_mae_r REAL, manager_last_review_candles INTEGER,
            manager_last_review_signal_id INTEGER,
            manager_last_decision TEXT, manager_last_reason TEXT);
        INSERT INTO metals_xau_live_trade_links VALUES (
           '1001','short','OPEN',3625,3710,3555,8,8,
           'MFE_GIVEBACK_25',1.3,2.0,2.0,-0.25,51,900,'EXTEND','protect profit');
        INSERT INTO metals_xau_live_trade_links VALUES (
           '1002','long','OPEN',3600,3500,3512,8,8,
           'ATR2_CHANDELIER',0.75,1.3,1.3,-0.5,47,900,'HOLD_MIN_48','young');
        """)
    @contextmanager
    def get_conn(self):
        yield self.db
    def metals_xau_live_broker_snapshot(self,include_account_summary=False):
        a={"id":"1001","instrument":"XAU_USD","currentUnits":"-0.1",
           "openTime":"2026-11-23T06:00:00Z","price":"3625",
           "unrealizedPL":"12.0"}
        b={"id":"1002","instrument":"XAU_USD","currentUnits":"0.1",
           "openTime":"2026-11-23T07:00:00Z","price":"3600",
           "unrealizedPL":"6.0"}
        return {"ok":True,"owned_open_trades":[b],
                "foreign_xau_short_trades":[a],
                "all_xau_trades":[a,b]}

class XauBothSidesTests(unittest.TestCase):
    def setUp(self):
        self.core=Core()
        self.addCleanup(self.core.db.close)

    def test_short_is_not_hidden_by_long_only_owned_broker_population(self):
        out=panel.snapshot(self.core)
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"],2)
        self.assertEqual(out["untracked_broker_count"],0)
        self.assertEqual(out["mature_manager_trade_count"],1)
        self.assertEqual(out["mature_manager_current_count"],1)
        short,long=out["trades"]
        self.assertEqual(short["side"],"short")
        self.assertTrue(short["tracked"])
        self.assertEqual(short["manager_mfe_r"],2.0)
        self.assertEqual(short["manager_mae_r"],-0.25)
        self.assertEqual(short["manager_review_candles"],51)
        self.assertEqual(short["manager_review_status"],"CURRENT")
        self.assertEqual(short["current_r_approx"],1.5)
        self.assertEqual(short["mfe_giveback_r_approx"],0.5)
        self.assertEqual(short["mfe_giveback_pct_approx"],25.0)
        self.assertEqual(short["mfe25_floor_r"],1.5)
        self.assertEqual(long["side"],"long")
        self.assertEqual(long["manager_mfe_r"],1.3)
        self.assertEqual(long["manager_mae_r"],-0.5)
        self.assertIsNone(long["mfe25_floor_r"])
        self.assertFalse(out["broker_order_writes"])

    def test_unlinked_broker_trade_is_visible_but_untrusted(self):
        source=self.core.metals_xau_live_broker_snapshot
        def modified(*args,**kwargs):
            d=source(*args,**kwargs)
            d["all_xau_trades"].append({
                "id":"9999","instrument":"XAU_USD",
                "currentUnits":"-0.1","price":"3640",
                "unrealizedPL":"9.0"})
            return d
        self.core.metals_xau_live_broker_snapshot=modified
        out=panel.snapshot(self.core)
        self.assertEqual(out["count"],3)
        self.assertEqual(out["untracked_broker_count"],1)
        self.assertFalse(out["broker_link_reconciled"])
        unknown=out["trades"][-1]
        self.assertFalse(unknown["tracked"])
        self.assertEqual(unknown["tracking"],"UNLINKED — CHECK")
        self.assertIsNone(unknown["manager_mfe_r"])

    def test_broker_failure_is_not_fake_zero(self):
        self.core.metals_xau_live_broker_snapshot=lambda **kw:{"ok":False}
        out=panel.snapshot(self.core)
        self.assertFalse(out["ok"])
        self.assertIsNone(out["count"])

    def test_broker_missing_all_sides_snapshot_fails_closed(self):
        self.core.metals_xau_live_broker_snapshot=lambda **kw:{
            "ok":True,"owned_open_trades":[{"id":"1002"}]}
        out=panel.snapshot(self.core)
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"],"xau_broker_all_sides_unavailable")

if __name__=="__main__":
    unittest.main()
