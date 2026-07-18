import math
from typing import Dict, Any

class OptimizadorGrid:
    def __init__(self, multiplicador_riesgo: float = 1.2, factor_espaciado: float = 0.8):
        """
        :param multiplicador_riesgo: Amplía el rango de protección (1.2 = 20% extra de margen).
        :param factor_espaciado: Qué porcentaje de la vela mediana usar entre cada grid (0.8 = 80%).
        """
        self.mult_riesgo = multiplicador_riesgo
        self.factor_espaciado = factor_espaciado

    def calcular_parametros(self, datos_analisis: Dict[str, Any], modo: str = "neutral") -> Dict[str, Any]:
        """
        Calcula los parámetros óptimos del grid según la dirección deseada.
        modo: 'neutral', 'long', 'short'
        """
        precio = datos_analisis["precio_actual"]
        # Soporta tanto el formato viejo como el nuevo del analizador
        deriva = datos_analisis.get("deriva_real_pct") or datos_analisis.get("deriva_total_pct", 0.02)
        rango_vela = datos_analisis.get("rango_pct_mediano") or datos_analisis.get("rango_pct_promedio", 0.002)

        # 1. Espaciado óptimo: Fracción de la volatilidad típica (mínimo 0.1% para evitar fees)
        espaciado_pct = max(rango_vela * self.factor_espaciado, 0.001)

        # 2. Calcular Límites según la estrategia
        if modo.lower() == "neutral":
            # Rango simétrico: Cubre la deriva histórica hacia ambos lados
            limite_sup = precio * (1 + (deriva / 2) * self.mult_riesgo)
            limite_inf = precio * (1 - (deriva / 2) * self.mult_riesgo)
            
        elif modo.lower() == "long":
            # Protege fuerte hacia abajo (deriva), límite superior corto (volatilidad local)
            limite_sup = precio * (1 + (rango_vela * 3))
            limite_inf = precio * (1 - (deriva) * self.mult_riesgo)
            
        elif modo.lower() == "short":
            # Protege fuerte hacia arriba (deriva), límite inferior corto (volatilidad local)
            limite_sup = precio * (1 + (deriva) * self.mult_riesgo)
            limite_inf = precio * (1 - (rango_vela * 3))
            
        else:
            raise ValueError("El modo debe ser 'neutral', 'long' o 'short'")

        # 3. Calcular cantidad de Grids (Aritmético)
        rango_precio = limite_sup - limite_inf
        tamano_grid_fiat = precio * espaciado_pct
        num_grids = math.floor(rango_precio / tamano_grid_fiat)

        # 4. Restricciones de Exchange (Ej: Binance permite entre 2 y 150 grids)
        num_grids = max(2, min(num_grids, 150))

        return {
            "symbol": datos_analisis.get("symbol", "UNKNOWN"),
            "modo": modo.upper(), # <--- Esto define la DIRECCIÓN (LONG/SHORT/NEUTRAL)
            "precio_actual": precio,
            "limite_superior": round(limite_sup, 5),
            "limite_inferior": round(limite_inf, 5),
            "num_grids": num_grids, # <--- Cantidad total de niveles
            "espaciado_pct": round(espaciado_pct, 6),
            "score_original": datos_analisis.get("score", 0.0)
        }
