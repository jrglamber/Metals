import unittest
from metals_xau_direction_coverage import diagnose_xau_coverage

class CoverageTests(unittest.TestCase):
    def test_unique_trade_ownership_counts_without_claiming_cash(self):
        links=[{"broker_trade_id":"11","instrument":"XAU_USD","side":"long"},
               {"broker_trade_id":"12","instrument":"XAU_USD","side":"short"}]
        tx=[{"transaction_id":"1","broker_trade_id":"11","instrument":"XAU_USD"},
            {"transaction_id":"2","broker_trade_id":"12","instrument":"XAU_USD"},
            {"transaction_id":"3","instrument":"XAU_USD"}]
        r=diagnose_xau_coverage(tx,links)
        self.assertEqual(r["transaction_row_counts"]["xau_long"],1)
        self.assertEqual(r["transaction_row_counts"]["xau_short"],1)
        self.assertEqual(r["transaction_row_counts"]["unlinked"],1)
        self.assertFalse(r["per_side_net_gbp_verified"])
        self.assertFalse(r["authoritative_broker_history_verified"])

    def test_conflicting_ownership_is_never_attributed(self):
        links=[{"broker_trade_id":"11","instrument":"XAU_USD","side":"long"},
               {"broker_trade_id":"11","instrument":"XAU_USD","side":"short"}]
        r=diagnose_xau_coverage([{"transaction_id":"1","broker_trade_id":"11","instrument":"XAU_USD"}],links)
        self.assertEqual(r["conflicting_trade_ids"],1)
        self.assertEqual(r["transaction_row_counts"]["ambiguous"],1)
        self.assertEqual(r["transaction_row_counts"]["xau_short"],0)

if __name__=="__main__": unittest.main()
