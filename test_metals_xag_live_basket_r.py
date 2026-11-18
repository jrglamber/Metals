"""XAG live R must be initial-risk based, broker reconciled and read-only."""
import copy
import unittest

from metals_xag_live_basket_r import project, SCOPE


def example():
    rows = [
        {"lane": "XAU_LIVE", "instrument": "XAU_USD", "side": "LONG",
         "broker_trade_id": "G1", "unrealized_gbp": 4},
        {"lane": "XAG_SHORT_LIVE", "instrument": "XAG_USD", "side": "SHORT",
         "broker_trade_id": "S1", "tracking": "TRACKED", "unrealized_gbp": 5},
        {"lane": "XAG_SHORT_LIVE", "instrument": "XAG_USD", "side": "SHORT",
         "broker_trade_id": "S2", "tracking": "TRACKED", "unrealized_gbp": -2},
    ]
    p = {"ok": True, "scope": "LIVE_XAU_LONG_SHORT_PLUS_LIVE_XAG_SHORT",
         "xag_broker_link_reconciled": True, "xag_unrealized_complete": True,
         "practice_excluded": True, "xag_short_count": 2, "trades": rows,
         "observed_at_utc": "2026-11-18T20:00:00+00:00"}
    c = {"ok": True, "xag_short_open_pnl_gbp": 3}
    links = [
        {"broker_trade_id": "S1", "status": "OPEN", "estimated_risk_amount": 2.5},
        {"broker_trade_id": "S2", "status": "CLOSE_RETRY", "estimated_risk_amount": 1},
    ]
    return p, c, links


class LiveXagRTests(unittest.TestCase):
    def test_sums_trade_r_and_preserves_cash(self):
        p, c, links = example()
        x = project(p, c, links)
        self.assertTrue(x["ok"])
        self.assertEqual(x["scope"], SCOPE)
        self.assertEqual(x["current_r"], 0.0)  # 5/2.5 - 2/1 = 0R
        self.assertEqual(x["open_pnl_gbp"], 3.0)
        self.assertFalse(x["broker_writes"])

    def test_reconciled_empty_live_basket_is_real_zero(self):
        p, c, links = example()
        p["trades"] = p["trades"][:1]
        p["xag_short_count"] = 0
        c["xag_short_open_pnl_gbp"] = 0
        x = project(p, c, [])
        self.assertEqual((x["current_r"], x["open_pnl_gbp"], x["open_count"]),
                         (0, 0, 0))

    def test_nonmatching_or_missing_risk_fails_closed(self):
        for field, bad in [("estimated_risk_amount", None),
                           ("estimated_risk_amount", 0),
                           ("estimated_risk_amount", -1)]:
            p, c, links = example()
            links[0][field] = bad
            with self.assertRaises(ValueError):
                project(p, c, links)

    def test_no_stale_or_duplicate_ledger_population(self):
        p, c, links = example()
        links[1]["broker_trade_id"] = "S1"
        with self.assertRaises(ValueError):
            project(p, c, links)
        p, c, links = example()
        p["trades"][2]["broker_trade_id"] = "other"
        with self.assertRaises(ValueError):
            project(p, c, links)

    def test_missing_ownership_or_cash_proof_fails(self):
        p, c, links = example()
        p["xag_broker_link_reconciled"] = False
        with self.assertRaises(ValueError):
            project(p, c, links)
        p, c, links = example()
        c["xag_short_open_pnl_gbp"] = 99
        with self.assertRaises(ValueError):
            project(p, c, links)

    def test_xau_cash_not_used_for_xag_r(self):
        p, c, links = example()
        p["trades"][0]["unrealized_gbp"] = 100000
        self.assertEqual(project(p, c, links)["current_r"], 0.0)


if __name__ == "__main__":
    unittest.main()
