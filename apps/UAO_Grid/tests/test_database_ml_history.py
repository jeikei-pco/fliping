import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.database import Database


class TestDatabaseMLHistory(unittest.TestCase):
    def _db(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.remove(tmp.name))
        return Database(tmp.name)

    def test_create_ml_session_persists_final_params_and_backtest(self):
        db = self._db()
        analyzer = {
            "grid_quality": 0.91,
            "riesgo": 0.22,
            "modo_preferido": "LONG",
        }
        math_params = {"espaciado_pct": 0.0025, "num_grids": 12}
        ai_factors = {"GRID_DENSITY_FACTOR": 1.1}
        final_params = {"modo": "LONG", "apalancamiento": 15, "num_grids": 14}
        backtest = {"pnl_neto": 3.4, "roi_pct": 3.4, "operaciones": 18}

        db.create_ml_session(
            "session-1",
            "TEST/USDT:USDT",
            analyzer,
            math_params,
            ai_factors,
            final_params=final_params,
            backtest_metrics=backtest,
            setup_source="AI",
        )

        row = db.get_latest_ml_session("TEST/USDT:USDT")

        self.assertEqual(row["session_id"], "session-1")
        self.assertEqual(row["setup_source"], "AI")
        self.assertEqual(row["grid_quality"], 0.91)
        self.assertEqual(row["riesgo"], 0.22)
        self.assertEqual(row["modo_preferido"], "LONG")
        self.assertEqual(row["final_params"], final_params)
        self.assertEqual(row["backtest_metrics"], backtest)
        self.assertEqual(row["ai_factors"], ai_factors)

    def test_update_latest_ml_session_backtest(self):
        db = self._db()
        db.create_ml_session(
            "session-2",
            "TEST/USDT:USDT",
            {"grid_quality": 0.7},
            {"espaciado_pct": 0.003},
            {},
        )

        updated = db.update_ml_session_backtest(
            "TEST/USDT:USDT",
            {"modo": "NEUTRAL", "apalancamiento": 10},
            {"pnl_neto": 1.25, "profit_factor": 99.0},
            setup_source="BRUTE_FORCE",
        )
        row = db.get_latest_ml_session("TEST/USDT:USDT")

        self.assertTrue(updated)
        self.assertEqual(row["setup_source"], "BRUTE_FORCE")
        self.assertEqual(row["final_params"]["apalancamiento"], 10)
        self.assertEqual(row["backtest_metrics"]["pnl_neto"], 1.25)

    def test_symbol_overrides_are_scoped_by_symbol(self):
        db = self._db()
        db.update_symbol_config_overrides("AAA/USDT:USDT", {"GRID_STEP_FACTOR": 1.1})
        db.update_symbol_config_overrides("BBB/USDT:USDT", {"GRID_STEP_FACTOR": 0.9})

        self.assertEqual(db.get_symbol_config_overrides("AAA/USDT:USDT"), {"GRID_STEP_FACTOR": "1.1"})
        self.assertEqual(db.get_symbol_config_overrides("BBB/USDT:USDT"), {"GRID_STEP_FACTOR": "0.9"})


if __name__ == "__main__":
    unittest.main()
