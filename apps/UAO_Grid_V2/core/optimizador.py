"""
optimizador.py — Calculadora de parámetros del grid. V2.

RESPONSABILIDAD ÚNICA: Dadas las métricas de mercado y los overrides de la IA,
calcular los parámetros óptimos del grid. Sin simulaciones. Sin descargas de datos.

Cambios respecto a V1:
  - Recibe MarketMetrics (tipado) en lugar de Dict[str, Any]
  - Recibe IAOverrides (tipado) en lugar de Dict[str, Any]
  - Recibe AppConfig para límites globales
  - Retorna GridParameters (dataclass) en lugar de dict
  - Modo (NEUTRAL/LONG/SHORT) se infiere de la tendencia detectada por el analizador
  - Añade riesgo_liquidacion_pct como campo informativo
  - Logging estructurado por paso
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from core.config import AppConfig
from core.models import GridMode, GridParameters, IAOverrides, MarketMetrics, TrendDirection

logger = logging.getLogger("UAO_Grid.Optimizador")

# Límites de seguridad absolutos (no sobreescribibles por la IA)
_MIN_LEVERAGE      = 2
_MAX_LEVERAGE_ABS  = 75    # Límite duro independiente del exchange
_MIN_GRIDS         = 4
_MAX_GRIDS         = 150
_MIN_ESPACIADO_PCT = 0.0012  # 0.12% — mínimo cubriendo comisiones estándar


class OptimizadorGrid:
    """
    Calcula los parámetros óptimos del grid dadas las métricas de mercado.

    No hace simulaciones (eso es el backtester).
    No descarga datos (eso es el provider/analizador).
    """

    def __init__(
        self,
        config: AppConfig,
        overrides: Optional[IAOverrides] = None,
        multiplicador_riesgo: float = 1.2,
    ) -> None:
        self.config               = config
        self.overrides            = overrides or IAOverrides.defaults()
        self.multiplicador_riesgo = multiplicador_riesgo

    def actualizar_overrides(self, overrides: IAOverrides) -> None:
        """Permite hot-reload de overrides sin reiniciar el optimizador."""
        self.overrides = overrides
        logger.info(
            "🤖 Overrides actualizados: step_pct=%s | density=%.2f | lev_factor=%.2f",
            overrides.grid_step_pct, overrides.grid_density_factor, overrides.leverage_factor,
        )

    def calcular(
        self,
        metrics: MarketMetrics,
        capital_total: float,
    ) -> GridParameters:
        """
        Calcula los parámetros de la malla para un símbolo.

        Args:
            metrics: Métricas de mercado del Analizador.
            capital_total: Capital disponible para este símbolo en USDT.

        Returns:
            GridParameters con la configuración completa o valido=False con razón.
        """
        ov = self.overrides

        # ── Filtros duros ──────────────────────────────────────────────────────
        checks = [
            ("score",            metrics.score,            ov.min_score),
            ("ops_promedio",     metrics.ops_promedio,     0.1),
            ("velas_utiles_pct", metrics.velas_utiles_pct, 20.0),
            ("consistencia",     metrics.consistencia,     ov.min_consistency),
            ("oscilacion",       metrics.oscilacion,       ov.min_oscillation),
        ]
        for campo, valor, minimo in checks:
            if valor < minimo:
                razon = f"{campo}={valor:.3f} < min={minimo:.3f}"
                logger.debug("❌ %s rechazado: %s", metrics.symbol, razon)
                return GridParameters(symbol=metrics.symbol, valido=False, razon_invalido=razon)

        # ── 1. Modo estratégico (inferido de tendencia) ───────────────────────
        modo = self._inferir_modo(metrics.tendencia)

        # ── 2. Espaciado ──────────────────────────────────────────────────────
        if ov.grid_step_pct is not None:
            # La IA fijó un espaciado exacto en %
            espaciado_pct = max(_MIN_ESPACIADO_PCT, min(float(ov.grid_step_pct) / 100.0, 0.05))
        else:
            espaciado_pct = max(metrics.grid_step_optimo, _MIN_ESPACIADO_PCT)

        # ── 3. Apalancamiento dinámico ────────────────────────────────────────
        apalancamiento = self._calcular_apalancamiento(metrics, espaciado_pct)

        # ── 4. Límites del grid ───────────────────────────────────────────────
        limite_sup, limite_inf = self._calcular_limites(metrics, modo)

        # ── 5. Número de líneas e inversión ──────────────────────────────────
        num_grids, capital_por_linea, tamaño_orden = self._calcular_densidad(
            metrics.precio, limite_sup, limite_inf, espaciado_pct, capital_total, apalancamiento
        )

        # ── 6. Riesgo de liquidación estimado ─────────────────────────────────
        riesgo_liq_pct = round(1.0 / (apalancamiento + 1e-9) * 100, 2)

        logger.info(
            "✅ %s → modo=%s | lev=%dx | grids=%d | espaciado=%.3f%% | capital/linea=%.2f USDT",
            metrics.symbol, modo.value, apalancamiento, num_grids,
            espaciado_pct * 100, capital_por_linea,
        )

        return GridParameters(
            symbol               = metrics.symbol,
            valido               = True,
            modo                 = modo,
            precio_actual        = round(metrics.precio, 6),
            apalancamiento       = apalancamiento,
            limite_superior      = round(limite_sup, 6),
            limite_inferior      = round(limite_inf, 6),
            num_grids            = num_grids,
            espaciado_pct        = round(espaciado_pct, 6),
            capital_por_linea    = round(capital_por_linea, 2),
            tamaño_orden_usdt    = round(tamaño_orden, 2),
            riesgo_liquidacion_pct = riesgo_liq_pct,
        )

    # ── Helpers privados ──────────────────────────────────────────────────────

    def _inferir_modo(self, tendencia: TrendDirection) -> GridMode:
        """Sugiere el modo del grid basado en la tendencia detectada."""
        if tendencia == TrendDirection.BULLISH:
            return GridMode.LONG
        if tendencia == TrendDirection.BEARISH:
            return GridMode.SHORT
        return GridMode.NEUTRAL

    def _calcular_apalancamiento(
        self, metrics: MarketMetrics, espaciado_pct: float
    ) -> int:
        """
        Apalancamiento dinámico basado en riesgo compuesto.

        Fórmula:
          riesgo_base = deriva_pct*0.4 + atr_pct*0.3
          penalidades = por consistencia y score bajos
          lev = floor(1 / (riesgo_base * penalidades) * leverage_factor)
        """
        ov = self.overrides
        deriva_pct = metrics.deriva_pct / 100.0
        atr_pct    = metrics.atr_pct

        riesgo_base           = (deriva_pct * 0.4) + (atr_pct * 0.3)
        penalidad_consistencia = 1 + ((1.0 - metrics.consistencia) * 0.2)
        penalidad_score       = 1 + ((100.0 / max(metrics.score, 1.0)) * 0.1)

        riesgo_total  = riesgo_base * penalidad_consistencia * penalidad_score * self.multiplicador_riesgo
        lev_calculado = math.floor((1.0 / (riesgo_total + 0.001)) * ov.leverage_factor)

        max_lev  = min(int(ov.max_leverage), _MAX_LEVERAGE_ABS)
        max_lev  = min(max_lev, self.config.capital.leverage)  # No superar el global
        lev_final = max(_MIN_LEVERAGE, min(lev_calculado, int(max_lev)))

        logger.debug(
            "%s riesgo_base=%.4f → lev_calculado=%d → lev_final=%d",
            "lev", riesgo_base, lev_calculado, lev_final,
        )
        return lev_final

    def _calcular_limites(
        self, metrics: MarketMetrics, modo: GridMode
    ) -> tuple[float, float]:
        """Calcula los límites superior e inferior del grid según el modo."""
        ov             = self.overrides
        precio         = metrics.precio
        deriva_frac    = (metrics.deriva_pct / 100.0)
        rango_v        = metrics.rango_vela_mediano
        mult           = self.multiplicador_riesgo

        if modo == GridMode.NEUTRAL:
            limite_sup = precio * (1 + (deriva_frac / 2) * mult)
            limite_inf = precio * (1 - (deriva_frac / 2) * mult)
        elif modo == GridMode.LONG:
            # En modo LONG: rango amplio abajo (para acumular), estrecho arriba (para TPs)
            limite_sup = precio * (1 + rango_v * 3)
            limite_inf = precio * (1 - deriva_frac * mult)
        elif modo == GridMode.SHORT:
            # En modo SHORT: rango amplio arriba (para acumular), estrecho abajo (para TPs)
            limite_sup = precio * (1 + deriva_frac * mult)
            limite_inf = precio * (1 - rango_v * 3)
        else:
            limite_sup = precio * 1.05
            limite_inf = precio * 0.95

        return limite_sup, limite_inf

    def _calcular_densidad(
        self,
        precio: float,
        limite_sup: float,
        limite_inf: float,
        espaciado_pct: float,
        capital_total: float,
        apalancamiento: int,
    ) -> tuple[int, float, float]:
        """
        Calcula el número de líneas y la inversión por línea.

        Returns:
            (num_grids, capital_por_linea, tamaño_orden_usdt)
        """
        ov = self.overrides

        rango_total_fiat = limite_sup - limite_inf
        tamano_grid_fiat = precio * espaciado_pct
        grids_matematicos = rango_total_fiat / (tamano_grid_fiat + 1e-9)

        num_grids = max(
            _MIN_GRIDS,
            min(math.floor(grids_matematicos * ov.grid_density_factor), _MAX_GRIDS),
        )

        # Garantizar margen mínimo por línea
        min_margin        = self.config.capital.min_margin_per_line
        max_grids_posibles = max(_MIN_GRIDS, math.floor(capital_total / (min_margin + 1e-9)))
        num_grids          = min(num_grids, max_grids_posibles)

        capital_por_linea = capital_total / (num_grids + 1e-9)
        tamaño_orden      = (capital_por_linea * apalancamiento) * ov.capital_factor

        return num_grids, capital_por_linea, tamaño_orden
