import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.backtester import _backtest_grid_simbolo


class FakeExchange:
    markets = {
        "TEST/USDT:USDT": {
            "maker": 0.0002,
            "taker": 0.0005,
        }
    }

    def fetch_ohlcv(self, symbol, timeframe, limit=288):
        rows = []
        price = 100.0
        for i in range(limit):
            open_ = price
            high = open_ * 1.006
            low = open_ * 0.994
            close = open_ * (1.001 if i % 2 == 0 else 0.999)
            rows.append([i * 300000, open_, high, low, close, 1000])
            price = close
        return rows


class TestBacktesterAIOverrides(unittest.TestCase):
    def _analisis(self):
        return {
            "symbol": "TEST/USDT:USDT",
            "precio": 100.0,
            "ops_promedio": 1.0,
            "velas_utiles_pct": 75.0,
            "score": 82.0,
            "consistencia": 0.82,
            "oscilacion": 1.7,
            "atr_pct": 0.004,
            "deriva_pct": 4.0,
            "rango_vela_mediano": 0.003,
            "grid_step_optimo": 0.0025,
            "grid_quality": 0.88,
            "riesgo": 0.24,
            "densidad_sugerida": 1.15,
            "capital_factor": 1.08,
            "apalancamiento_factor": 1.05,
            "modo_preferido": "LONG",
        }

    def test_backtest_with_ai_overrides_marks_source_and_keeps_overrides(self):
        overrides = {
            "GRID_DENSITY_FACTOR": 1.1,
            "LEVERAGE_FACTOR": 0.95,
            "CAPITAL_FACTOR": 1.05,
            "GRID_STEP_FACTOR": 1.1,
        }

        result = _backtest_grid_simbolo(FakeExchange(), self._analisis(), 100.0, overrides=overrides)

        self.assertEqual(result["source"], "AI")
        self.assertEqual(result["ai_overrides"], overrides)
        self.assertIn("params_optimos", result)
        self.assertIn(result["modo"], {"NEUTRAL", "LONG", "SHORT"})

    def test_backtest_with_candidate_config_preserves_final_params(self):
        params = {
            "valido": True,
            "modo": "SHORT",
            "apalancamiento": 12,
            "num_grids": 8,
            "espaciado_pct": 0.003,
        }

        result = _backtest_grid_simbolo(
            FakeExchange(),
            self._analisis(),
            100.0,
            params_candidatos=params,
        )

        self.assertEqual(result["source"], "CONFIG")
        self.assertEqual(result["modo"], "SHORT")
        self.assertEqual(result["apalancamiento"], 12)
        self.assertEqual(result["num_grids"], 8)
        self.assertEqual(result["params_optimos"]["espaciado_pct"], 0.003)


if __name__ == "__main__":
    unittest.main()
