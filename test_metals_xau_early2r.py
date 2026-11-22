"""Unit tests for the proposed XAU short early-2R protection.

These tests have NO broker calls and do not enable live trading.
The feature is still draft until separately validated and reviewed.
"""
import unittest

from metals_intrahour_exit_override import (
    EARLY_MFE_POLICY,
    _early_short_candidate,
    _early_short_selftest,
)


class FakeCore:
    safe_float = staticmethod(lambda value: float(value) if value is not None else None)
    safe_str = staticmethod(lambda value: str(value or ""))


class XauEarlyTwoRTests(unittest.TestCase):
    def setUp(self):
        self.core = FakeCore()
        self.link = {
            "side": "short", "active_exit_policy": EARLY_MFE_POLICY,
            "entry_price": 100.0, "stop_price": 102.0,
        }
        self.metrics = {
            "side": "short", "hold_candles": 12,
            "mfe_r": 2.0, "current_price": 96.0,
        }

    def candidate(self, *, link=None, metrics=None, actual_stop=102.0):
        return _early_short_candidate(
            self.core,
            self.link if link is None else link,
            self.metrics if metrics is None else metrics,
            actual_stop,
        )

    def test_self_test(self):
        self.assertTrue(_early_short_selftest()["ok"])

    def test_arms_at_exactly_two_r(self):
        self.assertFalse(self.candidate(metrics={**self.metrics, "mfe_r": 1.999})["eligible"])
        self.assertEqual(self.candidate()["stop_price"], 97.0)

    def test_raises_short_protection_at_three_r(self):
        result = self.candidate(metrics={**self.metrics, "mfe_r": 3.0})
        self.assertTrue(result["eligible"])
        self.assertEqual(result["stop_price"], 95.5)

    def test_never_loosens_existing_broker_stop(self):
        self.assertFalse(self.candidate(actual_stop=96.5)["eligible"])
        self.assertFalse(self.candidate(actual_stop=97.0)["eligible"])

    def test_restart_with_protected_broker_stop(self):
        result = self.candidate(
            metrics={**self.metrics, "mfe_r": 2.5}, actual_stop=97.0
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["stop_price"], 96.25)
        self.assertFalse(
            self.candidate(metrics={**self.metrics, "mfe_r": 1.0}, actual_stop=97.0)["eligible"]
        )

    def test_48_candle_transition_preserves_manager_ownership(self):
        for hold in (48, 49, 120):
            self.assertFalse(
                self.candidate(metrics={**self.metrics, "hold_candles": hold})["eligible"]
            )

    def test_no_effect_on_long_or_different_exit_policy(self):
        self.assertFalse(
            self.candidate(
                link={**self.link, "side": "long"},
                metrics={**self.metrics, "side": "long"},
            )["eligible"]
        )
        self.assertFalse(
            self.candidate(
                link={**self.link, "active_exit_policy": "ATR2_CHANDELIER"}
            )["eligible"]
        )

    def test_missing_or_nonnumeric_risk_fails_closed(self):
        for stop in (None, 99.0, 100.0):
            self.assertFalse(
                self.candidate(link={**self.link, "stop_price": stop})["eligible"]
            )
        self.assertFalse(
            self.candidate(metrics={**self.metrics, "mfe_r": float("nan")})["eligible"]
        )


if __name__ == "__main__":
    unittest.main()
