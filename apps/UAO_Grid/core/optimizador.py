import math
import pandas as pd
import numpy as np
from typing import Dict, Any


def _normalizar_analisis(analisis: Dict[str, Any]) -> Dict[str, Any]:
    """
    Acepta el contrato nuevo del analizador ({symbol, analysis_profile}) y el
    formato plano anterior, devolviendo un diccionario plano para los calculos.
    """
    if not analisis:
        return {}

    profile = analisis.get("analysis_profile") or analisis.get("analysis")
    if not isinstance(profile, dict):
        return analisis

    market_data = profile.get("market_data", {})
    volatility = profile.get("volatility", {})
    trend = profile.get("trend", {})
    grid = profile.get("grid", {})
    risk = profile.get("risk", {})
    capital = profile.get("capital", {})
    execution = profile.get("execution", {})
    metadata = profile.get("metadata", {})

    normalized = dict(analisis)
    normalized.update({
        "symbol": profile.get("symbol", analisis.get("symbol")),
        "precio": market_data.get("precio", analisis.get("precio")),
        "ops_promedio": grid.get("ops_promedio", analisis.get("ops_promedio", 0)),
        "velas_utiles_pct": grid.get("velas_utiles_pct", analisis.get("velas_utiles_pct", 0)),
        "score": metadata.get("score", analisis.get("score", 0)),
        "consistencia": volatility.get("consistencia", analisis.get("consistencia", 0)),
        "oscilacion": grid.get("oscilacion", analisis.get("oscilacion", 0)),
        "atr_pct": market_data.get("atr_pct", volatility.get("atr_pct", analisis.get("atr_pct", 0.003))),
        "deriva_pct": market_data.get("deriva_pct", trend.get("deriva_pct", analisis.get("deriva_pct", 1.5))),
        "rango_vela_mediano": market_data.get(
            "rango_vela_mediano",
            volatility.get("rango_vela_mediano", analisis.get("rango_vela_mediano", 0.001)),
        ),
        "grid_step_optimo": grid.get("grid_step_optimo", analisis.get("grid_step_optimo")),
        "grid_quality": grid.get("grid_quality", analisis.get("grid_quality", analisis.get("zigzag_score", 0.5))),
        "riesgo": risk.get("riesgo", analisis.get("riesgo", 0.5)),
        "densidad_sugerida": grid.get("densidad_sugerida", analisis.get("densidad_sugerida", 1.0)),
        "capital_factor": capital.get("capital_factor", analisis.get("capital_factor", 1.0)),
        "apalancamiento_factor": capital.get("apalancamiento_factor", analisis.get("apalancamiento_factor", 1.0)),
        "modo_preferido": trend.get("modo_preferido", analisis.get("modo_preferido", "NEUTRAL")),
        "fee_round_trip_pct": execution.get("comision_rt", analisis.get("fee_round_trip_pct", 0.0012)),
        "min_profit_pct": execution.get("ganancia_min", analisis.get("min_profit_pct", 0.0005)),
        "min_grid_step": execution.get("min_grid_step", analisis.get("min_grid_step")),
    })
    return normalized


class OptimizadorGrid:
    def __init__(self, multiplicador_riesgo: float = 1.2, max_leverage: int = 25, overrides: Dict[str, Any] = None):
        self.mult_riesgo = multiplicador_riesgo
        self.max_leverage = max_leverage
        # Si no se pasan overrides específicos del símbolo, usa un diccionario vacío
        self.overrides = overrides or {}

    def optimizar_symbol(self, symbol: str, df: pd.DataFrame, capital_total: float, analisis: Dict[str, Any], modo: str = "NEUTRAL") -> Dict[str, Any]:
        """
        Transforma el perfil del analizador en la configuracion final de la malla.
        """
        analisis = _normalizar_analisis(analisis)

        if df is None or df.empty or len(df) < 15:
            return {"symbol": symbol, "valido": False}

        # Extraer filtros de la IA o defaults
        min_score = float(self.overrides.get("MIN_SCORE", 30))   # ↓ el filtro real es el PnL del backtest

        min_cons = float(self.overrides.get("MIN_CONSISTENCY", 0.0))
        min_osc = float(self.overrides.get("MIN_OSCILLATION", 0.0))
        min_grid_quality = float(self.overrides.get("MIN_GRID_QUALITY", 0.0))
        
        # Factores sugeridos por el analizador. Los defaults preservan compatibilidad
        # con perfiles antiguos que aun no tengan las claves nuevas.
        grid_quality = max(0.0, min(float(analisis.get("grid_quality", analisis.get("zigzag_score", 0.5))), 1.0))
        riesgo = max(0.0, min(float(analisis.get("riesgo", 0.5)), 1.0))
        densidad_analisis = max(0.75, min(float(analisis.get("densidad_sugerida", 1.0)), 1.25))
        capital_analisis = max(0.70, min(float(analisis.get("capital_factor", 1.0)), 1.20))
        leverage_analisis = max(0.75, min(float(analisis.get("apalancamiento_factor", 1.0)), 1.15))
        modo_preferido = str(analisis.get("modo_preferido", "NEUTRAL")).upper()

        # Extraer overrides de IA como multiplicadores finos.
        grid_step_pct_override = self.overrides.get("GRID_STEP_PCT")
        grid_step_factor = max(0.80, min(float(self.overrides.get("GRID_STEP_FACTOR", 1.0)), 1.20))
        grid_density_factor = max(0.75, min(float(self.overrides.get("GRID_DENSITY_FACTOR", 1.0)), 1.25))
        leverage_factor = max(0.80, min(float(self.overrides.get("LEVERAGE_FACTOR", 1.0)), 1.15))
        capital_factor = max(0.70, min(float(self.overrides.get("CAPITAL_FACTOR", 1.0)), 1.30))

        densidad_final = max(0.60, min(densidad_analisis * grid_density_factor, 1.45))
        capital_factor_final = max(0.50, min(capital_analisis * capital_factor, 1.50))
        leverage_factor_final = max(0.50, min(leverage_analisis * leverage_factor, 1.30))
        
        # Filtros duros — verificar uno a uno para diagnóstico exacto
        min_ops   = float(self.overrides.get("MIN_OPS", 0.1))
        min_velas = float(self.overrides.get("MIN_VELAS_UTILES", 20))
        checks = [
            ("ops_promedio",    analisis.get("ops_promedio",    0),   min_ops),
            ("velas_utiles_pct", analisis.get("velas_utiles_pct", 0),  min_velas),
            ("score",           analisis.get("score",           0),   min_score),
            ("consistencia",    analisis.get("consistencia",    0),   min_cons),
            ("oscilacion",      analisis.get("oscilacion",      0),   min_osc),
            ("grid_quality",     grid_quality,                         min_grid_quality),
        ]
        for campo, valor, minimo in checks:
            if valor < minimo:
                return {
                    "symbol": symbol, "valido": False,
                    "razon": f"{campo}={valor:.3f} < min={minimo:.3f}"
                }

        precio_actual = df["close"].iloc[-1] if df is not None and not df.empty else analisis.get("precio", 1.0)
        
        # 1. Cálculo de Volatilidad (Desde el analizador, sin recalcular)
        atr_pct = analisis.get("atr_pct", 0.003)
        deriva_max_pct = analisis.get("deriva_pct", 1.5) / 100.0  # analizador lo devuelve en pct (ej. 1.5 para 1.5%)
        rango_vela_mediano = analisis.get("rango_vela_mediano", 0.001)
        fee_round_trip_pct = float(analisis.get("fee_round_trip_pct", 0.0012) or 0.0012)
        min_profit_pct = float(analisis.get("min_profit_pct", 0.0005) or 0.0005)
        min_grid_step = float(analisis.get("min_grid_step", fee_round_trip_pct + min_profit_pct) or (fee_round_trip_pct + min_profit_pct))

        # 2. Apalancamiento dinamico desde el perfil del simbolo.
        limite_apalancamiento = int(self.overrides.get("MAX_LEVERAGE", self.max_leverage))
        base_leverage_ratio = 0.35 + (grid_quality * 0.50) + ((1.0 - riesgo) * 0.15)
        apalancamiento_calculado = math.floor(limite_apalancamiento * base_leverage_ratio * leverage_factor_final)
        apalancamiento = max(2, min(apalancamiento_calculado, limite_apalancamiento))

        # 3. Espaciado y Límites
        if grid_step_pct_override is not None:
            # La IA decidió un espaciado exacto en porcentaje (ej 0.22 -> 0.0022)
            espaciado_pct = max(min_grid_step, min(float(grid_step_pct_override) / 100.0, 0.05))
        else:
            grid_step_optimo = analisis.get("grid_step_optimo", max(rango_vela_mediano * 0.8, 0.0012))
            espaciado_pct = max(grid_step_optimo * grid_step_factor, min_grid_step)
        
        modo_final = str(modo or modo_preferido).upper()
        if modo_final == "NEUTRAL" and modo_preferido in {"LONG", "SHORT"}:
            modo_final = modo_preferido

        rango_operativo = max(deriva_max_pct * self.mult_riesgo, atr_pct * 6, espaciado_pct * 8)

        if modo_final == "NEUTRAL":
            limite_sup = precio_actual * (1 + rango_operativo / 2)
            limite_inf = precio_actual * (1 - rango_operativo / 2)
        elif modo_final == "LONG":
            limite_sup = precio_actual * (1 + max(rango_vela_mediano * 3, espaciado_pct * 4))
            limite_inf = precio_actual * (1 - rango_operativo)
        elif modo_final == "SHORT":
            limite_sup = precio_actual * (1 + rango_operativo)
            limite_inf = precio_actual * (1 - max(rango_vela_mediano * 3, espaciado_pct * 4))
        else:
            limite_sup = precio_actual * 1.05
            limite_inf = precio_actual * 0.95
            modo_final = "NEUTRAL"

        # 4. Densidad de Líneas e Inversión
        rango_total_fiat = limite_sup - limite_inf
        tamano_grid_fiat = precio_actual * espaciado_pct
        grids_matematicos = rango_total_fiat / (tamano_grid_fiat + 1e-9)
        num_grids_total = max(4, min(math.floor(grids_matematicos * densidad_final), 150))
        
        # 🎯 Ajuste crucial: Garantizar margen mínimo por línea (ej. 5 USDT)
        min_margin = float(self.overrides.get("MIN_MARGIN_PER_LINE", 5.0))
        max_grids_posibles = max(4, math.floor(capital_total / min_margin))
        num_grids_total = min(num_grids_total, max_grids_posibles)
        
        capital_por_linea = capital_total / num_grids_total
        tamaño_orden_usdt = (capital_por_linea * apalancamiento) * capital_factor_final

        return {
            "symbol": symbol,
            "valido": True,
            "modo": modo_final,
            "precio_actual": round(precio_actual, 5),
            "apalancamiento": apalancamiento,
            "limite_superior": round(limite_sup, 5),
            "limite_inferior": round(limite_inf, 5),
            "num_grids": num_grids_total,
            "espaciado_pct": round(espaciado_pct, 6),
            "capital_por_linea": round(capital_por_linea, 2),
            "tamaño_orden_usdt": round(tamaño_orden_usdt, 2),
            "grid_quality": round(grid_quality, 4),
            "riesgo": round(riesgo, 4),
            "densidad_factor_final": round(densidad_final, 4),
            "capital_factor_final": round(capital_factor_final, 4),
            "apalancamiento_factor_final": round(leverage_factor_final, 4),
            "fee_round_trip_pct": round(fee_round_trip_pct, 8),
            "min_profit_pct": round(min_profit_pct, 8),
        }
