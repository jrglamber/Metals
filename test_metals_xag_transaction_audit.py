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

    def test_verified_event_export_matches_net_without_trade_ids(self):
        d=transaction_financials(self.rows(),LINKS,TRADES,now=NOW)
        self.assertTrue(d["ok"],d["reconciliation_issues"])
        rows=d["cash_events"]
        self.assertEqual(len(rows),2)
        self.assertAlmostEqual(sum(r["net_realized_gbp"] for r in rows),4.05)
        self.assertEqual(set(rows[0]),{"transaction_time_utc","transaction_type","net_realized_gbp"})

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


class TransactionHistoryCheckpointTests(unittest.TestCase):
    def test_full_broker_audit_resumes_after_original_18000_id_limit(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch, MagicMock
        from urllib.parse import urlparse, parse_qs
        import metals_xag_transaction_audit as audit

        first, last = 100, 18201  # Exactly 18,102 IDs, the production failure.
        local_links = [{"broker_trade_id": str(first), "status": "CLOSED"}]
        broker_trades = [{
            "id": str(first), "instrument": "XAG_USD", "initialUnits": "-1",
            "state": "CLOSED", "realizedPL": "4.20",
            "closeTime": "2026-11-17T10:00:00Z",
        }]
        core = MagicMock()
        core.get_conn.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = local_links
        requests = []
        checkpoint = [last]

        def request(core_arg, url, method):
            self.assertEqual(method, "GET")
            base = "/v3/accounts/test-account"
            if url == base + "/summary":
                return {"ok": True, "data": {"account": {"currency": "GBP"},
                                             "lastTransactionID": str(checkpoint[0])}}
            if url.startswith(base + "/trades?"):
                return {"ok": True, "data": {"trades": broker_trades}}
            self.assertTrue(url.startswith(base + "/transactions/idrange?"))
            q = parse_qs(urlparse(url).query)
            start, end = int(q["from"][0]), int(q["to"][0])
            self.assertLessEqual(end - start + 1, audit.TX_RANGE_SIZE)
            requests.append((start, end))
            transactions = []
            for i in range(start, end + 1):
                row = {"id": str(i), "type": "ORDER_CANCEL",
                       "time": "2026-11-16T18:00:00Z", "raw_extra": "discard"}
                if i == first:
                    row = dict(opening(str(first)), id=str(first))
                elif i == last:
                    row = dict(closing(), id=str(last),
                               tradesClosed=[{"tradeID": str(first),
                                              "realizedPL": "4.20", "financing": "-0.02"}])
                elif i == last + 1:
                    row = dict(daily(), id=str(i), positionFinancings=[{
                        "instrument": "XAG_USD", "financing": "-0.05",
                        "openTradeFinancings": [{"tradeID": str(first), "financing": "-0.05"}],
                    }])
                transactions.append(row)
            return {"ok": True, "data": {"transactions": transactions}}

        fake_xag = SimpleNamespace(_account_id=lambda core: "test-account", _request=request)
        history = audit.TransactionHistory()
        with patch.dict(sys.modules, {"xag_short_live_override": fake_xag}), \
                patch.object(audit, "_history", history):
            partial = audit.audit_live(core)
            self.assertFalse(partial["ok"])
            self.assertFalse(partial["transaction_history_complete"])
            self.assertFalse(partial["net_realised_gbp_verified"])
            self.assertEqual(partial["blockers"], ["transaction_history_backfill_pending"])
            self.assertNotIn("realised_all_time_gbp", partial)
            self.assertEqual(partial["transaction_ids_remaining"], 102)
            self.assertEqual(len(requests), 60)
            self.assertNotIn("raw_extra", history.rows[1])

            full = audit.audit_live(core)
            self.assertTrue(full["ok"], full)
            self.assertTrue(full["transaction_history_complete"])
            self.assertTrue(full["broker_trade_cumulative_pl_verified"])
            self.assertEqual(full["transaction_count"], 18102)
            self.assertEqual(full["broker_transaction_pages_this_refresh"], 1)
            self.assertEqual(len(requests), 61)
            self.assertAlmostEqual(full["realised_all_time_gbp"], 4.13)
            self.assertEqual(requests[-1], (first + 18000, last))
            self.assertEqual(full["blockers"], [])

            checkpoint[0] += 1
            incremental = audit.audit_live(core)
            self.assertTrue(incremental["ok"], incremental)
            self.assertEqual(requests[-1], (last + 1, last + 1))
            self.assertEqual(len(requests), 62)
            self.assertEqual(incremental["transaction_count"], 18103)
            self.assertAlmostEqual(incremental["realised_all_time_gbp"], 4.08)

            unchanged = audit.audit_live(core)
            self.assertTrue(unchanged["ok"])
            self.assertEqual(len(requests), 62)
            self.assertEqual(unchanged["broker_transaction_pages_this_refresh"], 0)

    def test_failure_and_malformed_range_do_not_advance_checkpoint(self):
        from metals_xag_transaction_audit import TransactionHistory
        history = TransactionHistory()
        history.read("A", 1, 600, lambda a, b: {
            "ok": True, "data": {"transactions": [{"id": str(a)}]}
        }, max_pages=1)
        self.assertEqual(history.next_id, 301)
        for response in (
            {"ok": False},
            {"ok": True, "data": {"transactions": None}},
            {"ok": True, "data": {"transactions": [{"id": "300"}]}},
            {"ok": True, "data": {"transactions": [{"id": "301"}, {"id": "301"}]}},
        ):
            result = history.read("A", 1, 600, lambda a, b: response)
            self.assertFalse(result["transaction_history_complete"])
            self.assertEqual(history.next_id, 301)
            self.assertEqual(len(history.rows), 1)
        result = history.read("A", 1, 600, lambda a, b: {
            "ok": True, "data": {"transactions": [{"id": str(a)}]}
        })
        self.assertTrue(result["transaction_history_complete"])
        self.assertEqual([r["id"] for r in history.rows], ["1", "301"])

    def test_account_boundary_and_rewind_discard_old_rows(self):
        from metals_xag_transaction_audit import TransactionHistory
        history = TransactionHistory()
        def request(a, b):
            return {"ok": True, "data": {"transactions": [{"id": str(a)}]}}
        history.read("A", 1, 600, request)
        history.read("B", 1, 3, request)
        self.assertEqual([r["id"] for r in history.rows], ["1"])
        history.read("B", 2, 5, request)
        self.assertEqual([r["id"] for r in history.rows], ["2"])
        history.read("B", 2, 3, request)
        self.assertEqual([r["id"] for r in history.rows], ["2"])
        self.assertEqual(history.next_id, 4)


class AuditCacheRecoveryTests(unittest.TestCase):
    class DeferredThread:
        pending = []
        def __init__(self, target, args=(), **kwargs):
            self.target, self.args = target, args
        def start(self):
            self.pending.append(self)
        def complete(self):
            self.target(*self.args)

    def setUp(self):
        import metals_xag_transaction_audit as audit
        self.audit=audit
        self.saved=(audit._cache,audit._at,audit._consecutive_failures,audit._refreshing)
        self.DeferredThread.pending=[]
        audit._cache=None
        audit._at=0.0
        audit._consecutive_failures=0
        audit._refreshing=False

    def tearDown(self):
        self.audit._cache,self.audit._at,self.audit._consecutive_failures,self.audit._refreshing=self.saved

    def finish_one(self):
        self.assertEqual(len(self.DeferredThread.pending),1)
        self.DeferredThread.pending.pop().complete()

    def test_first_failure_retries_after_30_seconds_without_blocking(self):
        from unittest.mock import patch
        audit=self.audit
        with patch.object(audit.threading, "Thread", self.DeferredThread):
            with patch.object(audit, "audit_live", side_effect=[
                {"ok":False, "error_type":"BrokerUnavailable"},
                {"ok":True, "version":audit.VERSION},
            ]) as read:
                warming=audit.snapshot(None)
                self.assertFalse(warming["ok"])
                self.assertTrue(warming["audit_refresh_in_progress"])
                self.assertEqual(read.call_count,0)
                self.finish_one()
                self.assertEqual(read.call_count,1)
                self.assertEqual(audit.snapshot(None)["retry_delay_seconds"],30)
                audit._at-=31.0
                pending=audit.snapshot(None)
                self.assertFalse(pending["ok"])
                self.finish_one()
                self.assertTrue(audit.snapshot(None)["ok"])
                self.assertEqual(read.call_count,2)
                self.assertEqual(audit._consecutive_failures,0)

    def test_repeated_failures_back_off_and_never_block_an_api_request(self):
        from unittest.mock import patch
        audit=self.audit
        with patch.object(audit.threading, "Thread", self.DeferredThread):
            with patch.object(audit, "audit_live", return_value={"ok":False}):
                for delay in (30,60,120,240,300,300):
                    pending=audit.snapshot(None)
                    self.assertFalse(pending["ok"])
                    self.finish_one()
                    self.assertEqual(audit._cache["retry_delay_seconds"],delay)
                    audit._at-=delay+1

    def test_backfill_progress_retries_without_escalating_delay(self):
        from unittest.mock import patch
        audit=self.audit
        with patch.object(audit.threading, "Thread", self.DeferredThread):
            with patch.object(audit, "audit_live", return_value={
                "ok": False, "blockers": ["transaction_history_backfill_pending"],
                "transaction_history_complete": False,
            }):
                for _ in range(4):
                    pending=audit.snapshot(None)
                    self.assertFalse(pending["ok"])
                    self.finish_one()
                    self.assertEqual(audit._cache["retry_delay_seconds"],30)
                    audit._at-=31

    def test_bounded_stale_success_is_timestamped_and_never_silent_forever(self):
        from unittest.mock import patch
        audit=self.audit
        with patch.object(audit.threading, "Thread", self.DeferredThread):
            with patch.object(audit, "audit_live", return_value={"ok":True,"version":audit.VERSION}):
                audit.snapshot(None)
                self.finish_one()
                audit._at-=audit.CACHE_SECONDS+1
                proof=audit.snapshot(None)
                self.assertTrue(proof["ok"])
                self.assertTrue(proof["audit_refresh_in_progress"])
                self.assertIn("verified_proof_age_seconds",proof)
                # If a broker audit has not finished within its maximum proof
                # age, do not keep claiming the old cash total is verified.
                audit._at-=audit.MAX_VERIFIED_PROOF_AGE_SECONDS
                old=audit.snapshot(None)
                self.assertFalse(old["ok"])
                self.assertEqual(old["error_type"],"AUDIT_REFRESHING")
                self.finish_one()


if __name__=="__main__":
    unittest.main()
