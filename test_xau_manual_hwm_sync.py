"""Regression tests for manual XAU stacking/HWM/harvest reset without broker writes."""
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch
import metals_xau_manual_highwater_reset as hwm


class FakeCore:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE metals_xau_live_trade_links (
            id INTEGER PRIMARY KEY,broker_trade_id TEXT,side TEXT,status TEXT,
            estimated_risk_amount REAL,requested_risk_amount REAL,created_at_utc TEXT
          );
          CREATE TABLE metals_xau_live_runtime_state(
            key TEXT PRIMARY KEY,value TEXT,updated_at_utc TEXT
          );
          CREATE TABLE metals_xau_live_harvest_stages(
            id INTEGER PRIMARY KEY,cycle_id TEXT,status TEXT,threshold_r INTEGER,
            executed_bank_gbp REAL,updated_at_utc TEXT,reason TEXT
          );
          CREATE TABLE metals_xau_live_action_queue(
            id INTEGER PRIMARY KEY,action TEXT,status TEXT,reason TEXT
          );
          CREATE TABLE metals_xau_live_hwm_events(
            id INTEGER PRIMARY KEY,created_at_utc TEXT,observed_at_utc TEXT,
            event_type TEXT,open_count INTEGER,current_gbp REAL,current_r REAL,
            high_water_gbp REAL,high_water_r REAL,source TEXT,raw_json TEXT
          );
          CREATE TABLE metals_xau_live_execution_audit(
            id INTEGER PRIMARY KEY,created_at_utc TEXT,raw_signal_id INTEGER,
            action TEXT,status TEXT,message TEXT,raw_json TEXT
          );
        """)
        self.cycle = "XAU_LIVE_20261105T160039074684Z"
        self.broker = {"ok": True,"all_xau_trades": [
            {"id":"123","currentUnits":"-0.1","unrealizedPL":"8.0"}
        ]}
        self._metals_xau_live_runtime_set(self.db,"active_harvest_cycle_id",self.cycle)
        self._metals_xau_live_runtime_set(self.db,"broker_hwm_r","30")
        self._metals_xau_live_runtime_set(self.db,"broker_hwm_gbp","150")
        self._metals_xau_live_runtime_set(self.db,"broker_hwm_seen_at","2026-11-10T11:00:00+00:00")
        self.db.execute(
            "INSERT INTO metals_xau_live_trade_links VALUES (1,'123','short','OPEN',4.0,4.0,'2026-11-05T16:01:00+00:00')")
        self.db.execute(
            "INSERT INTO metals_xau_live_harvest_stages VALUES "
            "(1,?,'EXECUTED',30,32.0,'2026-11-11T00:00:00Z','historical bank'),"
            "(2,?,'ARMED',80,0.0,'2026-11-11T00:00:00Z','waiting')",
            (self.cycle,self.cycle))
        self.db.commit()
    @contextmanager
    def get_conn(self):
        yield self.db
    def _metals_xau_live_runtime_get(self,conn,key,default=""):
        row=conn.execute("SELECT value FROM metals_xau_live_runtime_state WHERE key=?",(key,)).fetchone()
        return row["value"] if row else default
    def _metals_xau_live_runtime_set(self,conn,key,value):
        conn.execute(
            "INSERT INTO metals_xau_live_runtime_state(key,value,updated_at_utc) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at_utc=excluded.updated_at_utc",
            (key,str(value),datetime.now(timezone.utc).isoformat()))
    def metals_xau_live_broker_snapshot(self,include_account_summary=False):
        return self.broker


class ManualHwmTests(unittest.TestCase):
    def setUp(self):
        self.core = FakeCore()
        self.addCleanup(self.core.db.close)

    def test_baseline_requires_broker_cash_and_trade_risk(self):
        proof=hwm.baseline(self.core,self.core.broker,[
            {"broker_trade_id":"123","side":"short","estimated_risk_amount":4.0}
        ],"short")
        self.assertTrue(proof["ok"])
        self.assertEqual(proof["new_hwm_gbp"],8.0)
        self.assertEqual(proof["new_hwm_r"],2.0)
        missing=hwm.baseline(self.core,{"ok":True,"all_xau_trades":[{"id":"123","currentUnits":"-0.1"}]},[
            {"broker_trade_id":"123","side":"short","estimated_risk_amount":4.0}
        ],"short")
        self.assertFalse(missing["ok"])

    def test_mixed_xau_long_short_rejected(self):
        self.core.broker["all_xau_trades"].append(
            {"id":"124","currentUnits":"0.1","unrealizedPL":"2.0"})
        proof=hwm.baseline(self.core,self.core.broker,[
            {"broker_trade_id":"123","side":"short","estimated_risk_amount":4.0},
            {"broker_trade_id":"124","side":"long","estimated_risk_amount":4.0},
        ],"short")
        self.assertFalse(proof["ok"])
        self.assertEqual(proof["reason"],"xau_opposite_direction_open_hwm_is_shared")

    def test_atomic_rebase_archives_peak_and_unfinished_harvest(self):
        result=hwm.perform_rebase(self.core,"short",align_only=False,
                                  expected_cycle_id=self.core.cycle,expected_open_ids=["123"])
        self.assertTrue(result["ok"])
        self.assertNotEqual(result["new_cycle_id"],self.core.cycle)
        self.assertEqual(result["previous"]["r"],30)
        self.assertEqual(result["baseline"]["new_hwm_r"],2)
        self.assertEqual(self.core._metals_xau_live_runtime_get(self.core.db,"broker_hwm_r"),"2.0")
        self.assertEqual(self.core._metals_xau_live_runtime_get(self.core.db,"harvest_last_seen_hwm_r"),"2.0")
        stages=[tuple(row) for row in self.core.db.execute(
            "SELECT status,executed_bank_gbp FROM metals_xau_live_harvest_stages ORDER BY id")]
        self.assertEqual(stages,[("EXECUTED",32.0),("MANUAL_CAMPAIGN_ARCHIVED",0.0)])
        history=self.core.db.execute(
            "SELECT event_type,raw_json FROM metals_xau_live_hwm_events ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(history["event_type"],"MANUAL_CAMPAIGN_HWM_REBASE")
        self.assertIn("previous_harvest_execution_preserved",history["raw_json"])
        self.assertEqual(self.core.db.execute(
            "SELECT COUNT(*) FROM metals_xau_live_trade_links WHERE status='OPEN'").fetchone()[0],1)
        # No action queue writes or broker side effects.
        self.assertEqual(self.core.db.execute("SELECT COUNT(*) FROM metals_xau_live_action_queue").fetchone()[0],0)

    def test_refuse_partially_banked_harvest(self):
        self.core.db.execute("UPDATE metals_xau_live_harvest_stages SET status='PARTIAL_RETRY',executed_bank_gbp=5 WHERE id=2")
        self.core.db.commit()
        r=hwm.perform_rebase(self.core,"short",align_only=False,
                             expected_cycle_id=self.core.cycle,expected_open_ids=["123"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"],"xau_harvest_in_progress")
        self.assertEqual(self.core._metals_xau_live_runtime_get(self.core.db,"broker_hwm_r"),"30")

    def test_refuse_queued_harvest_close(self):
        self.core.db.execute("INSERT INTO metals_xau_live_action_queue VALUES (1,'CLOSE','QUEUED','LIVE_XAU_HARVEST_30R')")
        self.core.db.commit()
        r=hwm.perform_rebase(self.core,"short",align_only=False,
                             expected_cycle_id=self.core.cycle,expected_open_ids=["123"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"],"xau_harvest_in_progress")

    def test_align_previous_reset_only_if_identity_and_state_match(self):
        at="2026-11-12T14:10:00+00:00"
        self.core._metals_xau_live_runtime_set(self.core.db,
            "xau_adaptive_manual_campaign_short_reset_at",at)
        self.core._metals_xau_live_runtime_set(self.core.db,
            "xau_adaptive_manual_campaign_short_cycle_id",self.core.cycle)
        self.core.db.commit()
        r=hwm.perform_rebase(self.core,"short",align_only=True,
                             expected_cycle_id=self.core.cycle,
                             expected_open_ids=["123"],manual_reset_at=at)
        self.assertTrue(r["ok"])
        self.assertEqual(self.core._metals_xau_live_runtime_get(self.core.db,
            "xau_adaptive_manual_campaign_short_reset_at"),at)
        self.assertEqual(self.core._metals_xau_live_runtime_get(self.core.db,
            "xau_adaptive_manual_campaign_short_hwm_aligned_cycle_id"),r["new_cycle_id"])
        again=hwm.perform_rebase(self.core,"short",align_only=True,
                                 expected_cycle_id=r["new_cycle_id"],
                                 expected_open_ids=["123"],manual_reset_at=at)
        self.assertFalse(again["ok"])

    def test_no_rebase_if_open_positions_changed_since_preview(self):
        r=hwm.perform_rebase(self.core,"short",align_only=False,
                             expected_cycle_id=self.core.cycle,
                             expected_open_ids=["OLD"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"],"xau_positions_changed_since_preview")


if __name__ == "__main__":
    unittest.main()
