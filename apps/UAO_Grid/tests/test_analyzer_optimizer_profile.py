import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.analizador import _calcular_perfil_operativo
from core.optimizador import OptimizadorGrid


class TestAnalyzerOptimizerProfile(unittest.TestCase):
    def _df(self):
        return pd.DataFrame(
            {
                "close": [100.0, 101.0, 100.5, 102.0, 101.5, 103.0],
                "open": [100.0, 100.5, 101.0, 101.0, 102.0, 102.5],
                "high": [101.0, 102.0, 101.5, 103.0, 102.5, 104.0],
                "low": [99.5, 100.0, 100.0, 100.5, 101.0, 102.0],
            }
        )

    def test_analyzer_profile_contains_expected_controls(self):
        perfil = _calcular_perfil_operativo(
            self._df(),
            atr_pct=0.006,
            deriva=0.045,
            consistencia=0.82,
            simetria=0.9,
            oscilacion=1.8,
            pct_util=0.72,
            ops=1.4,
            zigzag_score=0.86,
            recorrido_real_mediano=0.004,
            rango_vela_mediano=0.0035,
            grid_step_optimo=0.0028,
        )

        for key in {
            "riesgo_volatilidad",
            "indice_tendencia",
            "indice_reversion",
            "eficiencia_grid",
            "grid_quality",
            "riesgo",
            "densidad_sugerida",
            "capital_factor",
            "apalancamiento_factor",
            "modo_preferido",
        }:
            self.assertIn(key, perfil)

        self.assertGreaterEqual(perfil["grid_quality"], 0.0)
        self.assertLessEqual(perfil["grid_quality"], 1.0)
        self.assertIn(perfil["modo_preferido"], {"NEUTRAL", "LONG", "SHORT"})

    def test_optimizer_uses_analyzer_profile_factors(self):
        df = pd.DataFrame({"close": [100.0 + i * 0.1 for i in range(30)]})
        analisis = {
            "precio": 103.0,
            "ops_promedio": 1.0,
            "velas_utiles_pct": 70.0,
            "score": 80.0,
            "consistencia": 0.8,
            "oscilacion": 1.6,
            "atr_pct": 0.004,
            "deriva_pct": 4.0,
            "rango_vela_mediano": 0.003,
            "grid_step_optimo": 0.0025,
            "grid_quality": 0.9,
            "riesgo": 0.2,
            "densidad_sugerida": 1.2,
            "capital_factor": 1.1,
            "apalancamiento_factor": 1.1,
            "modo_preferido": "LONG",
        }

        opt = OptimizadorGrid(max_leverage=20, overrides={"GRID_DENSITY_FACTOR": 1.1})
        params = opt.optimizar_symbol("TEST/USDT:USDT", df, 100.0, analisis)

        self.assertTrue(params["valido"])
        self.assertEqual(params["modo"], "LONG")
        self.assertGreater(params["apalancamiento"], 2)
        self.assertGreater(params["densidad_factor_final"], 1.0)
        self.assertGreater(params["capital_factor_final"], 1.0)


if __name__ == "__main__":
    unittest.main()
