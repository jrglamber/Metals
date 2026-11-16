"""Regression tests: Metals open P&L must match Hub's broker scope."""
import unittest
from datetime import datetime, timezone

import metals_live_open_pnl_display as display


def sample():
    return {
        "scope": display.SCOPE, "ok": True,
        "xau_ok": True, "xag_ok": True, "practice_excluded": True,
        "xag_broker_link_reconciled": True, "xag_unrealized_complete": True,
        "xag_untracked_broker_count": 0,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "xau_count": 2, "xag_count": 2, "xag_short_count": 2, "count": 4,
        "xag_unrealized_gbp": 13.84,
        "trades": [
            {"lane": "XAU_LIVE", "instrument": "XAU_USD", "side": "LONG",
             "broker_trade_id": "X1", "unrealized_gbp": 9.00},
            {"lane": "XAU_LIVE", "instrument": "XAU_USD", "side": "SHORT",
             "broker_trade_id": "X2", "unrealized_gbp": 7.83},
            {"lane": "XAG_SHORT_LIVE", "instrument": "XAG_USD", "side": "SHORT",
             "broker_trade_id": "A1", "tracking": "TRACKED", "unrealized_gbp": 10.24},
            {"lane": "XAG_SHORT_LIVE", "instrument": "XAG_USD", "side": "SHORT",
             "broker_trade_id": "A2", "tracking": "TRACKED", "unrealized_gbp": 3.60},
        ],
    }


class CombinedLivePnlTests(unittest.TestCase):
    def test_xau_plus_xag_matches_hub_scope(self):
        result = display.reconcile(sample())
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["open_pnl_gbp"], 30.67)
        self.assertAlmostEqual(result["xau_open_pnl_gbp"], 16.83)
        self.assertAlmostEqual(result["xag_short_open_pnl_gbp"], 13.84)
        self.assertEqual(result["open_count"], 4)
        self.assertFalse(result["broker_writes"])

    def test_rejects_incomplete_xag(self):
        payload = sample()
        payload["xag_broker_link_reconciled"] = False
        with self.assertRaises(ValueError):
            display.reconcile(payload)

    def test_rejects_missing_trade_and_duplicate_id(self):
        payload = sample()
        payload["trades"][2]["unrealized_gbp"] = None
        with self.assertRaises(ValueError):
            display.reconcile(payload)
        payload = sample()
        payload["trades"][2]["broker_trade_id"] = "X1"
        with self.assertRaises(ValueError):
            display.reconcile(payload)

    def test_rejects_incorrect_xag_or_scope(self):
        payload = sample()
        payload["xag_unrealized_gbp"] = 0
        with self.assertRaises(ValueError):
            display.reconcile(payload)
        payload = sample()
        payload["scope"] = "LIVE_XAU_ONLY"
        with self.assertRaises(ValueError):
            display.reconcile(payload)

    def test_rejects_untracked_or_long_xag(self):
        for key, value in [("tracking", "UNLINKED"), ("side", "LONG")]:
            payload = sample()
            payload["trades"][2][key] = value
            with self.assertRaises(ValueError):
                display.reconcile(payload)

    def test_production_endpoint_precedes_existing_root_mount(self):
        from fastapi import FastAPI
        api = FastAPI()
        api.mount("/", FastAPI())  # Same catch-all route order as Metals production.
        installed = display.install(api, object())
        self.assertTrue(installed["installed"])
        self.assertEqual(installed["route_priority"], 0)
        self.assertEqual(api.router.routes[0].path, "/api/live-metals-open-pnl")
        self.assertTrue(any(getattr(r, "path", None) in ("", "/") for r in api.router.routes[1:]))

    def test_dashboard_script_deduplicated(self):
        html = "<html><body><h1>Metals</h1></body></html>"
        patched = display.inject_html(html)
        self.assertIn('pep-combined-live-metals-pnl-v2', patched)
        self.assertIn("Broker P&L", patched)
        self.assertIn("/api/live-metals-open-pnl", patched)
        self.assertEqual(display.inject_html(patched), patched)


if __name__ == "__main__":
    unittest.main()
