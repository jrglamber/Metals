import unittest
from datetime import datetime,timezone
from metals_xag_transaction_audit import transaction_financials

NOW=datetime(2026,11,17,20,0,tzinfo=timezone.utc)
LINKS=[{"broker_trade_id":"11"},{"broker_trade_id":"12"}]
TRADES=[
 {"id":"11","realizedPL":"4.20","instrument":"XAG_USD","state":"CLOSED"},
 {"id":"12","realizedPL":"0.00","instrument":"XAG_USD","state":"OPEN"},
]

def opening(tx_id="11"):
    return {"id":"11","type":"ORDER_FILL","time":"2026-11-16T18:00:00Z",
            "instrument":"XAG_USD","pl":"0","financing":"0",
            "commission":"0","guaranteedExecutionFee":"0",
            "tradeOpened":{"tradeID":tx_id},
            "tradesClosed":[]}

def closing():
    return {"id":"20","type":"ORDER_FILL","time":"2026-11-17T10:00:00Z",
            "instrument":"XAG_USD","pl":"4.20","financing":"-0.02",
            "commission":"0.05","guaranteedExecutionFee":"0",
            "tradesClosed":[{"tradeID":"11","realizedPL":"4.20","financing":"-0.02"}]}

def daily():
    return {"id":"19","type":"DAILY_FINANCING","time":"2026-11-16T22:00:00Z",
            "positionFinancings":[
                 {"instrument":"XAG_USD","financing":"-0.08",
                  "openTradeFinancings":[{"tradeID":"11","financing":"-0.05"},
                                          {"tradeID":"12","financing":"-0.03"}]},
                 {"instrument":"XAU_USD","financing":"-2.01",
                  "openTradeFinancings":[{"tradeID":"888","financing":"-2.01"}]},
            ]}

class TransactionAuditTests(unittest.TestCase):
    def rows(self):
        return [opening(),{"id":"12","type":"ORDER_FILL",
                           "time":"2026-11-16T19:00:00Z","instrument":"XAG_USD",
                           "pl":"0","financing":"0","tradeOpened":{"tradeID":"12"}},
                daily(), closing()]

    def test_verified_net_cash_includes_xag_daily_financing_and_commission(self):
        d=transaction_financials(self.rows(),LINKS,TRADES,now=NOW)
        self.assertTrue(d["ok"],d["reconciliation_issues"])
        self.assertAlmostEqual(d["realised_all_time_gbp"],4.05)
        self.assertAlmostEqual(d["realised_week_gbp"],4.05)
        self.assertAlmostEqual(d["realised_month_gbp"],4.05)
        self.assertEqual(d["broker_daily_financing_gbp"],-0.08)
        self.assertEqual(d["broker_fill_financing_gbp"],-0.02)
        self.assertEqual(d["broker_commission_gbp"],0.05)
        self.assertEqual(d["broker_fill_realised_pl_gbp"],4.2)

    def test_unlinked_xag_fill_fails_all_totals(self):
        rows=self.rows()+[{"id":"21","type":"ORDER_FILL","time":"2026-11-17T10:00:00Z",
                           "instrument":"XAG_USD","pl":"1.0","tradeOpened":{"tradeID":"999"}}]
        d=transaction_financials(rows,LINKS,TRADES,now=NOW)
        self.assertFalse(d["ok"])
        self.assertIn("xag_fill_unowned_trade",d["reconciliation_issues"])
        self.assertIsNone(d["realised_all_time_gbp"])

    def test_duplicated_broker_transaction_blocked(self):
        rows=self.rows()+[closing()]
        d=transaction_financials(rows,LINKS,TRADES,now=NOW)
        self.assertFalse(d["ok"])
        self.assertIn("transaction_id_missing_or_repeated",d["reconciliation_issues"])

    def test_position_financing_without_per_trade_breakdown_is_not_allocated(self):
        rows=self.rows()
        rows[2]={"id":"19","type":"DAILY_FINANCING","time":"2026-11-16T22:00:00Z",
                 "positionFinancings":[{"instrument":"XAG_USD","financing":"-0.08"}]}
        d=transaction_financials(rows,LINKS,TRADES,now=NOW)
        self.assertFalse(d["ok"])
        self.assertIn("xag_daily_financing_trade_breakdown_missing",d["reconciliation_issues"])

    def test_broker_trade_pl_independent_crosscheck(self):
        bad=[dict(t) for t in TRADES]
        bad[0]["realizedPL"]="9"
        d=transaction_financials(self.rows(),LINKS,bad,now=NOW)
        self.assertFalse(d["ok"])
        self.assertIn("order_fill_pl_mismatch_broker_trade_history",d["reconciliation_issues"])

    def test_mixed_broker_trade_ids_not_assigned_by_instrument(self):
        rows=[opening()]+[dict(closing(),tradesClosed=[
            {"tradeID":"11","realizedPL":"2","financing":"0"},
            {"tradeID":"999","realizedPL":"2.20","financing":"-0.02"},
        ])]
        d=transaction_financials(rows,LINKS,TRADES,now=NOW)
        self.assertFalse(d["ok"])
        self.assertIn("fill_mixed_or_missing_trade_ownership",d["reconciliation_issues"])

    def test_partial_close_allocated_by_transaction_timestamp(self):
        trade=[{"id":"11","realizedPL":"4.20","instrument":"XAG_USD","state":"CLOSED"}]
        link=[{"broker_trade_id":"11"}]
        rows=[opening(),{
            "id":"18","type":"ORDER_FILL","time":"2026-10-31T20:00:00Z",
            "instrument":"XAG_USD","pl":"1.20","financing":"0",
            "tradesClosed":[{"tradeID":"11","realizedPL":"1.20","financing":"0"}],
        },{
            "id":"20","type":"ORDER_FILL","time":"2026-11-17T10:00:00Z",
            "instrument":"XAG_USD","pl":"3.00","financing":"0",
            "tradesClosed":[{"tradeID":"11","realizedPL":"3.00","financing":"0"}],
        }]
        d=transaction_financials(rows,link,trade,now=NOW)
        self.assertTrue(d["ok"],d["reconciliation_issues"])
        self.assertEqual(d["realised_all_time_gbp"],4.2)
        self.assertEqual(d["realised_month_gbp"],3.0)
        self.assertEqual(d["realised_week_gbp"],3.0)


class AuditCacheRecoveryTests(unittest.TestCase):
    def test_transient_failed_audit_retries_without_five_minute_lockout(self):
        import metals_xag_transaction_audit as audit
        from unittest.mock import patch
        saved=(audit._cache, audit._at, audit._consecutive_failures)
        try:
            audit._cache=None
            audit._at=0.0
            audit._consecutive_failures=0
            with patch.object(audit, "audit_live", side_effect=[
                {"ok":False, "error_type":"BrokerUnavailable"},
                {"ok":True, "version":audit.VERSION},
            ]) as read:
                first=audit.snapshot(None)
                self.assertFalse(first["ok"])
                self.assertEqual(read.call_count, 1)
                self.assertEqual(first["retry_delay_seconds"], 30)
                audit.snapshot(None)
                self.assertEqual(read.call_count, 1)
                audit._at -= 31.0
                fixed=audit.snapshot(None)
                self.assertTrue(fixed["ok"])
                self.assertEqual(read.call_count, 2)
                self.assertEqual(audit._consecutive_failures, 0)
        finally:
            audit._cache,audit._at,audit._consecutive_failures=saved

    def test_repeated_failure_has_bounded_backoff(self):
        import metals_xag_transaction_audit as audit
        from unittest.mock import patch
        saved=(audit._cache, audit._at, audit._consecutive_failures)
        try:
            audit._cache=None
            audit._at=0.0
            audit._consecutive_failures=0
            with patch.object(audit, "audit_live", return_value={"ok":False}):
                expected=[30,60,120,240,300,300]
                for delay in expected:
                    out=audit.snapshot(None)
                    self.assertEqual(out["retry_delay_seconds"], delay)
                    audit._at -= delay + 1
        finally:
            audit._cache,audit._at,audit._consecutive_failures=saved

if __name__=="__main__":
    unittest.main()
