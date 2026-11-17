import unittest
from metals_xag_ledger_audit import audit_links

LINKS=[
 {"broker_trade_id":"100","side":"short","instrument":"XAG_USD","status":"CLOSED"},
 {"broker_trade_id":"101","side":"short","instrument":"XAG_USD","status":"OPEN"},
]
TRADES=[
 {"id":"100","instrument":"XAG_USD","initialUnits":"-1","state":"CLOSED",
  "realizedPL":"1.25","financing":"-0.09",
  "closingTransactionIDs":["102"],"closeTime":"2026-11-17T10:00:00Z"},
 {"id":"101","instrument":"XAG_USD","initialUnits":"-1","state":"OPEN",
  "realizedPL":"0.00","financing":"0",
  "closingTransactionIDs":[]},
]

class XagLedgerAuditTests(unittest.TestCase):
    def test_balanced_history_still_does_not_fake_net_gbp_ledger(self):
        r=audit_links(LINKS,TRADES,"GBP",True,1)
        self.assertTrue(r["trade_ownership_verified"])
        self.assertFalse(r["net_realised_gbp_verified"])
        self.assertEqual(r["broker_xag_short_closed"],1)
        self.assertEqual(r["broker_nonzero_financing_trades"],1)
        self.assertEqual(r["broker_shorts_not_locally_linked"],0)
        self.assertIn("net_GBP_transactions_not_yet_attributed",r["blockers"])
        self.assertEqual(r["reporting_status"],"awaiting_reconciliation")
        self.assertNotIn("realised_week_gbp",r)

    def test_broker_trade_without_local_link_fails_ownership(self):
        t=TRADES+[{"id":"102","instrument":"XAG_USD","initialUnits":"-1",
                   "state":"CLOSED","realizedPL":"0.5",
                   "closeTime":"2026-11-17T11:00:00Z"}]
        r=audit_links(LINKS,t,"GBP",True,1)
        self.assertFalse(r["trade_ownership_verified"])
        self.assertEqual(r["broker_shorts_not_locally_linked"],1)

    def test_broker_closed_while_local_open_flags_reconciliation_need(self):
        local=[dict(LINKS[0]),dict(LINKS[1])]
        local[1]["status"]="OPEN"
        broker=[dict(t) for t in TRADES]
        broker[1].update(state="CLOSED",closeTime="2026-11-17T12:00:00Z")
        r=audit_links(local,broker,"GBP",True,1)
        self.assertEqual(r["broker_closed_not_locally_closed"],1)
        self.assertFalse(r["trade_ownership_verified"])

    def test_incomplete_history_is_never_verified(self):
        r=audit_links(LINKS,TRADES,"GBP",False,6)
        self.assertFalse(r["trade_ownership_verified"])
        self.assertIn("broker_history_not_exhaustive",r["blockers"])

    def test_non_gbp_account_never_relabels_broker_money(self):
        r=audit_links(LINKS,TRADES,"USD",True,1)
        self.assertFalse(r["trade_ownership_verified"])
        self.assertIn("GBP_account_currency_unverified",r["blockers"])

    def test_open_partial_realisation_and_multiclose_are_counted(self):
        trades=[dict(t) for t in TRADES]
        trades[1]["realizedPL"]="0.65"
        trades[1]["closingTransactionIDs"]=["111","112"]
        r=audit_links(LINKS,trades,"GBP",True,1)
        self.assertEqual(r["broker_open_partially_realised"],1)
        self.assertEqual(r["broker_multiclose_trades"],1)

    def test_missing_trade_link_id_invalidates_coverage(self):
        links=LINKS+[{"broker_trade_id":"","status":"CLOSED"}]
        r=audit_links(links,TRADES,"GBP",True,1)
        self.assertFalse(r["trade_ownership_verified"])
        self.assertEqual(r["local_invalid_trade_ids"],1)

if __name__=="__main__":
    unittest.main()
