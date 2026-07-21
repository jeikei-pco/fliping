"""
analizador.py — Análisis de mercado puro. V2.

RESPONSABILIDAD ÚNICA: Descargar datos OHLCV y calcular métricas del mercado.
No toma decisiones de trading. No calcula parámetros de grid.

Retorna MarketMetrics (dataclass tipado), no Dict[str, Any].

Mejoras respecto a V1:
  - Retorna MarketMetrics tipado en lugar de dict
  - Incluye fee_maker/fee_taker (para que backtester no los recalcule)
  - Añade volumen 24h (liquidez)
  - Añade detección de tendencia (EMA 21 vs EMA 50)
  - ATR con manejo explícito de NaN
  - Alineación de velas 1m/5m con validación de timestamps
  - Filtro de datos insuficientes más robusto
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.models import MarketMetrics, TrendDirection

logger = logging.getLogger("UAO_Grid.Analizador")

# Constantes
_MIN_GRID_PCT   = 0.002   # 0.2% — mínimo rentable por comisiones
_MIN_RANGO      = 0.0005  # Filtro inicial: rango mediano mínimo
_EMA_FAST       = 21      # EMA rápida para detección de tendencia
_EMA_SLOW       = 50      # EMA lenta para detección de tendencia
_TREND_THRESHOLD = 0.003  # 0.3%: separación mínima EMA para declarar tendencia


# ─────────────────────────────────────────────────────────────────────────────
# Funciones de cálculo modulares (privadas)
# ─────────────────────────────────────────────────────────────────────────────

def _calcular_recorrido_real(df1: pd.DataFrame) -> pd.Series:
    """
    Calcula el recorrido absoluto interno dentro de cada bloque de 5 minutos.

    Agrupa las velas de 1m en bloques de 5 (= 1 vela de 5m) y suma las
    diferencias absolutas entre cierres consecutivos como % del precio de apertura.

    osc > 1 → el precio recorrió más distancia que el simple rango H-L (zig-zag real).
    """
    df = df1.copy()
    df["grupo"] = np.arange(len(df)) // 5
    vals = []
    for _, g in df.groupby("grupo"):
        c = g["close"].to_numpy()
        if len(c) < 2:
            vals.append(0.0)
        else:
            open_ref = float(g["open"].iloc[0]) or c[0]
            vals.append(float(np.abs(np.diff(c)).sum() / (open_ref + 1e-9)))
    return pd.Series(vals)


def _calcular_simetria(df5: pd.DataFrame) -> float:
    """
    Balance entre movimientos alcistas y bajistas.
    Retorna un valor en [0, 1] donde 1 = simetría perfecta.
    """
    up   = df5[df5["ret"] > 0]
    down = df5[df5["ret"] < 0]
    if len(up) > 5 and len(down) > 5:
        med_up   = up["range_pct"].median()
        med_down = down["range_pct"].median()
        return float(min(med_up, med_down) / (max(med_up, med_down) + 1e-9))
    return 0.5


def _calcular_consistencia_norm(df5: pd.DataFrame) -> float:
    """
    Consistencia normalizada [0, 1] usando tanh(1/cv).
    cv bajo → volatilidad estable → consistencia alta.
    cv alto → volatilidad esporádica → consistencia baja.
    """
    media = df5["range_pct"].mean()
    std   = df5["range_pct"].std()
    cv    = std / (media + 1e-9)
    return float(np.tanh(1.0 / (cv + 0.01)))


def _calcular_oscilacion(df5: pd.DataFrame) -> float:
    """
    Ratio recorrido_real / range_pct.
    >1 = el precio recorrió más distancia que el simple rango H-L (zig-zag real).
    """
    return float(
        (df5["recorrido_real"].mean() + 1e-9) / (df5["range_pct"].mean() + 1e-9)
    )


def _calcular_atr(df5: pd.DataFrame) -> Tuple[float, float]:
    """
    Calcula el ATR(14) en porcentaje y en valor absoluto.

    Returns:
        (atr_pct, atr_abs) — atr_pct como fracción (ej. 0.003), atr_abs en precio.
    """
    high  = df5["high"]
    low   = df5["low"]
    close = df5["close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    atr_series = tr.rolling(14, min_periods=1).mean()
    last_close = float(close.iloc[-1])

    # Protección explícita contra NaN
    atr_val = float(atr_series.iloc[-1])
    if np.isnan(atr_val) or last_close <= 0:
        # Fallback: usar mediana del rango si ATR no disponible
        atr_val = float((high - low).median())

    atr_abs = round(atr_val, 8)
    atr_pct = round(atr_val / (last_close + 1e-9), 8)
    return atr_pct, atr_abs


def _calcular_tendencia(df5: pd.DataFrame) -> Tuple[TrendDirection, float, float]:
    """
    Detecta la tendencia usando EMA 21 vs EMA 50.

    Returns:
        (TrendDirection, ema_fast, ema_slow)
    """
    close = df5["close"]
    if len(close) < _EMA_SLOW:
        return TrendDirection.SIDEWAYS, float(close.iloc[-1]), float(close.iloc[-1])

    ema_fast = float(close.ewm(span=_EMA_FAST, adjust=False).mean().iloc[-1])
    ema_slow = float(close.ewm(span=_EMA_SLOW, adjust=False).mean().iloc[-1])

    diff_pct = (ema_fast - ema_slow) / (ema_slow + 1e-9)

    if diff_pct > _TREND_THRESHOLD:
        trend = TrendDirection.BULLISH
    elif diff_pct < -_TREND_THRESHOLD:
        trend = TrendDirection.BEARISH
    else:
        trend = TrendDirection.SIDEWAYS

    return trend, round(ema_fast, 6), round(ema_slow, 6)


def _calcular_score_zigzag(
    ops: float,
    pct_util: float,
    consistencia: float,
    sim: float,
    osc: float,
    rango_vela_mediano: float,
) -> Tuple[float, float, float]:
    """
    Score compuesto para detectar símbolos con zig-zag constante y velas grandes.

    Pesos:
      zigzag_score  40% — zig-zag de calidad (osc × sim, normalizado)
      amplitude     25% — velas grandes relativas al grid mínimo (5 pts × ratio)
      ops           15% — operaciones reales por vela
      pct_util      12% — % de velas con rango útil
      consistencia   8% — volatilidad estable en el tiempo

    NOTA: La penalización por deriva fue eliminada intencionalmente.
    El engine gestiona mercados direccionales con trailing de malla.
    El backtester elige el modo óptimo (NEUTRAL/LONG/SHORT).

    Returns:
        (score, zigzag_score, amplitude_ratio)
    """
    # Zig-zag: [0, 1] — 1 = zig-zag perfecto
    zigzag_score = float(np.tanh(osc * sim))

    # Amplitud relativa al mínimo de grid (cap en 5.0)
    amplitude_ratio = min(rango_vela_mediano / _MIN_GRID_PCT, 5.0)

    score = (
        zigzag_score    * 40.0 +   # Zig-zag de calidad
        amplitude_ratio *  5.0 +   # Velas grandes (ratio max=5 → max 25 pts)
        ops             * 15.0 +   # Operaciones reales por vela
        pct_util        * 12.0 +   # % velas útiles
        consistencia    *  8.0     # Volatilidad estable [0, 1]
    )

    return round(score, 3), round(zigzag_score, 4), round(amplitude_ratio, 3)


# ─────────────────────────────────────────────────────────────────────────────
# Función principal por símbolo
# ─────────────────────────────────────────────────────────────────────────────

def analizar_simbolo(
    exchange: Any,
    symbol: str,
    precio_vivo: Optional[float] = None,
    timeframe: str = "5m",
    limit: int = 500,
    volumen_24h_usdt: float = 0.0,
) -> Optional[MarketMetrics]:
    """
    Analiza un símbolo y retorna sus métricas de mercado.

    Args:
        exchange: Instancia CCXT con mercados cargados.
        symbol: Símbolo en formato CCXT ('BTC/USDT:USDT').
        precio_vivo: Precio actual del ticker (evita fetch adicional).
        timeframe: Marco temporal para el análisis (default '5m').
        limit: Número de velas a descargar.
        volumen_24h_usdt: Volumen 24h ya conocido (evita fetch adicional).

    Returns:
        MarketMetrics o None si los datos son insuficientes.
    """
    try:
        # ── Comisiones del mercado ─────────────────────────────────────────────
        market      = (exchange.markets or {}).get(symbol, {}) if hasattr(exchange, "markets") else {}
        fee_maker   = float(market.get("maker") or 0.00020)
        fee_taker   = float(market.get("taker") or 0.00050)
        comision_rt = fee_maker * 2  # Comisión ida + vuelta

        # ── Datos OHLCV ───────────────────────────────────────────────────────
        velas_5m = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not velas_5m or len(velas_5m) < max(50, limit // 4):
            return None
        df5 = pd.DataFrame(velas_5m, columns=["ts", "open", "high", "low", "close", "volume"])
        df5 = df5.astype({c: float for c in ["open", "high", "low", "close", "volume"]})

        # Velas de 1m para recorrido real (5× el límite de 5m)
        velas_1m = exchange.fetch_ohlcv(symbol, timeframe="1m", limit=limit * 5)
        if not velas_1m or len(velas_1m) < limit:
            return None
        df1 = pd.DataFrame(velas_1m, columns=["ts", "open", "high", "low", "close", "volume"])
        df1 = df1.astype({c: float for c in ["open", "high", "low", "close", "volume"]})

        # ── Métricas base ─────────────────────────────────────────────────────
        df5["range_pct"] = (df5["high"] - df5["low"]) / (df5["open"] + 1e-9)
        df5["ret"]       = (df5["close"] - df5["open"]) / (df5["open"] + 1e-9)

        # Filtro de liquidez / volatilidad mínima
        rango_mediano = float(df5["range_pct"].median())
        if rango_mediano < _MIN_RANGO:
            return None

        # ── Recorrido real (alineado por bloques de 5 velas de 1m) ────────────
        recorrido_serie = _calcular_recorrido_real(df1)
        n_comun = min(len(df5), len(recorrido_serie))
        df5 = df5.iloc[:n_comun].copy()
        df5["recorrido_real"] = recorrido_serie.iloc[:n_comun].values

        rango_vela_mediano    = float(df5["range_pct"].median())
        recorrido_real_mediano = float(df5["recorrido_real"].median())

        # ── Espaciado óptimo del grid ─────────────────────────────────────────
        ganancia_min      = 0.0005   # 0.05% de ganancia mínima deseada
        min_step_posible  = comision_rt + ganancia_min
        grid_step_optimo  = max(rango_vela_mediano * 0.8, min_step_posible)

        # ── Velas útiles y operaciones ─────────────────────────────────────────
        df5["vela_util"]    = df5["range_pct"] >= grid_step_optimo
        pct_util            = float(df5["vela_util"].mean())
        df5["ops_reales"]   = np.floor(df5["recorrido_real"] / (grid_step_optimo + 1e-9))
        ops                 = float(df5["ops_reales"].mean())

        # ── Métricas de oscilación ────────────────────────────────────────────
        consistencia = _calcular_consistencia_norm(df5)
        sim          = _calcular_simetria(df5)
        osc          = _calcular_oscilacion(df5)
        deriva       = (df5["high"].max() - df5["low"].min()) / (df5["low"].min() + 1e-9)

        # ── ATR ───────────────────────────────────────────────────────────────
        atr_pct, atr_abs = _calcular_atr(df5)

        # ── Tendencia ─────────────────────────────────────────────────────────
        tendencia, ema_fast, ema_slow = _calcular_tendencia(df5)

        # ── Score ─────────────────────────────────────────────────────────────
        score, zigzag_score, amplitude_ratio = _calcular_score_zigzag(
            ops, pct_util, consistencia, sim, osc, rango_vela_mediano
        )

        # ── Precio actual ─────────────────────────────────────────────────────
        precio = precio_vivo if precio_vivo else float(df5["close"].iloc[-1])

        return MarketMetrics(
            symbol               = symbol,
            precio               = precio,
            atr_pct              = atr_pct,
            atr_abs              = atr_abs,
            rango_vela_mediano   = round(rango_vela_mediano, 6),
            recorrido_real       = round(recorrido_real_mediano, 6),
            ops_promedio         = round(ops, 2),
            velas_utiles_pct     = round(pct_util * 100, 2),
            consistencia         = round(consistencia, 3),
            simetria             = round(sim, 3),
            oscilacion           = round(osc, 3),
            zigzag_score         = zigzag_score,
            amplitude_ratio      = amplitude_ratio,
            deriva_pct           = round(deriva * 100, 2),
            score                = score,
            grid_step_optimo     = round(grid_step_optimo, 6),
            volumen_24h_usdt     = volumen_24h_usdt,
            tendencia            = tendencia,
            ema_fast             = ema_fast,
            ema_slow             = ema_slow,
            fee_maker            = fee_maker,
            fee_taker            = fee_taker,
        )

    except Exception as exc:
        logger.debug("analizar_simbolo %s: %s", symbol, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Análisis en lote (paralelo)
# ─────────────────────────────────────────────────────────────────────────────

def analizar_lote(
    exchange: Any,
    simbolos: List[str],
    tickers_info: Optional[Dict[str, Any]] = None,
    timeframe: str = "5m",
    limit: int = 500,
    workers: int = 10,
) -> List[MarketMetrics]:
    """
    Analiza múltiples símbolos en paralelo.

    Args:
        exchange: Instancia CCXT con mercados cargados.
        simbolos: Lista de símbolos a analizar.
        tickers_info: Dict de tickers precargados {symbol: {last: precio, quoteVolume: vol}}.
        timeframe: Marco temporal (default '5m').
        limit: Número de velas a descargar por símbolo.
        workers: Máximo de hilos paralelos.

    Returns:
        Lista de MarketMetrics ordenada por score descendente.
    """
    resultados: List[MarketMetrics] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futuros: Dict[Any, str] = {}
        for sym in simbolos:
            ticker = (tickers_info or {}).get(sym, {})
            precio = ticker.get("last") or ticker.get("close")
            vol    = float(ticker.get("quoteVolume") or 0.0)
            futuros[
                executor.submit(analizar_simbolo, exchange, sym, precio, timeframe, limit, vol)
            ] = sym

        for fut in as_completed(futuros):
            try:
                result = fut.result()
                if result is not None:
                    resultados.append(result)
            except Exception as exc:
                logger.debug("analizar_lote error en %s: %s", futuros[fut], exc)

    # Ordenar por score descendente
    resultados.sort(key=lambda m: m.score, reverse=True)
    logger.info(
        "✅ Análisis completado: %d/%d símbolos aptos | Top: %s (score=%.1f)",
        len(resultados),
        len(simbolos),
        resultados[0].symbol if resultados else "N/A",
        resultados[0].score if resultados else 0.0,
    )
    return resultados


# ─────────────────────────────────────────────────────────────────────────────
# Análisis de correlaciones (nuevo en V2)
# ─────────────────────────────────────────────────────────────────────────────

def calcular_correlaciones(
    exchange: Any,
    simbolos: List[str],
    timeframe: str = "1h",
    limit: int = 100,
) -> Dict[str, float]:
    """
    Calcula la correlación media de cada símbolo con los demás.
    Útil para evitar sobreconcentración en activos muy correlacionados.

    Returns:
        Dict {symbol: correlacion_media} — valores cercanos a 1.0 = muy correlacionado.
    """
    closes: Dict[str, pd.Series] = {}
    for sym in simbolos[:20]:  # Limitar para evitar rate limiting
        try:
            velas = exchange.fetch_ohlcv(sym, timeframe=timeframe, limit=limit)
            if velas and len(velas) >= 30:
                df = pd.DataFrame(velas, columns=["ts", "open", "high", "low", "close", "volume"])
                closes[sym] = df["close"].pct_change().dropna()
        except Exception:
            pass

    if len(closes) < 2:
        return {s: 0.0 for s in simbolos}

    returns_df = pd.DataFrame(closes)
    corr_matrix = returns_df.corr()

    result = {}
    for sym in closes:
        others = [s for s in closes if s != sym]
        if others:
            avg_corr = float(corr_matrix.loc[sym, others].mean())
            result[sym] = round(avg_corr, 3)
        else:
            result[sym] = 0.0

    return result
