"""Isolated regression tests for XAU manual adaptive campaign reset."""
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import metals_xau_manual_campaign_reset as reset


class Connection:
    def __init__(self, rows):
        self.rows = rows
    def execute(self, statement, params=()):
        return Cursor(self.rows)


class Cursor:
    def __init__(self, rows):
        self.rows = rows
    def fetchall(self):
        return self.rows


class FakeCore:
    def __init__(self, rows=None, state=None, broker=None):
        self.rows = rows or []
        self.state = state or {}
        self.broker = broker or {"ok": True, "all_xau_trades": []}
    @contextmanager
    def get_conn(self):
        yield Connection(self.rows)
    def init_db(self):
        pass
    def _metals_xau_live_runtime_get(self, conn, key, default=""):
        return self.state.get(key, default)
    def metals_xau_live_broker_snapshot(self, include_account_summary=False):
        return self.broker


def trade(id, side):
    return {"id": str(id), "currentUnits": "1" if side == "long" else "-1"}


def link(id, side):
    return {"broker_trade_id": str(id), "side": side}


class ResetTests(unittest.TestCase):
    def test_manual_boundary_applies_only_to_original_harvest_cycle(self):
        core = FakeCore(state={
            reset._key("short", "cycle_id"): "XAU_LIVE_20261110T000000Z",
            reset._key("short", "reset_at"): "2026-11-12T14:00:00+00:00",
        })
        old = datetime(2026, 11, 10, tzinfo=timezone.utc)
        epoch, _, active = reset._effective_anchor(core, "short", "XAU_LIVE_20261110T000000Z", old)
        self.assertTrue(active)
        self.assertEqual(epoch.hour, 14)
        epoch2, _, active2 = reset._effective_anchor(core, "short", "XAU_LIVE_20261113T000000Z", old)
        self.assertFalse(active2)
        self.assertEqual(epoch2, old)

    def test_count_since_reset_excludes_closed_older_entries(self):
        core = FakeCore(rows=[
            {"created_at_utc": "2026-11-11T09:00:00+00:00"},
            {"created_at_utc": "2026-11-12T15:00:00+00:00"},
            {"created_at_utc": "2026-11-12T16:00:00+00:00"},
        ])
        start = datetime(2026,11,12,14,tzinfo=timezone.utc)
        self.assertEqual(reset._count_after_epoch(core, "short", "cycle", start), 2)

    def preview(self, broker_trades, links, count=25):
        core = FakeCore(broker={"ok": True, "all_xau_trades": broker_trades})
        with patch.object(reset.adaptive, "_active_cycle",
                          return_value=("XAU_LIVE_20261110T000000Z", datetime(2026,11,10,tzinfo=timezone.utc))), \
             patch.object(reset.adaptive, "_open_links", return_value=links), \
             patch.object(reset.adaptive, "_campaign_entry_count", return_value={"count": count, "cycle_id":"XAU_LIVE_20261110T000000Z"}), \
             patch.object(reset, "_recent_closed", return_value={"days":7,"closed_count":1,"warning":False,"realized_r_known":0}):
            return reset._preview(core, "short")

    def test_eligible_with_one_remaining_short(self):
        state = self.preview([trade(123, "short")], [link(123, "short")])
        self.assertTrue(state["allowed"])
        self.assertEqual(state["open_xau_total"], 1)
        self.assertEqual(state["current_campaign_entry_count"], 25)

    def test_exact_match_checks_both_signed_sides(self):
        state = self.preview([trade(1, "long"), trade(2, "short")],
                             [link(1, "long"), link(2, "short")])
        self.assertTrue(state["allowed"])

    def test_blocks_three_live_trades(self):
        state = self.preview([trade(1,"short"), trade(2,"short"), trade(3,"long")],
                             [link(1,"short"), link(2,"short"), link(3,"long")])
        self.assertFalse(state["allowed"])
        self.assertIn("xau_exposure_above_two_open_trades", state["blocking_reasons"])

    def test_blocks_unreconciled_broker_trade(self):
        state = self.preview([trade(1,"short")], [link(2,"short")])
        self.assertFalse(state["allowed"])
        self.assertIn("xau_broker_link_reconciliation_not_exact", state["blocking_reasons"])

    def test_blocks_incorrect_side(self):
        state = self.preview([trade(1,"short")], [link(1,"long")])
        self.assertFalse(state["allowed"])

    def test_no_reset_when_still_within_normal_allowance(self):
        state = self.preview([trade(1,"short")], [link(1,"short")],count=19)
        self.assertFalse(state["allowed"])
        self.assertIn("adaptive_campaign_below_20_entries_no_reset_needed", state["blocking_reasons"])


if __name__ == "__main__":
    unittest.main()
