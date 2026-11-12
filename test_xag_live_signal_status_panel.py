"""Dashboard must show real live XAG short fills despite practice guard pending."""
import sqlite3
import unittest
from contextlib import contextmanager

import metals_xag_live_signal_status_panel as panel


class Core:
    def __init__(self):
        self.db=sqlite3.connect(":memory:")
        self.db.row_factory=sqlite3.Row
        self.db.executescript("""
        CREATE TABLE raw_signals (id INTEGER PRIMARY KEY,pair TEXT,timestamp_readable TEXT,received_at_utc TEXT);
        CREATE TABLE metals_xag_live_trade_links(
            id INTEGER PRIMARY KEY,raw_signal_id INTEGER,status TEXT,side TEXT,broker_trade_id TEXT);
        CREATE TABLE metals_xag_live_execution_audit(
            id INTEGER PRIMARY KEY,raw_signal_id INTEGER,action TEXT,status TEXT,reason TEXT,created_at_utc TEXT);
        """)
        self.db.executemany("INSERT INTO raw_signals VALUES(?,?,?,?)",[
            (3132,"XAGUSD","2026-11-12 09:00","2026-11-12T15:00:05Z"),
            (3130,"XAGUSD","2026-11-12 08:00","2026-11-12T14:00:05Z"),
        ])
        self.db.execute("INSERT INTO metals_xag_live_trade_links VALUES(1,3132,'OPEN','short','107528')")
        self.db.execute("INSERT INTO metals_xag_live_execution_audit VALUES(1,3132,'entry','OPENED','LIVE XAG SHORT opened','2026-11-12T15:00:07Z')")
        self.db.execute("INSERT INTO metals_xag_live_execution_audit VALUES(2,3130,'entry','BLOCKED','unlinked_broker_xag_trade_present','2026-11-12T14:00:07Z')")
        self.db.commit()
    @contextmanager
    def get_conn(self):
        yield self.db


class XagLiveSignalPanelTests(unittest.TestCase):
    def setUp(self):
        self.core=Core()
        self.addCleanup(self.core.db.close)
    def test_live_link_overrides_old_practice_guard_in_status_panel(self):
        output=panel.render(self.core)
        self.assertIn("LIVE TRACKED",output)
        self.assertIn("107528",output)
        self.assertIn("3132",output)
        self.assertIn("legacy XAG practice guard",output)
        self.assertNotIn("XAG_GUARD_PENDING_FAIL_CLOSED",output)
    def test_older_live_block_remains_explicit(self):
        output=panel.render(self.core)
        self.assertIn("LIVE BLOCKED",output)
        self.assertIn("unlinked_broker_xag_trade_present",output)
        self.assertIn("3130",output)
    def test_html_escaping(self):
        self.core.db.execute("UPDATE metals_xag_live_execution_audit SET reason='<script>unsafe</script>' WHERE raw_signal_id=3130")
        output=panel.render(self.core)
        self.assertNotIn("<script>unsafe</script>",output)
        self.assertIn("&lt;script&gt;",output)


if __name__=="__main__":
    unittest.main()
