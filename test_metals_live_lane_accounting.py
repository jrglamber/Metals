import unittest
from datetime import datetime, timezone
from metals_live_lane_accounting import live_lane_financials

NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)

class TestLaneAccounting(unittest.TestCase):
    def test_live_lanes_and_partial_close(self):
        links = [dict(broker_trade_id='11', instrument='XAU_USD', side='long'),
                 dict(broker_trade_id='12', instrument='XAU_USD', side='short'),
                 dict(broker_trade_id='13', instrument='XAG_USD', side='short')]
        txs = [dict(transaction_id=str(i), broker_trade_id=tid,
                    transaction_time='2026-11-06T10:00:00Z', net_realized_gbp=amount)
               for i, tid, amount in [(1,'11',3), (2,'11',2), (3,'12',-1), (4,'13',4)]]
        result = live_lane_financials(txs, links, now=NOW)
        self.assertTrue(result['reconciled'])
        self.assertEqual(result['live_lanes']['xau_long']['realised_week_gbp'], 5)
        self.assertEqual(result['live_lanes']['xau_short']['realised_week_gbp'], -1)
        self.assertEqual(result['live_lanes']['xag_short']['realised_week_gbp'], 4)

    def test_missing_attribution_does_not_claim_zero(self):
        result = live_lane_financials([dict(transaction_id='1', net_realized_gbp=5,
                         transaction_time='2026-11-06T10:00:00Z')], [], now=NOW)
        self.assertFalse(result['reconciled'])
        self.assertIsNone(result['live_lanes']['xau_long']['realised_week_gbp'])

    def test_conflicting_broker_ownership_fails_closed(self):
        links = [dict(broker_trade_id='11', instrument='XAU_USD', side='long'),
                 dict(broker_trade_id='11', instrument='XAU_USD', side='short')]
        tx = dict(transaction_id='1', broker_trade_id='11', net_realized_gbp=5,
                  transaction_time='2026-11-06T10:00:00Z')
        result = live_lane_financials([tx], links, now=NOW)
        self.assertFalse(result['reconciled'])
        self.assertEqual(result['conflicting_trade_id_count'], 1)
        self.assertIsNone(result['live_lanes']['xau_short']['realised_week_gbp'])

    def test_duplicate_transactions_flagged(self):
        tx = dict(transaction_id='1', instrument='XAU_USD', side='long',
                  net_realized_gbp=5, transaction_time='2026-11-06T10:00:00Z')
        result = live_lane_financials([tx, tx], [], now=NOW)
        self.assertEqual(result['duplicate_count'], 1)
        self.assertFalse(result['reconciled'])

if __name__ == '__main__': unittest.main()
