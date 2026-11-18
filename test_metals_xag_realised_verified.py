import unittest
from datetime import datetime,timezone,timedelta
from metals_xag_realised_verified import verify,apply_top

NOW=datetime(2026,11,17,21,4,tzinfo=timezone.utc)

def audit():
    return {
        "ok":True,"time_utc":"2026-11-17T21:04:00Z",
        "trade_ownership_verified":True,"transaction_history_complete":True,
        "net_realised_gbp_verified":True,"broker_trade_cumulative_pl_verified":True,
        "account_currency":"GBP","reconciliation_issues":[],"version":"v1",
        "broker_trade_count":77,"broker_closed_xag_trades":56,"transaction_count":2230,
        "financial_components_gbp":{"order_fills":-32.562,"daily_financing":0.4399,
                                   "net_gbp":-32.1221},
        "realised_today_gbp":1.25,"realised_week_gbp":12.8748,
        "realised_month_gbp":-32.1221,"realised_all_time_gbp":-32.1221,
    }

def top():
    return {"status":"ok","account":{"nav":5450.0},
            "accounting":{"week_pnl":20.82,"month_pnl":39.37},
            "strategy":{"realized_pnl":250.43,"open_pnl":20.38,
                        "high_water_gbp":72.24,"giveback_gbp":51.86,
                        "open_trades":36},
            "xau_only_strategy":{"realized_pnl":250.43,
                                 "high_water_gbp":60.0}}

class VerifiedRealisedTests(unittest.TestCase):
    def test_promote_only_proven_transaction_cash(self):
        proof=verify(audit(),now=NOW)
        t=apply_top(top(),proof)
        self.assertAlmostEqual(t["strategy"]["realized_pnl"],218.3079)
        self.assertAlmostEqual(t["accounting"]["week_pnl"],33.6948)
        self.assertAlmostEqual(t["accounting"]["month_pnl"],7.2479)
        self.assertEqual(t["account"]["nav"],5450.0)
        self.assertEqual(t["strategy"]["high_water_gbp"],72.24)
        self.assertEqual(t["strategy"]["open_trades"],36)
        self.assertEqual(t["xau_only_strategy"]["realized_pnl"],250.43)
        self.assertTrue(t["strategy"]["xag_realised_verified"])
        self.assertEqual(t["strategy"]["realised_scope"],
            "XAU_LEGACY_PLUS_XAG_SHORT_NET_BROKER_VERIFIED")

    def test_failed_verification_preserves_xau_only_without_mislabeling_total(self):
        x=apply_top(top(),{"ok":False})
        self.assertIsNone(x["strategy"]["realized_pnl"])
        self.assertIsNone(x["accounting"]["week_pnl"])
        self.assertIsNone(x["accounting"]["month_pnl"])
        self.assertEqual(x["xau_only_strategy"]["realized_pnl"],250.43)
        self.assertEqual(x["xau_only_accounting"]["week_pnl"],20.82)
        self.assertFalse(x["strategy"]["xag_realised_verified"])
        self.assertEqual(x["strategy"]["realised_scope"],"COMBINED_METALS_CASH_UNVERIFIED")

    def test_no_double_add_across_source(self):
        src=top()
        x=apply_top(src,verify(audit(),now=NOW))
        self.assertEqual(src["strategy"]["realized_pnl"],250.43)
        self.assertEqual(x["xau_only_accounting"]["month_pnl"],39.37)

    def test_bad_currency_fails(self):
        p=audit();p["account_currency"]="USD"
        with self.assertRaises(ValueError):verify(p,now=NOW)

    def test_missing_or_stale_broker_evidence_fails(self):
        for field in ("trade_ownership_verified","transaction_history_complete",
                      "net_realised_gbp_verified","broker_trade_cumulative_pl_verified"):
            p=audit();p[field]=False
            with self.subTest(field=field),self.assertRaises(ValueError):
                verify(p,now=NOW)
        with self.assertRaises(ValueError):
            verify(audit(),now=NOW+timedelta(minutes=30))

    def test_money_mismatch_or_missing_fails(self):
        p=audit();p["financial_components_gbp"]["net_gbp"]=-30.0
        with self.assertRaises(ValueError):verify(p,now=NOW)
        p=audit();p["realised_week_gbp"]=None
        with self.assertRaises(ValueError):verify(p,now=NOW)

if __name__=="__main__":
    unittest.main()
