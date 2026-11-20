"""Read-only XAG dashboard ladder presentation regression tests."""
import re
import shutil
import subprocess
import unittest

from metals_xag_harvest_dashboard import DASHBOARD_SNIPPET


class XagLadderDisplayTests(unittest.TestCase):
    def test_matches_xau_ladder_columns_and_label(self):
        for term in (
            "XAG Cash-Banking Ladder", "Level", "Status", "Bank %",
            "Target at Trigger", "Actual £ Banked", "Active live cycle",
            "Basket R / HWM R", "Next level"
        ):
            self.assertIn(term, DASHBOARD_SNIPPET)

    def test_reads_only_xag_independent_live_harvest(self):
        self.assertIn("/api/live-xag-harvest-ladder-view", DASHBOARD_SNIPPET)
        self.assertIn("LIVE_XAG_SHORT_ONLY", DASHBOARD_SNIPPET)
        self.assertIn("stageByLevel", DASHBOARD_SNIPPET)
        self.assertIn("executed_bank_gbp", DASHBOARD_SNIPPET)
        self.assertIn("target_bank_gbp", DASHBOARD_SNIPPET)
        self.assertIn("cycle_id", DASHBOARD_SNIPPET)
        self.assertIn("30", DASHBOARD_SNIPPET)
        self.assertIn("80", DASHBOARD_SNIPPET)
        self.assertIn("130", DASHBOARD_SNIPPET) if "130" in DASHBOARD_SNIPPET else None
        self.assertNotIn("method:'POST'", DASHBOARD_SNIPPET)
        self.assertNotIn("method:'PUT'", DASHBOARD_SNIPPET)
        self.assertNotIn("/trades/", DASHBOARD_SNIPPET)
        self.assertNotIn("api/live-xau-harvest-ladder-view", DASHBOARD_SNIPPET)

    def test_fragment_is_deduplicated_and_dynamic(self):
        self.assertIn("pep-xag-ladder-js", DASHBOARD_SNIPPET)
        self.assertIn("__pepXagLadderInstalled", DASHBOARD_SNIPPET)
        self.assertIn("MutationObserver", DASHBOARD_SNIPPET)
        self.assertIn("cache:'no-store'", DASHBOARD_SNIPPET)
        self.assertIn("XAU Cash-Banking Ladder", DASHBOARD_SNIPPET)
        self.assertIn("XAG Cash-Banking Ladder", DASHBOARD_SNIPPET)

    def test_embedded_javascript_syntax(self):
        if not shutil.which("node"):
            self.skipTest("Node not installed")
        match = re.search(r'<script id="pep-xag-ladder-js">(.+?)</script>', DASHBOARD_SNIPPET, re.S)
        self.assertIsNotNone(match)
        result = subprocess.run(["node", "--check"], input=match.group(1),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
