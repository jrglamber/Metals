import sqlite3
import tempfile
import unittest
from pathlib import Path

from metals_xag_transaction_audit import TransactionHistory
from metals_xag_transaction_checkpoint import Checkpoint, TABLE, scope


class PersistentCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "cache.sqlite"
        self.store = Checkpoint(self.connect)
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def broker(self, start, end):
        self.calls.append((start, end))
        return {"ok": True, "data": {"transactions": [{"id": str(i), "type": "ORDER_CANCEL",
                "time": "2026-11-26T00:00:00Z", "raw_extra": "discard"}
                for i in range(start, end+1)]}}

    def test_cold_restart_resumes_large_incomplete_backfill(self):
        a = TransactionHistory()
        result = a.read("account", 100, 18201, self.broker, checkpoint=self.store)
        self.assertFalse(result["transaction_history_complete"])
        self.assertEqual(len(self.calls), 60)
        b = TransactionHistory()
        result = b.read("account", 100, 18201, self.broker, checkpoint=self.store)
        self.assertTrue(result["transaction_history_complete"])
        self.assertEqual(self.calls[-1], (18100, 18201))
        self.assertEqual(len(self.calls), 61)
        self.assertEqual(len(b.rows), 18102)
        self.assertNotIn("raw_extra", b.rows[0])
        c = TransactionHistory()
        result = c.read("account", 100, 18201, self.broker, checkpoint=self.store)
        self.assertTrue(result["transaction_history_complete"])
        self.assertEqual(result["broker_transaction_pages_this_refresh"], 0)
        self.assertEqual(c.rows, b.rows)

    def test_corruption_only_reuses_valid_prefix(self):
        TransactionHistory().read("account", 1, 900, self.broker, checkpoint=self.store)
        with self.connect() as conn:
            conn.execute(f"UPDATE {TABLE} SET payload='[]' WHERE scope_key=? AND first_id=301",
                         (scope("account",1),))
        self.calls.clear()
        a = TransactionHistory()
        result = a.read("account", 1, 900, self.broker, checkpoint=self.store)
        self.assertTrue(result["transaction_history_complete"])
        self.assertEqual(self.calls, [(301,600),(601,900)])
        self.assertEqual(len(a.rows), 900)

    def test_account_boundary_and_rewind_are_isolated(self):
        TransactionHistory().read("account", 1, 900, self.broker, checkpoint=self.store)
        for account, earliest, last, expected in [
            ("other", 1, 100, [(1,100)]), ("account", 2, 100, [(2,100)]),
            ("account", 1, 450, [(301,450)])]:
            self.calls.clear()
            a = TransactionHistory()
            a.read(account, earliest, last, self.broker, checkpoint=self.store)
            self.assertEqual(self.calls, expected)
            self.assertEqual(len(a.rows), last-earliest+1)

    def test_db_outage_never_promotes_incomplete_broker_history(self):
        def unavailable():
            raise ConnectionError("private details")
        bad = Checkpoint(unavailable)
        a = TransactionHistory()
        partial = a.read("account",1,601,self.broker,max_pages=1,checkpoint=bad)
        self.assertFalse(partial["transaction_history_complete"])
        self.assertEqual(len(a.pending),1)
        full = a.read("account",1,601,self.broker,checkpoint=self.store)
        self.assertTrue(full["transaction_history_complete"])
        self.assertFalse(a.pending)
        b = TransactionHistory()
        full = b.read("account",1,601,self.broker,checkpoint=self.store)
        self.assertEqual(full["broker_transaction_pages_this_refresh"],0)
        self.assertEqual(len(b.rows),601)

    def test_failed_broker_page_not_persisted(self):
        a = TransactionHistory()
        a.read("account",1,600,lambda a,b: {"ok":True,"data":{"transactions":[{"id":"999"}]}},
               checkpoint=self.store)
        self.assertEqual(self.store.load("account",1,600),[])


if __name__ == "__main__":
    unittest.main()
