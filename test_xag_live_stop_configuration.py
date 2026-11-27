import unittest
import xag_short_live_override as xag


class XagLiveStopConfigurationTests(unittest.TestCase):
    def test_stop_changes_without_increasing_per_trade_risk_or_size(self):
        self.assertEqual(xag.SL_PCT, 4.5)
        self.assertEqual(xag.TARGET_RISK_GBP, 2.0)
        self.assertEqual(xag.PILOT_UNITS, 1.0)


if __name__ == "__main__":
    unittest.main()
