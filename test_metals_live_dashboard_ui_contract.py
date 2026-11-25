"""Source contracts for LIVE metals UI: no demo mixing or invented NAV capacity."""
from pathlib import Path
import unittest


class LiveMetalsUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout=Path("metals_dashboard_clean_entrypoint.py").read_text()
        cls.top=Path("metals_xag_live_wording_entrypoint.py").read_text()
        cls.closed=Path("metals_recent_closed_dom_override.py").read_text()

    def test_broker_verified_xau_and_xag_nav_budget_data(self):
        for endpoint in ("/api/xau-nav-budget", "/api/xag-nav-budget"):
            self.assertIn(endpoint,self.top)
        for field in ("cap_gbp","risk_used_gbp","cap_pct_nav"):
            self.assertIn(field,self.top)
        for heading in ("NAV Risk Budgets", "Allowance", "Used", "Available"):
            self.assertIn(heading,self.top)
        self.assertIn("Math.max(0,cap-used)", self.top)
        self.assertIn("Math.max(0,used-cap)", self.top)
        self.assertIn("Over budget by",self.top)
        self.assertIn("new entries fail closed",self.top)
        self.assertIn("if(!data||data.ok!==true)",self.top)
        self.assertIn("pep-metals-risk-budget-script",self.top)
        self.assertIn("if(!document.hidden)refresh()",self.top)

    def test_one_combined_live_open_table_with_xau_and_xag_manager_metrics(self):
        self.assertIn("xau_open.snapshot(core)",self.layout)
        self.assertIn("xag_open.snapshot(core)",self.layout)
        self.assertIn("MFE (R)",self.layout)
        self.assertIn("MAE (R)",self.layout)
        self.assertIn("Giveback from MFE~",self.layout)
        self.assertIn("Manager review",self.layout)
        self.assertIn("XAU LONG and SHORT",self.layout)

    def test_live_recent_closed_is_one_asset_filterable_feed(self):
        self.assertIn("/api/live-metals-recently-closed",self.closed)
        self.assertIn("pep-closed-asset-filter",self.closed)
        self.assertIn("LIVE XAU LONG / SHORT + XAG SHORT",self.closed)
        self.assertIn("Demo excluded",self.closed)

if __name__ == "__main__":
    unittest.main()
