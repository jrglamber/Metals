"""Combined Metals top-card scope regression, keeping NAV and XAU protection separate."""
import unittest
import metals_live_open_pnl_display as display

def top():
    return {
        "status":"ok",
        "account":{"nav":5434.42,"balance":5423.88,"unrealized_pl":10.54},
        "accounting":{"week_pnl":19.09,"month_pnl":37.63},
        "strategy":{"headline_pnl":8.49,"open_pnl":8.49,"realized_pnl":248.69,
                    "open_trades":16,"local_open_trades":16,
                    "high_water_gbp":61.79,"high_water_r":9.40,
                    "giveback_gbp":53.29,"giveback_pct":86.3,
                    "giveback_r":8.11,"basket_r":1.29},
    }

def proof():
    return {"ok":True,"scope":display.SCOPE,
            "open_count":38,"xau_count":16,"xag_short_count":22,
            "open_pnl_gbp":11.35,"high_water_status":"OBSERVED_COMBINED_LIVE",
            "high_water_gbp":72.24,"giveback_gbp":60.89,"giveback_pct":84.28,
            "high_water_at_utc":"2026-11-17T01:26:00Z",
            "high_water_tracking_started_at_utc":"2026-11-16T18:38:00Z"}

class DashboardTopTests(unittest.TestCase):
    def test_combined_does_not_corrupt_nav_or_realised_or_xau_hwm(self):
        original=top()
        out=display.project_dashboard_top(original,proof())
        s=out["strategy"]
        self.assertEqual((s["headline_pnl"],s["open_trades"]),(11.35,38))
        self.assertEqual((s["xau_count"],s["xag_short_count"]),(16,22))
        self.assertEqual((s["high_water_gbp"],s["giveback_gbp"]),(72.24,60.89))
        self.assertEqual(s["giveback_pct"],84.28)
        for k in ("basket_r","high_water_r","giveback_r"):
            self.assertIsNone(s[k])
        self.assertEqual(out["account"]["nav"],5434.42)
        self.assertEqual(out["accounting"]["week_pnl"],19.09)
        self.assertEqual(s["realized_pnl"],248.69)
        self.assertEqual(out["xau_only_strategy"]["open_trades"],16)
        self.assertEqual(out["xau_only_strategy"]["high_water_gbp"],61.79)
        self.assertEqual(original["strategy"]["open_trades"],16)

    def test_unverified_combined_never_falls_back_to_xau_financials(self):
        out=display.project_dashboard_top(top(),{"ok":False,"reason":"XAG ownership mismatch"})
        s=out["strategy"]
        for k in ("headline_pnl","open_pnl","open_trades","high_water_gbp",
                  "giveback_gbp","giveback_pct","high_water_r","giveback_r"):
            self.assertIsNone(s[k],k)
        self.assertEqual(s["combined_reporting_status"],"BROKER_UNVERIFIED")
        self.assertEqual(out["account"]["nav"],5434.42)

    def test_missing_peak_does_not_hide_verified_open_pnl(self):
        p=proof();p["high_water_status"]="UNAVAILABLE"
        s=display.project_dashboard_top(top(),p)["strategy"]
        self.assertEqual(s["headline_pnl"],11.35)
        self.assertEqual(s["open_trades"],38)
        self.assertIsNone(s["high_water_gbp"])
        self.assertEqual(s["combined_reporting_status"],"HWM_UNAVAILABLE")

    def test_missing_top_renderer_fails_closed(self):
        with self.assertRaises(ValueError):
            display.rewrite_dashboard_html("async function loadTop(force=false){}")

if __name__=="__main__": unittest.main()
