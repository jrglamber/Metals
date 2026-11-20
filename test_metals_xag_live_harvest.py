"""Isolated regression tests for live XAG harvest safety boundaries."""
import unittest
from unittest.mock import patch, Mock
import metals_xag_live_harvest as h


class _Rows:
    def __init__(self, row):
        self.row = row
    def execute(self, sql, params=()):
        if "SELECT * FROM metals_xag_live_trade_links" in sql:
            return self
        raise AssertionError("Unexpected SQL: " + sql)
    def fetchone(self):
        return self.row
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class _Core:
    def get_conn(self):
        return _Rows({"id": 9, "broker_trade_id": "321", "side": "short",
                      "status": "OPEN"})


class HarvestRules(unittest.TestCase):
    def test_ladder_matches_xau_without_sharing_state(self):
        self.assertEqual(h._next_level([]), 30.0)
        self.assertEqual(h._next_level([30.0]), 80.0)
        self.assertEqual(h._next_level([30.0, 80.0]), 130.0)
        self.assertEqual([h._fraction(x) for x in (30, 80, 130, 180)],
                         [0.2, 0.2, 0.25, 0.25])

    def test_nonpositive_trade_never_closed(self):
        stage = {"id": 1, "cycle_id": "xag_test", "threshold_r": 30,
                 "selected_json": '[{"id":"321","expected_gbp":3}]',
                 "target_bank_gbp": 3}
        trade = {"state": "OPEN", "instrument": "XAG_USD",
                 "currentUnits": "-1", "unrealizedPL": "-0.01"}
        with patch.object(h, "_event", return_value=None), \
             patch.object(h.xag, "_request", return_value={"ok": True, "data": {"trade": trade}}), \
             patch.object(h, "_stage_status") as mark, \
             patch.object(h.xag, "_close_link") as close:
            out = h._execute_stage(_Core(), stage)
        self.assertEqual(out["status"], "REVIEW_REQUIRED")
        close.assert_not_called()
        self.assertEqual(mark.call_args.args[2], "REVIEW_REQUIRED")

    def test_close_requires_broker_verified_result(self):
        stage = {"id": 1, "cycle_id": "xag_test", "threshold_r": 30,
                 "selected_json": '[{"id":"321","expected_gbp":3}]',
                 "target_bank_gbp": 3}
        trade = {"state": "OPEN", "instrument": "XAG_USD",
                 "currentUnits": "-1", "unrealizedPL": "2.96"}
        with patch.object(h, "_event", return_value=None), \
             patch.object(h.xag, "_request", return_value={"ok": True, "data": {"trade": trade}}), \
             patch.object(h, "_record_event") as events, \
             patch.object(h, "_stage_status") as mark, \
             patch.object(h.xag, "_close_link", return_value={"ok": True, "realized_pl": 2.93}) as close:
            out = h._execute_stage(_Core(), stage)
        self.assertEqual(out["status"], "EXECUTED")
        self.assertEqual(out["banked_gbp"], 2.93)
        self.assertEqual(close.call_count, 1)
        self.assertIn("LIVE_XAG_HARVEST_30R", close.call_args.args[2])
        self.assertEqual(events.call_count, 2)
        self.assertEqual(events.call_args.args[5], "CLOSED_VERIFIED")
        self.assertEqual(mark.call_args.args[2], "EXECUTED")

    def test_incomplete_close_never_claimed_executed(self):
        stage = {"id": 1, "cycle_id": "xag_test", "threshold_r": 30,
                 "selected_json": '[{"id":"321","expected_gbp":3}]',
                 "target_bank_gbp": 3}
        trade = {"state": "OPEN", "instrument": "XAG_USD",
                 "currentUnits": "-1", "unrealizedPL": "2.96"}
        with patch.object(h, "_event", return_value=None), \
             patch.object(h.xag, "_request", return_value={"ok": True, "data": {"trade": trade}}), \
             patch.object(h, "_record_event"), \
             patch.object(h, "_stage_status") as mark, \
             patch.object(h.xag, "_close_link", return_value={"ok": False, "retry": True}):
            out = h._execute_stage(_Core(), stage)
        self.assertEqual(out["status"], "REVIEW_REQUIRED")
        self.assertEqual(mark.call_args.args[2], "REVIEW_REQUIRED")

    def test_resolved_event_is_idempotent(self):
        stage = {"id": 1, "cycle_id": "xag_test", "threshold_r": 30,
                 "selected_json": '[{"id":"321","expected_gbp":3}]',
                 "target_bank_gbp": 3}
        with patch.object(h, "_event", return_value={"status": "CLOSED_VERIFIED", "realized_gbp": 2.98}), \
             patch.object(h.xag, "_request") as request, \
             patch.object(h, "_stage_status") as mark:
            out = h._execute_stage(_Core(), stage)
        self.assertEqual(out["banked_gbp"], 2.98)
        request.assert_not_called()
        self.assertEqual(mark.call_args.args[2], "EXECUTED")


if __name__ == "__main__":
    unittest.main()
