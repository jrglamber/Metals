import sqlite3
import unittest
from contextlib import contextmanager

import app_postgres_runtime as app


class XauPersistentChopPredicateTests(unittest.TestCase):
    def test_blocks_exact_frozen_persistent_chop_state(self):
        self.assertTrue(
            app._metals_xau_persistent_chop_from_states(
                "CHOPPY", "MIXED", "CHOPPY"
            )
        )

    def test_does_not_block_when_eight_hour_state_is_mixed(self):
        self.assertFalse(
            app._metals_xau_persistent_chop_from_states(
                "MIXED", "CHOPPY", "CHOPPY"
            )
        )

    def test_does_not_block_when_longer_window_is_trending(self):
        self.assertFalse(
            app._metals_xau_persistent_chop_from_states(
                "CHOPPY", "TRENDING", "MIXED"
            )
        )

    def test_case_normalisation(self):
        self.assertTrue(
            app._metals_xau_persistent_chop_from_states(
                "choppy", "mixed", "choppy"
            )
        )

    def _gate_for_closes(self, closes):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE raw_signals (
                id INTEGER PRIMARY KEY,
                pair TEXT,
                exec_close REAL,
                model_version TEXT,
                timestamp_readable TEXT
            )
        """)
        conn.executemany(
            """
            INSERT INTO raw_signals(
                id, pair, exec_close, model_version, timestamp_readable
            ) VALUES(?, ?, ?, ?, ?)
            """,
            [
                (idx, "XAUUSD", close, "PRODUCTION", f"t{idx}")
                for idx, close in enumerate(closes, 1)
            ],
        )

        @contextmanager
        def fake_get_conn():
            yield conn

        original_get_conn = app.get_conn
        app.get_conn = fake_get_conn
        try:
            return app.metals_xau_live_persistent_chop_gate(len(closes))
        finally:
            app.get_conn = original_get_conn
            conn.close()

    def test_gate_blocks_oscillating_xau_path(self):
        result = self._gate_for_closes(
            [100.0 + (idx % 2) for idx in range(25)]
        )
        self.assertEqual(result["decision"], "BLOCK")
        self.assertEqual(result["efficiency_state_8h"], "CHOPPY")
        self.assertEqual(result["efficiency_state_12h"], "CHOPPY")
        self.assertEqual(result["efficiency_state_24h"], "CHOPPY")

    def test_gate_allows_clean_xau_trend(self):
        result = self._gate_for_closes(
            [100.0 + idx for idx in range(25)]
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertEqual(result["efficiency_state_8h"], "CLEAN_TREND")
        self.assertEqual(result["efficiency_state_12h"], "CLEAN_TREND")
        self.assertEqual(result["efficiency_state_24h"], "CLEAN_TREND")


if __name__ == "__main__":
    unittest.main()
