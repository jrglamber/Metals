import math
import unittest

from metals_intrahour_exit_override import _xau_broker_stop_price


class XauOandaStopPrecisionTests(unittest.TestCase):
    def test_short_candidate_ceil_three_decimals(self):
        self.assertEqual(_xau_broker_stop_price(3756.4475, "short"), 3756.448)
        self.assertEqual(_xau_broker_stop_price(3754.5074999999997, "short"), 3754.508)
        self.assertEqual(_xau_broker_stop_price(3753.5449999999996, "short"), 3753.545)

    def test_long_candidate_floor_three_decimals(self):
        self.assertEqual(_xau_broker_stop_price(3756.4475, "long"), 3756.447)
        self.assertEqual(_xau_broker_stop_price(3756.448, "long"), 3756.448)

    def test_reject_invalid_stop_or_side(self):
        for value in (None, -1, 0, float("inf"), float("-inf"), float("nan")):
            self.assertIsNone(_xau_broker_stop_price(value, "short"))
        self.assertIsNone(_xau_broker_stop_price(3756.4475, "unknown"))

    def test_rounding_is_away_from_market(self):
        for raw in (3756.4475, 3754.5074999999997, 3753.5449999999996):
            s = _xau_broker_stop_price(raw, "short")
            l = _xau_broker_stop_price(raw, "long")
            self.assertGreaterEqual(s, raw)
            self.assertLessEqual(l, raw)
            self.assertTrue(math.isclose(s * 1000, round(s * 1000), abs_tol=1e-7))
            self.assertTrue(math.isclose(l * 1000, round(l * 1000), abs_tol=1e-7))


if __name__ == "__main__":
    unittest.main()
