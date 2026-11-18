"""Regression: broker-confirmed local XAG closure heals reporting, never orders."""
import sys
import threading
import types
import unittest
from unittest.mock import patch

import xag_local_reconciliation_watchdog as w


class ClosureReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.before=dict(w._last)

    def tearDown(self):
        w._last.clear()
        w._last.update(self.before)

    def test_confirmed_local_closure_reconciles_and_reaudits(self):
        fake=types.ModuleType("xag_short_live_override")
        fake._ENTRY_LOCK=threading.Lock()
        states=iter([[object() for _ in range(24)],
                     [object() for _ in range(23)]])
        fake._local_open=lambda core: next(states)
        fake._reconcile=lambda core: {
            "ok":True,"broker_xag_open_count":23,
            "local_xag_open_count":23,"exact_live_ownership":True,
            "recovered_historical_live_count":0}
        with patch.dict(sys.modules,{"xag_short_live_override":fake}):
            with patch.object(w,"_invalidate_local_ownership_caches") as invalidate:
                result=w.once(object())
        self.assertEqual(result["changed_local_links"],1)
        self.assertTrue(result["ok"])
        invalidate.assert_called_once_with()
        self.assertEqual(w.health()["consecutive_errors"],0)

    def test_unchanged_broker_ownership_does_not_invalidate_cash(self):
        fake=types.ModuleType("xag_short_live_override")
        fake._ENTRY_LOCK=threading.Lock()
        fake._local_open=lambda core:[object() for _ in range(23)]
        fake._reconcile=lambda core:{
            "ok":True,"broker_xag_open_count":23,"local_xag_open_count":23,
            "recovered_historical_live_count":0}
        with patch.dict(sys.modules,{"xag_short_live_override":fake}):
            with patch.object(w,"_invalidate_local_ownership_caches") as invalidate:
                result=w.once(object())
        self.assertEqual(result["changed_local_links"],0)
        invalidate.assert_not_called()

    def test_unverified_broker_read_does_not_erase_local_open_count(self):
        fake=types.ModuleType("xag_short_live_override")
        fake._ENTRY_LOCK=threading.Lock()
        fake._local_open=lambda core:[object() for _ in range(23)]
        fake._reconcile=lambda core:{"ok":False,"reason":"broker_snapshot_failed"}
        with patch.dict(sys.modules,{"xag_short_live_override":fake}):
            with patch.object(w,"_invalidate_local_ownership_caches") as invalidate:
                result=w.once(object())
        self.assertFalse(result["ok"])
        self.assertEqual(result["changed_local_links"],0)
        invalidate.assert_not_called()
        self.assertEqual(w.health()["last_ok"],False)


if __name__=="__main__":
    unittest.main()
