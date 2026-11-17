"""Regression for /api/live-metals-lane-accounting behind the root mount."""
import unittest
from fastapi import FastAPI
from metals_route_priority import promote_read_only_route


class RoutePriorityTests(unittest.TestCase):
    def test_promotes_late_accounting_route_before_root_mount(self):
        app = FastAPI()
        app.mount("/", FastAPI())  # Production mount shadows subsequent API routes.

        @app.get("/api/live-metals-lane-accounting")
        def ledger():
            return {"ok": False, "reporting_status": "awaiting_reconciliation"}

        route = promote_read_only_route(app, "/api/live-metals-lane-accounting", ledger)
        self.assertEqual(route["route_index"], 0)
        self.assertTrue(route["read_only"])
        self.assertEqual(app.router.routes[0].path, "/api/live-metals-lane-accounting")
        self.assertIs(app.router.routes[0].endpoint, ledger)
        self.assertTrue(any(getattr(r, "path", None) in ("", "/") for r in app.router.routes[1:]))

    def test_missing_route_fails_closed(self):
        app = FastAPI()
        with self.assertRaises(RuntimeError):
            promote_read_only_route(app, "/api/live-metals-lane-accounting", lambda: None)

    def test_other_routes_retained(self):
        app = FastAPI()
        @app.get("/api/healthy")
        def healthy():
            return {"ok": True}
        @app.get("/api/live-metals-lane-accounting")
        def ledger():
            return {"ok": False}
        before = len(app.router.routes)
        promote_read_only_route(app, "/api/live-metals-lane-accounting", ledger)
        self.assertEqual(len(app.router.routes), before)
        self.assertIn("/api/healthy", [r.path for r in app.router.routes])


if __name__ == "__main__":
    unittest.main()
