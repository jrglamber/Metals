"""Broker-mocked integration tests for XAU early-2R stop synchronization.

No real OANDA requests, account credentials, or real trading are used.
Exercise the manager tick / quote / matching / reconciliation / stop-writer path.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from metals_intrahour_exit_override import install, EARLY_2R_ENABLE_ENV


class MockApp:
    def on_event(self, _event):
        return lambda fn: fn

    def get(self, _route):
        return lambda fn: fn


class BrokerCore:
    METALS_XAU_LIVE_MANAGER_ENABLED = True
    METALS_XAU_LIVE_OANDA_ACCOUNT_ID = "TEST_DUMMY_ACCOUNT"
    METALS_XAU_LIVE_ALLOWED_INSTRUMENT = "XAU_USD"
    METALS_DEMO_MANAGER_MIN_STOP_STEP_PCT = 0.02

    safe_float = staticmethod(lambda value: float(value) if value is not None else None)
    safe_str = staticmethod(lambda value: str(value or ""))

    def __init__(self, filename, side="short", hold=10, price=None):
        self.filename = filename
        self.side = side
        self.hold = hold
        self.price = (96 if side == "short" else 104) if price is None else price
        self.quote_status = "tradeable"
        self.quote_time = None
        self.reconciliation_ok = True
        self.live_broker_trade = {
            "id": "123",
            "instrument": "XAU_USD",
            "currentUnits": "-1" if side == "short" else "1",
            "stopLossOrder": {"price": "102" if side == "short" else "98"},
        }
        self.writes = []
        self.closes = []
        self.fail_writer = False
        self.mature_candidates = 0

    def get_conn(self):
        conn = sqlite3.connect(self.filename)
        conn.row_factory = sqlite3.Row
        return conn

    def seed(self):
        with self.get_conn() as conn:
            conn.execute("CREATE TABLE metals_xau_live_trade_links "
                         "(id INTEGER, status TEXT, broker_trade_id TEXT, side TEXT,"
                         " active_exit_policy TEXT, entry_price REAL, stop_price REAL,"
                         " current_stop_price REAL, raw_signal_id INTEGER)")
            conn.execute("INSERT INTO metals_xau_live_trade_links "
                         "VALUES (1, 'OPEN', '123', ?, ?, 100, ?, ?, 20)",
                         (self.side,
                          "MFE_GIVEBACK_25" if self.side == "short" else "ATR2_CHANDELIER",
                          102 if self.side == "short" else 98,
                          102 if self.side == "short" else 98))

    def _metals_xau_live_request(self, path, *args, **kwargs):
        assert path.endswith("/pricing?instruments=XAU_USD")
        quote_time = self.quote_time or datetime.now(timezone.utc).isoformat()
        return {"ok": True, "data": {"prices": [{
            "status": self.quote_status, "time": quote_time,
            "closeoutBid": str(self.price - 0.01 if self.side == "short" else self.price),
            "closeoutAsk": str(self.price if self.side == "short" else self.price + 0.01),
        }]}}

    def _metals_xau_live_trade_metrics(self, link):
        return {
            "ok": True, "side": self.side,
            "hold_candles": self.hold, "mfe_r": 0,
            "current_price": self.price, "current_r": 0, "latest_signal_id": 20,
        }

    def metals_xau_live_manager_tick(self, *args, **kwargs):
        return {"ok": True, "source": "mock_legacy_manager"}

    def metals_xau_live_broker_snapshot(self):
        return {"ok": True, "owned_open_trades": [self.live_broker_trade]}

    def _metals_xau_live_reconciliation_snapshot(self, broker):
        return {"execution_safe": self.reconciliation_ok, "matched": [self.live_broker_trade]}

    def _metals_xau_live_stop_candidate(self, link, metrics):
        self.mature_candidates += 1
        actual = float(link.get("current_stop_price") or link["stop_price"])
        proposed = 97 if self.side == "short" else 103
        tighten = proposed < actual if self.side == "short" else proposed > actual
        return {"eligible": tighten, "stop_price": proposed, "reason": "mock_mature_candidate"}

    def _metals_xau_live_update_stop(self, link, candidate):
        if self.fail_writer:
            return {"ok": False, "status": "FAILED", "response": {
                "errorMessage": "mocked OANDA stop rejection"}}
        price = float(candidate["stop_price"])
        self.writes.append(price)
        self.live_broker_trade["stopLossOrder"]["price"] = str(price)
        return {"ok": True, "status": "UPDATED", "stop_price": price}

    def _metals_xau_live_queue_close(self, link, reason, raw_id):
        self.closes.append(reason)
        return 11

    def _metals_xau_live_close(self, link, queue_id):
        return {"ok": True, "status": "CLOSED"}


class BrokerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "test.db")

    def make_manager(self, side="short", hold=10, enabled=False, price=None):
        core = BrokerCore(self.db, side=side, hold=hold, price=price)
        core.seed()
        with patch.dict(os.environ, {EARLY_2R_ENABLE_ENV: "true" if enabled else "false"}):
            status = install(core, MockApp())
        return core, status

    def test_disabled_by_default_does_not_modify_early_stops(self):
        core, status = self.make_manager()
        self.assertFalse(status["early_mfe_policy"]["enabled"])
        result = core.metals_xau_live_manager_tick()
        self.assertEqual(result["intrahour_stop_sync"]["updated"], 0)
        self.assertEqual(core.writes, [])
        self.assertEqual(core.closes, [])

    def test_short_arms_2r_via_mocked_broker_writer(self):
        core, status = self.make_manager(enabled=True)
        result = core.metals_xau_live_manager_tick()
        self.assertEqual(result["intrahour_stop_sync"]["early_2r_updated"], 1)
        self.assertEqual(core.writes, [97.0])
        # No repeat writes for the same peak / current stop.
        core.metals_xau_live_manager_tick()
        self.assertEqual(core.writes, [97.0])

    def test_long_arms_2r_via_mocked_broker_writer(self):
        core, _ = self.make_manager(side="long", enabled=True)
        result = core.metals_xau_live_manager_tick()
        self.assertEqual(result["intrahour_stop_sync"]["early_2r_updated"], 1)
        self.assertEqual(core.writes, [103.0])
        core.metals_xau_live_manager_tick()
        self.assertEqual(core.writes, [103.0])

    def test_reconciliation_fails_closed(self):
        core, _ = self.make_manager(enabled=True)
        core.reconciliation_ok = False
        result = core.metals_xau_live_manager_tick()
        self.assertEqual(result["intrahour_stop_sync"]["skipped"], "reconciliation_not_execution_safe")
        self.assertEqual(core.writes, [])

    def test_stale_quote_and_untradeable_market_do_not_place_stop(self):
        for attribute, value in [
            ("quote_status", "non-tradeable"),
            ("quote_time", (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()),
        ]:
            with self.subTest(attribute=attribute):
                # Fresh fixture per loop because each installer wraps its own core.
                name = os.path.join(self.tmp.name, f"{attribute}.db")
                core = BrokerCore(name)
                core.seed()
                with patch.dict(os.environ, {EARLY_2R_ENABLE_ENV: "true"}):
                    install(core, MockApp())
                setattr(core, attribute, value)
                result = core.metals_xau_live_manager_tick()
                self.assertEqual(result["intrahour_stop_sync"]["early_2r_updated"], 0)
                self.assertEqual(core.writes, [])

    def test_broker_direction_mismatch_does_not_write(self):
        core, _ = self.make_manager(enabled=True)
        core.live_broker_trade["currentUnits"] = "1"
        core.metals_xau_live_manager_tick()
        self.assertEqual(core.writes, [])

    def test_broker_rejection_reports_error_and_preserves_stop(self):
        core, _ = self.make_manager(enabled=True)
        core.fail_writer = True
        result = core.metals_xau_live_manager_tick()
        self.assertEqual(result["intrahour_stop_sync"]["errors"], 1)
        self.assertEqual(core.writes, [])
        self.assertEqual(core.live_broker_trade["stopLossOrder"]["price"], "102")

    def test_48_hour_transition_delegates_to_old_manager(self):
        core, _ = self.make_manager(side="long", hold=48, enabled=True)
        result = core.metals_xau_live_manager_tick()
        self.assertEqual(core.mature_candidates, 1)
        self.assertEqual(result["intrahour_stop_sync"]["mature"], 1)
        self.assertEqual(result["intrahour_stop_sync"]["early_2r_updated"], 0)

    def test_previously_tightened_long_stop_survives_atr2_transition(self):
        core, _ = self.make_manager(side="long", hold=10, enabled=True)
        core.metals_xau_live_manager_tick()
        self.assertEqual(core.writes, [103.0])
        core.hold = 48
        core.metals_xau_live_manager_tick()
        self.assertEqual(core.writes, [103.0])
        self.assertEqual(core.mature_candidates, 1)

    def test_previously_tightened_short_stop_survives_mfe25_transition(self):
        core, _ = self.make_manager(side="short", hold=10, enabled=True)
        core.metals_xau_live_manager_tick()
        self.assertEqual(core.writes, [97.0])
        core.hold = 48
        core.metals_xau_live_manager_tick()
        self.assertEqual(core.writes, [97.0])
        self.assertEqual(core.mature_candidates, 1)


if __name__ == "__main__":
    unittest.main()
