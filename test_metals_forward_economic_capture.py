"""Regression tests for forward-only LIVE Metals economic-equity capture."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading
import unittest

import metals_forward_economic_capture as capture


class Core:
    def __init__(self, when):
        self.when=when
        self.conn=sqlite3.connect(":memory:")
        self.conn.row_factory=sqlite3.Row
        self.conn.execute("CREATE TABLE metals_xau_live_runtime_state (key TEXT, value TEXT,updated_at_utc TEXT)")
        self.conn.executemany("INSERT INTO metals_xau_live_runtime_state VALUES(?,?,?)",[
            ("accounting_epoch_utc",when.isoformat(),when.isoformat()),
            ("broker_transaction_cursor","100",when.isoformat()),
        ])
        self.conn.execute("CREATE TABLE metals_xau_live_broker_transactions (instrument TEXT,pl_gbp REAL,financing_gbp REAL,net_realized_gbp REAL)")
        self.conn.execute("INSERT INTO metals_xau_live_broker_transactions VALUES('XAU_USD',12,-2,10)")
        self.conn.execute("""CREATE TABLE metals_xag_live_trade_links (
            broker_trade_id TEXT,created_at_utc TEXT,closed_at_utc TEXT,status TEXT,
            realized_pl REAL,estimated_risk_amount REAL)""")
        self.conn.execute("CREATE TABLE metals_xag_live_campaign_state (id INTEGER,start ed TEXT)") if False else None
        self.conn.execute("CREATE TABLE metals_xag_live_campaign_state (id INTEGER,started_at_utc TEXT)")
        self.conn.execute("INSERT INTO metals_xag_live_campaign_state VALUES(1,?)",(when.isoformat(),))
        self.conn.execute("INSERT INTO metals_xag_live_trade_links VALUES(?,?,?,?,?,?)",
            ("S1",when.isoformat(),None,"OPEN",None,2))
        self.conn.commit()
    def get_conn(self):
        return self.conn


def report(when, xag_open=True, good=True, cash_good=True):
    trades=[{"instrument":"XAU_USD","broker_trade_id":"G1","side":"SHORT",
             "unrealized_gbp":4,"units":-1,"entry_price":123,"opened_at_utc":when.isoformat()}]
    if xag_open:
        trades.append({"instrument":"XAG_USD","broker_trade_id":"S1","side":"SHORT",
                       "unrealized_gbp":3,"units":-1,"entry_price":43,"opened_at_utc":when.isoformat()})
    if not xag_open:
        trades=[] # matched and broker-confirmed family flat
    return {
        "generated_at_utc":when.isoformat(),
        "positions":{"ok":True,"count":len(trades),"xau_count":sum(t["instrument"]=="XAU_USD" for t in trades),
                     "xag_short_count":sum(t["instrument"]=="XAG_USD" for t in trades),
                     "xag_broker_link_reconciled":True,"trades":trades},
        "combined":{"ok":good,"open_pnl_gbp":sum(t["unrealized_gbp"] for t in trades) if good else None},
        "risk":{"ok":good},
        "cash":{"ok":cash_good,"proof":{"realised_all_time_gbp":-3,"time_utc":when.isoformat()} if cash_good else None}
    }


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.t=datetime.now(timezone.utc).replace(microsecond=0)
        self.core=Core(self.t)

    def tearDown(self):
        self.core.conn.close()

    def rows(self):
        return [dict(r) for r in self.core.conn.execute(
            "SELECT * FROM metals_forward_equity_samples ORDER BY id").fetchall()]

    def test_complete_sample_capture_and_r_proxy(self):
        result=capture._capture(self.core,report(self.t),now=self.t)
        self.assertTrue(result["ok"])
        row=self.rows()[0]
        self.assertEqual(row["status"],"COMPLETE")
        self.assertAlmostEqual(row["xag_open_r"],1.5)
        self.assertAlmostEqual(row["xag_funding_r_proxy"],1.5)
        self.assertEqual(row["xag_funding_status"],"PROXY_EXCLUDES_BROKER_FINANCING")
        self.assertAlmostEqual(row["combined_realised_since_epochs_gbp"],7)
        self.assertAlmostEqual(row["economic_equity_gbp"],7)
        self.assertAlmostEqual(row["economic_high_water_gbp"],7)
        self.assertEqual(row["family_bootstrap_partial"],1)
        self.assertEqual(row["execution_authority"],0)
        self.assertEqual(len(json.loads(row["position_marks_json"])),2)

    def test_sequential_high_water_and_drawdown_persist(self):
        capture._capture(self.core,report(self.t),now=self.t)
        later=self.t+timedelta(minutes=5)
        self.core.conn.execute(
            "UPDATE metals_xau_live_runtime_state SET updated_at_utc=? WHERE key='broker_transaction_cursor'",
            (later.isoformat(),))
        data=report(later)
        data["positions"]["trades"][0]["unrealized_gbp"]=-5
        data["combined"]["open_pnl_gbp"]=-2
        capture._capture(self.core,data,now=later)
        rows=self.rows()
        self.assertEqual(rows[1]["economic_high_water_gbp"],7)
        self.assertEqual(rows[1]["economic_equity_gbp"],-2)
        self.assertEqual(rows[1]["economic_giveback_gbp"],9)

    def test_failed_audit_is_recorded_unknown_and_never_reset(self):
        capture._capture(self.core,report(self.t),now=self.t)
        later=self.t+timedelta(minutes=5)
        data=report(later,cash_good=False)
        capture._capture(self.core,data,now=later)
        rows=self.rows()
        self.assertEqual(rows[1]["status"],"INCOMPLETE")
        self.assertIsNone(rows[1]["economic_equity_gbp"])
        self.assertIsNone(rows[1]["combined_realised_since_epochs_gbp"])
        self.assertEqual(rows[1]["cash_broker_verified"],0)
        self.assertEqual(self.core.conn.execute(
            "SELECT peak_equity_gbp FROM metals_forward_equity_state WHERE id=1").fetchone()[0],7)

    def test_verified_flat_resets_shadow_baseline_not_live_campaign_table(self):
        capture._capture(self.core,report(self.t),now=self.t)
        later=self.t+timedelta(minutes=5)
        self.core.conn.execute(
            "UPDATE metals_xau_live_runtime_state SET updated_at_utc=? WHERE key='broker_transaction_cursor'",
            (later.isoformat(),))
        capture._capture(self.core,report(later,xag_open=False),now=later)
        r=self.rows()[-1]
        self.assertEqual(r["family_bootstrap_partial"],0)
        self.assertAlmostEqual(r["economic_equity_gbp"],0)
        self.assertEqual(r["total_open_count"],0)
        self.assertEqual(self.core.conn.execute(
            "SELECT started_at_utc FROM metals_xag_live_campaign_state WHERE id=1"
        ).fetchone()[0],self.t.isoformat())
        self.assertNotEqual(self.rows()[0]["family_campaign_id"],r["family_campaign_id"])

    def test_closed_link_banked_r_is_estimated_not_mislabelled_exact(self):
        self.core.conn.execute(
            "INSERT INTO metals_xag_live_trade_links VALUES(?,?,?,?,?,?)",
            ("S0",self.t.isoformat(),self.t.isoformat(),"CLOSED_RECONCILED",5,2))
        self.core.conn.commit()
        capture._capture(self.core,report(self.t),now=self.t)
        r=self.rows()[0]
        self.assertEqual(r["xag_campaign_entries"],2)
        self.assertAlmostEqual(r["xag_banked_r_proxy"],2.5)
        self.assertAlmostEqual(r["xag_funding_r_proxy"],4)
        self.assertEqual(r["xag_funding_status"],"PROXY_EXCLUDES_BROKER_FINANCING")

    def test_incomplete_closed_trade_financing_never_promoted_to_exact_r(self):
        self.core.conn.execute(
            "INSERT INTO metals_xag_live_trade_links VALUES(?,?,?,?,?,?)",
            ("S0",self.t.isoformat(),self.t.isoformat(),"CLOSED",None,2))
        self.core.conn.commit()
        capture._capture(self.core,report(self.t),now=self.t)
        row=self.rows()[0]
        self.assertEqual(row["xag_funding_status"],"BANKED_R_INCOMPLETE")
        self.assertIsNone(row["xag_funding_r_proxy"])
        self.assertEqual(row["status"],"COMPLETE")

    def test_invalid_cross_broker_snapshot_never_computes_equity(self):
        d=report(self.t)
        d["risk"]["ok"]=False
        capture._capture(self.core,d,now=self.t)
        row=self.rows()[0]
        self.assertEqual(row["status"],"INCOMPLETE")
        self.assertIsNone(row["economic_equity_gbp"])
        self.assertEqual(row["broker_population_matched"],0)


if __name__=="__main__":
    unittest.main()
