import math
import pandas as pd
import numpy as np
from typing import Dict, Any

class OptimizadorGrid:
    def __init__(self, multiplicador_riesgo: float = 1.2, max_leverage: int = 20, overrides: Dict[str, Any] = None):
        self.mult_riesgo = multiplicador_riesgo
        self.max_leverage = max_leverage
        self.overrides = overrides or {}

    def optimizar_symbol(self, symbol: str, df: pd.DataFrame, capital_total: float, analisis: Dict[str, Any], modo: str = "NEUTRAL") -> Dict[str, Any]:
        """
        Analiza las velas y devuelve la configuración completa de la malla y el riesgo.
        """
        if df is None or df.empty or len(df) < 15:
            return {"symbol": symbol, "valido": False}
            
        # Extraer filtros de la IA o defaults
        min_score = float(self.overrides.get("MIN_SCORE", 70))
        min_cons = float(self.overrides.get("MIN_CONSISTENCY", 0.0))
        min_osc = float(self.overrides.get("MIN_OSCILLATION", 0.0))
        
        # Extraer overrides absolutos y factores
        grid_step_pct_override = self.overrides.get("GRID_STEP_PCT")
        grid_density_factor = max(0.75, min(float(self.overrides.get("GRID_DENSITY_FACTOR", 1.0)), 1.25))
        leverage_factor = max(0.80, min(float(self.overrides.get("LEVERAGE_FACTOR", 1.0)), 1.15))
        capital_factor = max(0.80, min(float(self.overrides.get("CAPITAL_FACTOR", 1.0)), 1.20))
        
        # Filtros duros combinados con la IA
        min_ops = float(self.overrides.get("MIN_OPS", 0.1))
        min_velas = float(self.overrides.get("MIN_VELAS_UTILES", 20))
        
        if analisis.get("ops_promedio", 0) < min_ops or analisis.get("velas_utiles_pct", 0) < min_velas or \
           analisis.get("score", 0) < min_score or analisis.get("consistencia", 0) < min_cons or \
           analisis.get("oscilacion", 0) < min_osc:
            return {"symbol": symbol, "valido": False, "razon": "No cumple criterios mínimos del analizador + IA"}

        precio_actual = df["close"].iloc[-1] if df is not None and not df.empty else analisis.get("precio", 1.0)
        
        # 1. Cálculo de Volatilidad (Desde el analizador, sin recalcular)
        atr_pct = analisis.get("atr_pct", 0.003)
        deriva_max_pct = analisis.get("deriva_pct", 1.5) / 100.0  # analizador lo devuelve en pct (ej. 1.5 para 1.5%)
        rango_vela_mediano = analisis.get("rango_vela_mediano", 0.001)

        # 2. Apalancamiento Dinámico (Riesgo Compuesto)
        # Peso base de volatilidad
        riesgo_base = (deriva_max_pct * 0.4) + (atr_pct * 0.3)
        
        # Penalizadores de riesgo por falta de consistencia y score
        consistencia = analisis.get("consistencia", 0.5)
        score = max(analisis.get("score", 70), 1)
        
        penalidad_consistencia = 1 + ((1 - consistencia) * 0.2)
        penalidad_score = 1 + ((100 / score) * 0.1)
        
        riesgo_liquidacion = riesgo_base * penalidad_consistencia * penalidad_score * self.mult_riesgo
        
        apalancamiento_calculado = math.floor((1.0 / (riesgo_liquidacion + 0.001)) * leverage_factor)
        limite_apalancamiento = int(self.overrides.get("MAX_LEVERAGE", self.max_leverage))
        apalancamiento = max(2, min(apalancamiento_calculado, limite_apalancamiento))

        # 3. Espaciado y Límites
        if grid_step_pct_override is not None:
            # La IA decidió un espaciado exacto en porcentaje (ej 0.22 -> 0.0022)
            espaciado_pct = max(0.0015, min(float(grid_step_pct_override) / 100.0, 0.05))
        else:
            grid_step_optimo = analisis.get("grid_step_optimo", max(rango_vela_mediano * 0.8, 0.0015))
            espaciado_pct = max(grid_step_optimo, 0.0015) # Mínimo 0.15% por comisiones
        
        if modo.upper() == "NEUTRAL":
            limite_sup = precio_actual * (1 + (deriva_max_pct / 2) * self.mult_riesgo)
            limite_inf = precio_actual * (1 - (deriva_max_pct / 2) * self.mult_riesgo)
        elif modo.upper() == "LONG":
            limite_sup = precio_actual * (1 + (rango_vela_mediano * 3))
            limite_inf = precio_actual * (1 - deriva_max_pct * self.mult_riesgo)
        elif modo.upper() == "SHORT":
            limite_sup = precio_actual * (1 + deriva_max_pct * self.mult_riesgo)
            limite_inf = precio_actual * (1 - (rango_vela_mediano * 3))
        else:
            limite_sup = precio_actual * 1.05
            limite_inf = precio_actual * 0.95

        # 4. Densidad de Líneas e Inversión
        rango_total_fiat = limite_sup - limite_inf
        tamano_grid_fiat = precio_actual * espaciado_pct
        grids_matematicos = rango_total_fiat / (tamano_grid_fiat + 1e-9)
        num_grids_total = max(4, min(math.floor(grids_matematicos * grid_density_factor), 150))
        
        capital_por_linea = capital_total / num_grids_total
        tamaño_orden_usdt = (capital_por_linea * apalancamiento) * capital_factor

        return {
            "symbol": symbol,
            "valido": True,
            "modo": modo.upper(),
            "precio_actual": round(precio_actual, 5),
            "apalancamiento": apalancamiento,
            "limite_superior": round(limite_sup, 5),
            "limite_inferior": round(limite_inf, 5),
            "num_grids": num_grids_total,
            "espaciado_pct": round(espaciado_pct, 6),
            "capital_por_linea": round(capital_por_linea, 2),
            "tamaño_orden_usdt": round(tamaño_orden_usdt, 2)
        }
