"""Contract tests for authoritative read-only Metals reporting sample."""
import sys
import threading
import types
import unittest
from unittest.mock import patch
import metals_reporting_snapshot as report


def positions():
    return {
        "ok": True, "scope": report.SCOPE,
        "count": 2, "xau_count": 1, "xag_count": 1,
        "xag_short_count": 1, "xau_ok": True, "xag_ok": True,
        "trades": [
            {"broker_trade_id": "11", "instrument": "XAU_USD"},
            {"broker_trade_id": "12", "instrument": "XAG_USD"},
        ],
    }


def risk():
    return {
        "ok": True, "scope": "metals", "open_trade_count": 2,
        "quantified_trade_count": 2, "unquantified_trade_count": 0,
        "broker_trade_ids": ["12","11"],
        "calculated_at_utc": "2026-11-18T13:00:00+00:00",
        "by_instrument": {
            "XAU_USD": {"open_trade_count": 1},
            "XAG_USD": {"open_trade_count": 1, "short_trade_count": 1,
                        "long_trade_count": 0},
        },
    }


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.old_cache,self.old_at = report._cache,report._cache_at
        report._cache=None
        report._cache_at=0

    def tearDown(self):
        report._cache,report._cache_at=self.old_cache,self.old_at

    def fakes(self, *, bad_cash=False, risk_mismatch=False):
        data=types.ModuleType("metals_dashboard_clean_entrypoint")
        data.live_metals_open_positions=lambda: positions()
        reconciler=types.ModuleType("metals_live_open_pnl_display")
        reconciler.reconcile=lambda p: {
            "ok": True,"scope": report.SCOPE,"open_count":p["count"],
            "open_pnl_gbp":18.7,
            "observed_at_utc":"2026-11-18T13:00:00+00:00",
        }
        peak=types.ModuleType("metals_combined_observed_hwm")
        peak.observe=lambda core, p: {
            "high_water_status":"OBSERVED_COMBINED_LIVE",
            "high_water_gbp":20.0,"giveback_gbp":1.3,
        }
        cash=types.ModuleType("metals_xag_transaction_audit")
        cash.snapshot=lambda core:{"ok": not bad_cash,"audit_refresh_in_progress":bad_cash}
        verifier=types.ModuleType("metals_xag_realised_verified")
        def verify(p):
            if not p["ok"]:
                raise ValueError("cash refreshing")
            return {"ok":True,"time_utc":"2026-11-18T13:00:00+00:00"}
        verifier.verify=verify
        stop=types.ModuleType("portfolio_live_risk")
        stop._read=lambda mode:dict(risk(), broker_trade_ids=["999","11"] if risk_mismatch else ["12","11"])
        stop._calculate=lambda mode:dict(risk(), broker_trade_ids=["999","11"] if risk_mismatch else ["12","11"])
        return {k:v for k,v in [
            ("metals_dashboard_clean_entrypoint",data),
            ("metals_live_open_pnl_display",reconciler),
            ("metals_combined_observed_hwm",peak),
            ("metals_xag_transaction_audit",cash),
            ("metals_xag_realised_verified",verifier),
            ("portfolio_live_risk",stop),
        ]}

    def test_broker_matched_ids_and_cash_have_independent_provenance(self):
        with patch.dict(sys.modules,self.fakes()):
            d=report.snapshot(object())
        self.assertTrue(d["complete"])
        self.assertEqual(d["combined"]["open_pnl_gbp"],18.7)
        self.assertEqual(d["risk"]["data"]["broker_trade_ids"],["12","11"])
        self.assertTrue(d["cash"]["ok"])

    def test_missing_cash_does_not_erase_broker_positions_or_stops(self):
        with patch.dict(sys.modules,self.fakes(bad_cash=True)):
            d=report.snapshot(object())
        self.assertTrue(d["combined"]["ok"])
        self.assertTrue(d["risk"]["ok"])
        self.assertEqual(d["cash"]["status"],"REFRESHING")
        self.assertFalse(d["complete"])

    def test_wrong_risk_ids_never_promoted_even_if_counts_match(self):
        with patch.dict(sys.modules,self.fakes(risk_mismatch=True)):
            d=report.snapshot(object())
        self.assertTrue(d["combined"]["ok"])
        self.assertTrue(d["cash"]["ok"])
        self.assertIsNone(d["risk"]["data"])
        self.assertEqual(d["risk"]["status"],"SNAPSHOT_MISMATCH")

    def test_cached_sample_defensively_copied(self):
        with patch.dict(sys.modules,self.fakes()):
            d=report.snapshot(object())
            d["positions"]["trades"][0]["broker_trade_id"]="MUTATED"
            again=report.snapshot(object())
        self.assertEqual(again["positions"]["trades"][0]["broker_trade_id"],"11")

    def test_stale_risk_forces_recheck(self):
        risk_input=dict(risk(),stale=True)
        with patch.dict(sys.modules,self.fakes()) as fake:
            fake["portfolio_live_risk"]._read=lambda mode:risk_input
            d=report.snapshot(object())
        self.assertTrue(d["risk"]["ok"])


if __name__=="__main__":
    unittest.main()
