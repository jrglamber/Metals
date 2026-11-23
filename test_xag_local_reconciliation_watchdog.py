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



class ProtectiveCloseRetryTests(unittest.TestCase):
    """No broker write unless a preexisting CLOSE_RETRY has exact broker proof."""

    def _fake(self, *, state="OPEN", instrument="XAG_USD", units="-1",
              management=True, broker_ok=True):
        fake = types.SimpleNamespace()
        fake.PILOT_UNITS = 1.0
        fake._account_id = lambda core: "test-account"
        fake._num = lambda core, value: float(value) if value is not None else None
        fake.config_status = lambda core: {"management_writes_allowed": management}
        fake._request = lambda core, path, method: (
            {"ok": True, "data": {"trade": {
                "id": "test-trade", "instrument": instrument, "state": state,
                "currentUnits": units,
            }}} if broker_ok else {"ok": False})
        fake.calls = []
        fake._close_link = lambda core, link, reason: (
            fake.calls.append((link["broker_trade_id"], reason))
            or {"ok": True})
        return fake

    def _row(self, **overrides):
        row = {"status": "CLOSE_RETRY", "broker_trade_id": "test-trade",
               "side": "short", "close_reason": "MFE25_CLOSE_RETRY"}
        row.update(overrides)
        return row

    def _run(self, fake, links, *, reconciled=True):
        with patch.object(w, "_invalidate_local_ownership_caches") as clear:
            result = w._retry_confirmed_xag_close_requests(
                object(), fake,
                {"ok": reconciled, "exact_live_ownership": reconciled}, links)
        return result, clear

    def test_retries_only_existing_verified_open_short_close(self):
        fake = self._fake()
        result, invalidate = self._run(fake, [
            self._row(), self._row(status="OPEN", broker_trade_id="another")])
        self.assertEqual((result["attempted"], result["confirmed"], result["pending"]),
                         (1, 1, 1))
        self.assertEqual(fake.calls, [("test-trade", "MFE25_CLOSE_RETRY")])
        invalidate.assert_called_once_with()

    def test_preexisting_broker_closed_trade_is_safe_to_reconcile(self):
        fake = self._fake(state="CLOSED", units="0")
        result, _ = self._run(fake, [self._row()])
        self.assertEqual(result["confirmed"], 1)

    def test_block_on_wrong_instrument_or_side_or_size(self):
        for kwargs in ({"instrument": "XAU_USD"},
                       {"units": "1"}, {"units": "-2"}):
            with self.subTest(kwargs=kwargs):
                fake = self._fake(**kwargs)
                result, _ = self._run(fake, [self._row()])
                self.assertEqual(result["attempted"], 0)
                self.assertFalse(fake.calls)
        fake = self._fake()
        result, _ = self._run(fake, [self._row(side="long")])
        self.assertEqual(result["attempted"], 0)

    def test_block_on_missing_broker_read_unverified_ownership_or_disabled_management(self):
        for fake, reconciled in (
            (self._fake(broker_ok=False), True),
            (self._fake(), False),
            (self._fake(management=False), True),
        ):
            with self.subTest(reconciled=reconciled):
                result, _ = self._run(fake, [self._row()], reconciled=reconciled)
                self.assertEqual(result["attempted"], 0)
                self.assertFalse(fake.calls)

    def test_unrelated_open_trades_are_never_closed(self):
        fake = self._fake()
        result, _ = self._run(fake, [self._row(status="OPEN")])
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["attempted"], 0)
        self.assertFalse(fake.calls)

if __name__=="__main__":
    unittest.main()
