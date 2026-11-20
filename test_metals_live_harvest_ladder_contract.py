"""Shared screenshot-aligned LIVE XAU/XAG banking ladder acceptance tests.

Trading engines remain independent; tests assert exact future rungs and fractions.
"""
import unittest

import metals_harvest_ladder_override as xau
import metals_xag_live_harvest as xag


class BothMetalsLadderContract(unittest.TestCase):
    EXPECTED = [30.0, 50.0, 100.0, 150.0, 200.0, 250.0, 300.0]

    def test_xau_exact_screenshot_ladder(self):
        self.assertEqual(xau._level_sequence(300), self.EXPECTED)
        self.assertEqual(xau._level_sequence(42.4128), [30.0])
        self.assertEqual(xau._level_sequence(50.0), [30.0, 50.0])
        self.assertEqual(xau._level_sequence(100.0), [30.0, 50.0, 100.0])

    def test_xau_next_level_boundaries(self):
        for current, expected in (
            (0, 30), (29.99, 30), (30, 50), (42.4128, 50),
            (49.99, 50), (50, 100), (80, 100), (99.99, 100),
            (100, 150), (149.99, 150), (150, 200), (250, 300),
        ):
            with self.subTest(current=current):
                self.assertEqual(xau._next_level(current), expected)

    def test_bank_fractions_match_screenshot(self):
        expected = [0.20, 0.20, 0.20, 0.25, 0.25, 0.25, 0.25]
        self.assertEqual([xau._fraction_for_level(r) for r in self.EXPECTED], expected)
        self.assertEqual([xag._fraction(r) for r in self.EXPECTED], expected)

    def test_xag_next_milestone_given_30r_already_banked(self):
        self.assertEqual(xag._next_level([]), 30)
        self.assertEqual(xag._next_level([30]), 50)
        self.assertEqual(xag._next_level([30, 50]), 100)
        self.assertEqual(xag._next_level([30, 50, 100]), 150)
        self.assertEqual(xag._next_level([30, 80]), 100)

    def test_existing_30r_execution_remains_terminal(self):
        self.assertIn("EXECUTED", xau._TERMINAL)
        self.assertNotIn("EXECUTED", {"NOT_ARMED", "EXECUTING", "REVIEW_REQUIRED"})
        self.assertEqual(xag._next_level([30]), 50)

    def test_does_not_mutate_shared_practice_ladder(self):
        self.assertEqual(xau.FIRST_LEVEL_R, 30.0)
        self.assertEqual(xau.STEP_R, 50.0)
        self.assertIn("_metals_xau_harvest_v2_level_sequence", __import__(
            "inspect").getsource(xau._install_live_tick))
        self.assertNotIn("core.METALS_HARVEST_FIRST_LEVEL_R =",
                         __import__("inspect").getsource(xau._install_live_tick))


if __name__ == "__main__":
    unittest.main()
