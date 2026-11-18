"""Fast, isolated safety tests for the LIVE metals export; no OANDA required."""
import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import metals_live_analysis_export as export


class FakeCore:
    def __init__(self, db): self.path=db
    def get_conn(self):
        conn=sqlite3.connect(self.path)
        conn.row_factory=sqlite3.Row
        return conn


def seed(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE metals_xau_live_runtime_state (key TEXT,value TEXT,updated_at_utc TEXT)")
        conn.execute("INSERT INTO metals_xau_live_runtime_state(key,value) VALUES('accounting_epoch_utc','2026-10-14T09:00:00+00:00')")
        conn.execute("INSERT INTO metals_xau_live_runtime_state(key,value,updated_at_utc) VALUES('broker_transaction_cursor','99','2026-11-18T14:55:00+00:00')")
        conn.execute("CREATE TABLE metals_xau_live_broker_transactions (transaction_id TEXT,transaction_time TEXT,transaction_type TEXT,instrument TEXT,pl_gbp REAL,financing_gbp REAL,net_realized_gbp REAL,synced_at_utc TEXT)")
        conn.executemany("INSERT INTO metals_xau_live_broker_transactions VALUES(?,?,?,?,?,?,?,?)",[
            ('111','2026-11-15T22:30:00+00:00','ORDER_FILL','XAU_USD',5,0,5,'2026-11-18T14:00:00+00:00'),
            ('112','2026-11-16T11:30:00+00:00','ORDER_FILL','XAU_USD',10,-0.3,9.7,'2026-11-18T14:00:00+00:00'),
        ])
        conn.execute("CREATE TABLE metals_combined_live_pnl_observed_hwm (id INTEGER,started_at_utc TEXT,high_water_gbp REAL,high_water_at_utc TEXT)")
        conn.execute("INSERT INTO metals_combined_live_pnl_observed_hwm VALUES(1,'2026-11-16T18:38:00Z',151.44,'2026-11-18T12:57:00Z')")
        for table in ('metals_xau_live_trade_links','metals_xag_live_trade_links','metals_xau_live_hwm_events','metals_xau_live_harvest_events'):
            conn.execute('CREATE TABLE '+table+' (id INTEGER)')


def verified(_core):
    return {'proof':{'ok':True,'time_utc':'2026-11-18T14:00:00+00:00','realised_all_time_gbp':2.25},
            'events':[{'transaction_time_utc':'2026-11-16T12:00:00Z','transaction_type':'ORDER_FILL','net_realized_gbp':2.5},
                      {'transaction_time_utc':'2026-11-16T22:00:00Z','transaction_type':'DAILY_FINANCING','net_realized_gbp':-0.25}]}


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'metals.sqlite')
        seed(self.path)
        self.core=FakeCore(self.path)
        self.now=datetime(2026,11,18,15,tzinfo=timezone.utc)

    def tearDown(self): self.temp.cleanup()

    def archive(self,reader=verified):
        payload, manifest=export.build_bundle(self.core,xag_reader=reader,now=self.now)
        self.z=zipfile.ZipFile(io.BytesIO(payload))
        return manifest

    def csv(self,name):
        return list(csv.DictReader(io.StringIO(self.z.read(name).decode())))

    def test_live_cash_weekly_and_provenance(self):
        m=self.archive()
        self.assertEqual(m['status'],'COMPLETE_FOR_RECORDED_XAU_EPOCH_AND_VERIFIED_XAG_HISTORY')
        self.assertAlmostEqual(m['all_time_net_gbp'],16.95)
        self.assertAlmostEqual(m['this_week_net_gbp'],11.95)
        self.assertFalse(m['xau']['historical_broker_backfill_before_epoch_verified'])
        self.assertFalse(m['xau']['per_side_long_vs_short_cash_attribution_verified'])
        rows=self.csv('live-metals-weekly-realised-gbp.csv')
        self.assertEqual([r['period_london'] for r in rows],['2026-11-09','2026-11-16'])
        self.assertEqual(rows[0]['combined_live_net_gbp'],'5.0')
        self.assertEqual(rows[1]['combined_live_net_gbp'],'11.95')
        self.assertEqual(len(self.csv('live-xau-broker-cash-events.csv')),2)
        self.assertEqual(len(self.csv('live-xag-short-broker-cash-events.csv')),2)
        self.assertIn('manifest.json',self.z.namelist())
        self.assertNotIn('metals-demo-broker-transactions.csv',self.z.namelist())

    def test_missing_xag_never_reports_fake_zero_or_combined_total(self):
        def broken(_): raise ValueError('XAG audit refreshing')
        m=self.archive(broken)
        self.assertEqual(m['status'],'INCOMPLETE_DO_NOT_USE_COMBINED_TOTALS')
        self.assertIsNone(m['all_time_net_gbp'])
        self.assertIsNone(m['this_week_net_gbp'])
        for r in self.csv('live-metals-weekly-realised-gbp.csv'):
            self.assertEqual(r['xag_short_net_gbp'],'')
            self.assertEqual(r['combined_live_net_gbp'],'')
            self.assertEqual(r['status'],'INCOMPLETE_XAG_NOT_VERIFIED')
        self.assertEqual(m['xau']['all_time_since_epoch_gbp'],14.7)

    def test_bad_xau_transaction_cannot_appear_reconciled(self):
        with self.core.get_conn() as conn:
            conn.execute("UPDATE metals_xau_live_broker_transactions SET net_realized_gbp=999 WHERE transaction_id='112'")
        m=self.archive()
        self.assertFalse(m['combined_cash_totals_available'])
        self.assertEqual(m['xau']['invalid_transaction_ids'],['112'])

    def test_stale_broker_cursor_blocks_combined_numbers(self):
        with self.core.get_conn() as conn:
            conn.execute("UPDATE metals_xau_live_runtime_state SET updated_at_utc='2026-11-17T07:00:00Z' WHERE key='broker_transaction_cursor'")
        m=self.archive()
        self.assertFalse(m['combined_cash_totals_available'])
        self.assertFalse(m['xau']['transaction_sync_fresh_within_15_minutes'])
        self.assertIsNone(m['this_week_net_gbp'])

    def test_mismatching_xag_proof_is_rejected(self):
        def mismatch(core):
            p=verified(core)
            p['proof']['realised_all_time_gbp']=22.25
            return p
        m=self.archive(mismatch)
        self.assertFalse(m['xag']['verified'])
        self.assertIsNone(m['all_time_net_gbp'])


if __name__ == '__main__': unittest.main()