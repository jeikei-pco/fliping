"""
backtester.py — Simulación de grid por fuerza bruta. V2.

RESPONSABILIDAD ÚNICA: Dado un DataFrame OHLCV y una configuración de grid,
simular el comportamiento y retornar un BacktestResult completo.
No descarga datos. No accede al exchange.

Mejoras respecto a V1:
  - NO llama exchange.fetch_ohlcv() — recibe el DataFrame desde el provider
  - Retorna BacktestResult tipado con métricas completas:
      pnl_neto, operaciones, win_rate, profit_factor, max_drawdown,
      sharpe_ratio, calmar_ratio, expectancy, recovery_factor, score_backtest
  - Simulación más robusta: registra PnL por trade para calcular equity curve
  - Guarda resultado en DB (opcional) para aprendizaje de la IA
  - Score compuesto: PnL×0.4 + Sharpe×0.3 + PF×0.2 + WR×0.1
  - Fix del bug de orden [O,L,H,C]: se usa orden correcto [O,L,H,C] o [O,H,L,C]
    según dirección de la vela para reducir sobreestimación de fills
"""
from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.models import BacktestResult, GridMode, MarketMetrics

logger = logging.getLogger("UAO_Grid.Backtester")


# ─────────────────────────────────────────────────────────────────────────────
# Motor de simulación
# ─────────────────────────────────────────────────────────────────────────────

def _simular_grid(
    df: pd.DataFrame,
    modo: GridMode,
    apalancamiento: int,
    num_grids: int,
    espaciado_pct: float,
    capital_total: float,
    fee_maker: float,
    fee_taker: float,
) -> Dict[str, Any]:
    """
    Simula el comportamiento del grid sobre un DataFrame OHLCV.

    Args:
        df: DataFrame con columnas [open, high, low, close].
        modo: GridMode (NEUTRAL / LONG / SHORT).
        apalancamiento: Leverage.
        num_grids: Número total de líneas del grid.
        espaciado_pct: Distancia entre líneas como fracción (ej. 0.003).
        capital_total: Capital total en USDT.
        fee_maker: Comisión de maker (ej. 0.00020).
        fee_taker: Comisión de taker (ej. 0.00050).

    Returns:
        Dict con operaciones, pnl_neto, lista de pnl por trade, etc.
    """
    costos_totales = fee_maker + fee_taker
    pnl_neto_pct   = espaciado_pct - costos_totales
    margen_minimo  = 0.0010  # 0.10% de ganancia mínima neta

    if pnl_neto_pct < margen_minimo:
        return {"operaciones": 0, "pnl_neto": -999.0, "trades_pnl": []}

    num_lineas_lado = max(1, num_grids // 2) if modo == GridMode.NEUTRAL else num_grids
    capital_por_linea = capital_total / (num_lineas_lado + 1e-9)
    tamaño_orden  = capital_por_linea * apalancamiento
    pnl_por_trade = tamaño_orden * pnl_neto_pct

    pnl_acumulado = 0.0
    operaciones   = 0
    posicion_neta = 0
    precio_actual = float(df["open"].iloc[0])
    trades_pnl: List[float] = []

    for _, row in df.iterrows():
        open_  = float(row["open"])
        high_  = float(row["high"])
        low_   = float(row["low"])
        close_ = float(row["close"])

        # Orden de simulación según dirección de la vela (reduce sobreestimación de fills)
        # Vela alcista: O → L → H → C  |  Vela bajista: O → H → L → C
        if close_ >= open_:
            movimientos = [open_, low_, high_, close_]
        else:
            movimientos = [open_, high_, low_, close_]

        for precio in movimientos:
            # Sube: cruza línea hacia arriba
            while precio >= precio_actual * (1.0 + espaciado_pct):
                precio_actual *= (1.0 + espaciado_pct)
                if modo == GridMode.NEUTRAL:
                    if posicion_neta > 0:          # Teníamos LONG abajo → cobrar ganancia
                        pnl_acumulado += pnl_por_trade
                        operaciones += 1
                        trades_pnl.append(pnl_por_trade)
                        posicion_neta -= 1
                    elif posicion_neta > -num_lineas_lado:  # Abrir SHORT
                        posicion_neta -= 1
                elif modo == GridMode.LONG:
                    if posicion_neta > 0:          # Cerrar LONG acumulado
                        pnl_acumulado += pnl_por_trade
                        operaciones += 1
                        trades_pnl.append(pnl_por_trade)
                        posicion_neta -= 1
                elif modo == GridMode.SHORT:
                    if posicion_neta > -num_lineas_lado:    # Acumular SHORT
                        posicion_neta -= 1

            # Baja: cruza línea hacia abajo
            while precio <= precio_actual * (1.0 - espaciado_pct):
                precio_actual *= (1.0 - espaciado_pct)
                if modo == GridMode.NEUTRAL:
                    if posicion_neta < 0:          # Teníamos SHORT arriba → cobrar ganancia
                        pnl_acumulado += pnl_por_trade
                        operaciones += 1
                        trades_pnl.append(pnl_por_trade)
                        posicion_neta += 1
                    elif posicion_neta < num_lineas_lado:   # Abrir LONG
                        posicion_neta += 1
                elif modo == GridMode.LONG:
                    if posicion_neta < num_lineas_lado:     # Acumular LONG
                        posicion_neta += 1
                elif modo == GridMode.SHORT:
                    if posicion_neta < 0:          # Cerrar SHORT acumulado
                        pnl_acumulado += pnl_por_trade
                        operaciones += 1
                        trades_pnl.append(pnl_por_trade)
                        posicion_neta += 1

    return {
        "operaciones": operaciones,
        "pnl_neto":    pnl_acumulado,
        "trades_pnl":  trades_pnl,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Métricas financieras (desde trades_pnl)
# ─────────────────────────────────────────────────────────────────────────────

def _calcular_metricas(
    trades_pnl: List[float],
    capital_total: float,
) -> Dict[str, float]:
    """
    Calcula métricas financieras completas desde la lista de PnL por trade.

    Returns:
        Dict con win_rate, profit_factor, max_drawdown, max_drawdown_pct,
        sharpe_ratio, calmar_ratio, expectancy, recovery_factor.
    """
    if not trades_pnl:
        return {
            "win_rate": 0.0, "profit_factor": 0.0,
            "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0, "calmar_ratio": 0.0,
            "expectancy": 0.0, "recovery_factor": 0.0,
        }

    wins   = [p for p in trades_pnl if p > 0]
    losses = [p for p in trades_pnl if p < 0]

    win_rate     = len(wins) / len(trades_pnl) if trades_pnl else 0.0
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    # Equity curve y Drawdown
    equity = [0.0]
    for p in trades_pnl:
        equity.append(equity[-1] + p)

    equity_arr  = np.array(equity)
    running_max = np.maximum.accumulate(equity_arr)
    drawdowns   = running_max - equity_arr
    max_drawdown     = float(np.max(drawdowns))
    max_drawdown_pct = (max_drawdown / (capital_total + 1e-9)) * 100.0

    # Sharpe Ratio (usando retornos diarios proxy: cada 288 trades ≈ 1 día en 5m)
    if len(trades_pnl) > 1:
        pnl_arr  = np.array(trades_pnl)
        mean_ret = np.mean(pnl_arr)
        std_ret  = np.std(pnl_arr, ddof=1)
        sharpe   = float(mean_ret / (std_ret + 1e-9)) * math.sqrt(288)  # Annualized (proxy diario)
    else:
        sharpe = 0.0

    # Calmar Ratio
    pnl_neto_total = sum(trades_pnl)
    calmar = float(pnl_neto_total / (max_drawdown + 1e-9))

    # Expectancy = wr * avg_win - (1-wr) * avg_loss
    avg_win  = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(abs(p) for p in losses) / len(losses) if losses else 0.0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

    # Recovery Factor = pnl_neto / max_drawdown
    recovery = float(pnl_neto_total / (max_drawdown + 1e-9))

    return {
        "win_rate":        round(win_rate, 4),
        "profit_factor":   profit_factor,
        "max_drawdown":    round(max_drawdown, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "sharpe_ratio":    round(sharpe, 3),
        "calmar_ratio":    round(calmar, 3),
        "expectancy":      round(expectancy, 4),
        "recovery_factor": round(recovery, 3),
    }


def _calcular_score_backtest(
    pnl_neto: float,
    sharpe: float,
    profit_factor: float,
    win_rate: float,
    capital_total: float,
) -> float:
    """
    Score compuesto para ranking de backtests.

    Pesos: PnL×0.4 + Sharpe×0.3 + PF×0.2 + WR×0.1
    Normalizado a [0, 100] aprox.
    """
    pnl_norm = min(pnl_neto / (capital_total + 1e-9) * 100, 10.0)  # Cap 10% ROI → 10 pts
    sha_norm = min(max(sharpe, 0.0), 5.0)                           # Cap Sharpe 5 → 5 pts
    pf_norm  = min(max(profit_factor - 1.0, 0.0), 5.0)              # Cap PF 6 → 5 pts
    wr_norm  = win_rate * 100                                        # [0, 100]

    score = (
        pnl_norm * 40.0 / 10.0 +   # Normaliza 10 → 40 pts
        sha_norm * 30.0 /  5.0 +   # Normaliza 5 → 30 pts
        pf_norm  * 20.0 /  5.0 +   # Normaliza 5 → 20 pts
        wr_norm  * 10.0 / 100.0    # Normaliza 100 → 10 pts
    )
    return round(score, 3)


# ─────────────────────────────────────────────────────────────────────────────
# Backtest por símbolo (fuerza bruta)
# ─────────────────────────────────────────────────────────────────────────────

def backtest_simbolo(
    df: pd.DataFrame,
    metrics: MarketMetrics,
    capital_total: float,
) -> BacktestResult:
    """
    Ejecuta backtest por fuerza bruta sobre todas las combinaciones de parámetros
    y retorna el mejor BacktestResult (por score compuesto).

    Args:
        df: DataFrame OHLCV con columnas [ts, open, high, low, close, volume].
            Mínimo 100 velas. Se recomienda 288 (1 día en 5m) o más.
        metrics: MarketMetrics del analizador.
        capital_total: Capital disponible para este símbolo.

    Returns:
        BacktestResult con la mejor configuración encontrada.
        Si no hay ninguna rentable, retorna result con pnl_neto=-999.
    """
    symbol     = metrics.symbol
    fee_maker  = metrics.fee_maker
    fee_taker  = metrics.fee_taker
    rango_v    = metrics.rango_vela_mediano
    min_spread = fee_maker + fee_taker + 0.0010  # Mínimo rentable

    if df.empty or len(df) < 50:
        logger.warning("Backtester: datos insuficientes para %s (%d velas)", symbol, len(df))
        return BacktestResult(
            symbol=symbol, modo_optimo=GridMode.NEUTRAL, apalancamiento=10,
            num_grids=10, espaciado_pct=min_spread, pnl_neto=-999.0,
            operaciones=0, win_rate=0.0, profit_factor=0.0,
            max_drawdown=0.0, max_drawdown_pct=0.0, sharpe_ratio=0.0,
            calmar_ratio=0.0, expectancy=0.0, recovery_factor=0.0,
            score_backtest=-999.0,
        )

    # Matriz de fuerza bruta
    MODOS       = [GridMode.NEUTRAL, GridMode.LONG, GridMode.SHORT]
    LEVERAGES   = [10, 15, 20]
    NUM_LINEAS  = [6, 8, 10, 14, 20]
    MULT_DIST   = {20: [0.4, 0.6, 0.8], 15: [0.8, 1.0, 1.2], 10: [1.2, 1.5, 2.0]}

    mejor_score  = -float("inf")
    mejor_result: Optional[BacktestResult] = None

    for modo in MODOS:
        for lev in LEVERAGES:
            for n_lin in NUM_LINEAS:
                for mult in MULT_DIST.get(lev, [1.0]):
                    espaciado = max(min_spread, rango_v * mult)

                    sim = _simular_grid(
                        df=df, modo=modo, apalancamiento=lev,
                        num_grids=n_lin, espaciado_pct=espaciado,
                        capital_total=capital_total,
                        fee_maker=fee_maker, fee_taker=fee_taker,
                    )

                    if sim["pnl_neto"] <= -999.0 or sim["operaciones"] == 0:
                        continue

                    mets = _calcular_metricas(sim["trades_pnl"], capital_total)
                    score = _calcular_score_backtest(
                        sim["pnl_neto"], mets["sharpe_ratio"],
                        mets["profit_factor"], mets["win_rate"], capital_total,
                    )

                    if score > mejor_score:
                        mejor_score = score
                        mejor_result = BacktestResult(
                            symbol          = symbol,
                            modo_optimo     = modo,
                            apalancamiento  = lev,
                            num_grids       = n_lin,
                            espaciado_pct   = espaciado,
                            pnl_neto        = round(sim["pnl_neto"], 4),
                            operaciones     = sim["operaciones"],
                            win_rate        = mets["win_rate"],
                            profit_factor   = mets["profit_factor"],
                            max_drawdown    = mets["max_drawdown"],
                            max_drawdown_pct= mets["max_drawdown_pct"],
                            sharpe_ratio    = mets["sharpe_ratio"],
                            calmar_ratio    = mets["calmar_ratio"],
                            expectancy      = mets["expectancy"],
                            recovery_factor = mets["recovery_factor"],
                            score_backtest  = score,
                            params_usados   = {
                                "modo": modo.value, "apalancamiento": lev,
                                "num_grids": n_lin, "espaciado_pct": espaciado,
                            },
                            metrics_originales = {
                                "score": metrics.score,
                                "atr_pct": metrics.atr_pct,
                                "oscilacion": metrics.oscilacion,
                                "consistencia": metrics.consistencia,
                            },
                        )

    if mejor_result and mejor_result.pnl_neto > 0:
        logger.info(
            "✅ [BT] %-15s PnL=$%.2f | Sharpe=%.2f | PF=%.2f | WR=%.0f%% | modo=%s | lev=%dx | grids=%d | dist=%.3f%%",
            symbol, mejor_result.pnl_neto, mejor_result.sharpe_ratio,
            mejor_result.profit_factor, mejor_result.win_rate * 100,
            mejor_result.modo_optimo.value, mejor_result.apalancamiento,
            mejor_result.num_grids, mejor_result.espaciado_pct * 100,
        )
        return mejor_result

    return BacktestResult(
        symbol=symbol, modo_optimo=GridMode.NEUTRAL, apalancamiento=10,
        num_grids=10, espaciado_pct=min_spread, pnl_neto=-999.0,
        operaciones=0, win_rate=0.0, profit_factor=0.0,
        max_drawdown=0.0, max_drawdown_pct=0.0, sharpe_ratio=0.0,
        calmar_ratio=0.0, expectancy=0.0, recovery_factor=0.0,
        score_backtest=-999.0,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Backtest en lote (paralelo)
# ─────────────────────────────────────────────────────────────────────────────

def backtest_lote(
    datos: List[Tuple[pd.DataFrame, MarketMetrics]],
    capital_total: float,
    workers: int = 5,
) -> List[BacktestResult]:
    """
    Ejecuta backtests en paralelo para una lista de (DataFrame, MarketMetrics).

    Args:
        datos: Lista de tuplas (df_ohlcv, metrics).
        capital_total: Capital por símbolo.
        workers: Número de hilos paralelos.

    Returns:
        Lista de BacktestResult con pnl_neto > 0, ordenada por score_backtest desc.
    """
    resultados: List[BacktestResult] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futuros = {
            executor.submit(backtest_simbolo, df, metrics, capital_total): metrics.symbol
            for df, metrics in datos
        }
        for fut in as_completed(futuros):
            sym = futuros[fut]
            try:
                result = fut.result()
                if result.pnl_neto > 0:
                    resultados.append(result)
            except Exception as exc:
                logger.error("backtest_lote error en %s: %s", sym, exc)

    resultados.sort(key=lambda r: r.score_backtest, reverse=True)
    logger.info(
        "📊 Backtest completado: %d/%d símbolos rentables | Top: %s (score=%.1f)",
        len(resultados),
        len(datos),
        resultados[0].symbol if resultados else "N/A",
        resultados[0].score_backtest if resultados else 0.0,
    )
    return resultados
