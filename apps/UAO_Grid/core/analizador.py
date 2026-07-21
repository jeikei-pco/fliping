import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger("UAO_Scalping.GridAnalizador")


@dataclass
class ConfigGridAnalyzer:
    """Configuración base para el escaneo y evaluación del grid."""
    grid_step: float = 0.0035
    comision_rt: float = 0.0012
    ganancia_min: float = 0.0005
    
    @property
    def min_mov(self) -> float:
        """Movimiento mínimo requerido para que una vela sea útil."""
        return self.grid_step + self.comision_rt + self.ganancia_min


@dataclass
class GridMetrics:
    """Estructura de datos para los resultados del análisis."""
    symbol: str
    precio: float
    ops_promedio: float
    velas_utiles_pct: float
    consistencia: float
    simetria: float
    oscilacion: float
    deriva_pct: float
    score: float
    zigzag_score: float       # 🎯 Nuevo: calidad del zig-zag (osc * simetria)
    amplitude_ratio: float    # 🎯 Nuevo: tamaño de vela relativo al grid mínimo
    recorrido_real: float
    grid_step_optimo: float
    atr_pct: float
    rango_vela_mediano: float
    riesgo_volatilidad: float
    indice_tendencia: float
    indice_reversion: float
    eficiencia_grid: float
    grid_quality: float
    riesgo: float
    densidad_sugerida: float
    capital_factor: float
    apalancamiento_factor: float
    modo_preferido: str


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(float(value), max_value))


def _calcular_perfil_operativo(
    df5: pd.DataFrame,
    *,
    atr_pct: float,
    deriva: float,
    consistencia: float,
    simetria: float,
    oscilacion: float,
    pct_util: float,
    ops: float,
    zigzag_score: float,
    recorrido_real_mediano: float,
    rango_vela_mediano: float,
    grid_step_optimo: float,
) -> Dict[str, Any]:
    """
    Convierte las metricas del analizador en factores accionables para el optimizador.
    Todos los indices normalizados usan rangos acotados para evitar parametros extremos.
    """
    close_ini = float(df5.close.iloc[0])
    close_fin = float(df5.close.iloc[-1])
    retorno_total = (close_fin - close_ini) / (close_ini + 1e-9)

    riesgo_volatilidad = _clamp((atr_pct / 0.015) * 0.55 + (deriva / 0.12) * 0.45, 0.0, 1.0)
    fuerza_tendencia = _clamp(abs(retorno_total) / (deriva + 1e-9), 0.0, 1.0)
    indice_tendencia = fuerza_tendencia if retorno_total >= 0 else -fuerza_tendencia

    indice_reversion = _clamp(
        zigzag_score * 0.40
        + simetria * 0.20
        + consistencia * 0.20
        + _clamp(oscilacion / 3.0, 0.0, 1.0) * 0.20,
        0.0,
        1.0,
    )

    eficiencia_grid = _clamp(
        pct_util * 0.35
        + _clamp(ops / 2.0, 0.0, 1.0) * 0.25
        + _clamp(recorrido_real_mediano / (grid_step_optimo + 1e-9), 0.0, 2.0) * 0.20 / 2.0
        + _clamp(rango_vela_mediano / (grid_step_optimo + 1e-9), 0.0, 2.0) * 0.20 / 2.0,
        0.0,
        1.0,
    )

    grid_quality = _clamp(
        zigzag_score * 0.35
        + eficiencia_grid * 0.30
        + indice_reversion * 0.20
        + consistencia * 0.15,
        0.0,
        1.0,
    )

    riesgo = _clamp(
        riesgo_volatilidad * 0.50
        + (1.0 - consistencia) * 0.25
        + abs(indice_tendencia) * 0.25,
        0.0,
        1.0,
    )

    densidad_sugerida = _clamp(0.85 + grid_quality * 0.45 - riesgo * 0.20, 0.75, 1.25)
    capital_factor = _clamp(0.85 + grid_quality * 0.35 - riesgo * 0.25, 0.70, 1.20)
    apalancamiento_factor = _clamp(1.15 - riesgo * 0.35 + grid_quality * 0.10, 0.75, 1.15)

    if indice_tendencia > 0.35 and indice_reversion < 0.78:
        modo_preferido = "LONG"
    elif indice_tendencia < -0.35 and indice_reversion < 0.78:
        modo_preferido = "SHORT"
    else:
        modo_preferido = "NEUTRAL"

    return {
        "riesgo_volatilidad": round(riesgo_volatilidad, 4),
        "indice_tendencia": round(indice_tendencia, 4),
        "indice_reversion": round(indice_reversion, 4),
        "eficiencia_grid": round(eficiencia_grid, 4),
        "grid_quality": round(grid_quality, 4),
        "riesgo": round(riesgo, 4),
        "densidad_sugerida": round(densidad_sugerida, 4),
        "capital_factor": round(capital_factor, 4),
        "apalancamiento_factor": round(apalancamiento_factor, 4),
        "modo_preferido": modo_preferido,
    }


def _fetch_5m(exchange: Any, symbol: str, limit: int) -> pd.DataFrame:
    """Obtiene velas de 5 minutos."""
    velas = exchange.fetch_ohlcv(symbol, timeframe="5m", limit=limit)
    if not velas or len(velas) < limit // 2:
        return pd.DataFrame()
    return pd.DataFrame(velas, columns=["ts", "open", "high", "low", "close", "volume"])


def _fetch_1m(exchange: Any, symbol: str, limit: int) -> pd.DataFrame:
    """Obtiene velas de 1 minuto para el cálculo de recorrido interno."""
    velas = exchange.fetch_ohlcv(symbol, timeframe="1m", limit=limit * 5)
    if not velas or len(velas) < limit:
        return pd.DataFrame()
    return pd.DataFrame(velas, columns=["ts", "open", "high", "low", "close", "volume"])


def _alinear_velas(df5: pd.DataFrame, rec: pd.Series) -> pd.DataFrame:
    """Alinea los datos de 5m con el recorrido real extraído de 1m."""
    n = min(len(df5), len(rec))
    df5_aligned = df5.iloc[:n].copy()
    df5_aligned["recorrido_real"] = rec.iloc[:n].values
    return df5_aligned


def _calcular_recorrido_real(df1: pd.DataFrame) -> pd.Series:
    """Calcula el recorrido absoluto interno dentro de cada bloque de 5 minutos (usando velas de 1m)."""
    df = df1.copy()
    # Agrupamos en bloques de 5 velas (1m * 5 = 5m)
    df["grupo"] = np.arange(len(df)) // 5
    vals = []
    
    for _, g in df.groupby("grupo"):
        c = g["close"].to_numpy()
        if len(c) < 2:
            vals.append(0.0)
        else:
            vals.append(float(np.abs(np.diff(c)).sum() / c[0]))
            
    return pd.Series(vals)


def _calcular_simetria(df5: pd.DataFrame) -> float:
    """Calcula el balance entre los movimientos alcistas y bajistas."""
    up = df5[df5.ret > 0]
    down = df5[df5.ret < 0]
    
    if len(up) > 5 and len(down) > 5:
        sim = min(up.range_pct.median(), down.range_pct.median()) / max(up.range_pct.median(), down.range_pct.median())
    else:
        sim = 0.5
        
    return sim


def _calcular_consistencia(df5: pd.DataFrame) -> float:
    """Calcula la consistencia de la volatilidad a lo largo del tiempo (sin normalizar)."""
    media = df5.range_pct.mean()
    std = df5.range_pct.std()
    cv = std / (media + 1e-9)
    consistencia = 1 / (cv + 0.01)
    return consistencia


def _calcular_oscilacion(df5: pd.DataFrame) -> float:
    """Mide el zig-zag interno: recorrido_real / range_pct.
    osc > 1 → el precio recorrió más distancia que el simple rango H-L (ida y vuelta real).
    osc < 1 → movimiento mono-direccional dentro de la vela (sin rebote).
    """
    osc = (df5.recorrido_real.mean() + 1e-9) / (df5.range_pct.mean() + 1e-9)
    return osc


def _calcular_consistencia_norm(df5: pd.DataFrame) -> float:
    """
    Consistencia normalizada al rango [0, 1].
    cv alto (volatilidad esporádica) → consistencia baja.
    cv bajo (volatilidad estable) → consistencia alta.
    Usamos tanh para mapear suavemente sin explotar la escala.
    """
    media = df5.range_pct.mean()
    std = df5.range_pct.std()
    cv = std / (media + 1e-9)
    # tanh(1/cv): cv=0.5→tanh(2)=0.96 | cv=1→tanh(1)=0.76 | cv=2→tanh(0.5)=0.46
    return float(np.tanh(1.0 / (cv + 0.01)))


def _calcular_score_zigzag(
    ops: float,
    pct_util: float,
    consistencia: float,
    sim: float,
    osc: float,
    deriva: float,
    rango_vela_mediano: float,
) -> tuple[float, float, float]:
    """
    Score rediseñado para detectar símbolos con:
      1. Velas GRANDES (rango_vela_mediano alto)  ← amplitude_ratio
      2. Zig-zag CONSTANTE (osc > 1 + simetria ≈ 1)  ← zigzag_score

    Pesos:
      zigzag_score  40% — zig-zag de calidad (osc * sim, normalizado)
      amplitude     25% — velas grandes relativas al grid mínimo
      ops           15% — operaciones reales por vela
      pct_util      12% — % de velas con rango útil
      consistencia   8% — volatilidad estable en el tiempo

    NOTA sobre deriva:
      La penalización por deriva (tendencia) fue ELIMINADA.
      El engine tiene deslizamiento de malla (trailing) que maneja mercados
      direccionales — castigar deriva descartaría candidatos ideales para
      modo LONG/SHORT con trailing. El backtester elige el modo óptimo
      (NEUTRAL/LONG/SHORT) y el score solo mide calidad de oscilación/amplitud.

    Returns: (score, zigzag_score, amplitude_ratio)
    """
    # --- Zig-zag: osc mide cuánto oscila el precio DENTRO de la vela vs su rango H-L.
    # osc > 1 → el precio recorre más camino que el simple rango = verdadero zig-zag.
    # sim ≈ 1 → movimientos alcistas y bajistas equilibrados.
    zigzag_score = float(np.tanh(osc * sim))  # → [0, 1], 1 = zig-zag perfecto

    # --- Amplitud: qué tan grandes son las velas respecto al grid mínimo (0.2%).
    # Un rango de 0.004 (0.4%) = 2x el mínimo → amplitude_ratio = 2.0 (cap en 5)
    MIN_GRID = 0.002
    amplitude_ratio = min(rango_vela_mediano / MIN_GRID, 5.0)

    # --- Score compuesto (escala ≈ 0–100, SIN penalización por deriva)
    # deriva alta + zigzag alto → candidato LONG/SHORT con deslizamiento → NO penalizar
    score = (
        zigzag_score  * 40 +   # 🎯 Zig-zag de calidad
        amplitude_ratio * 5  +  # 🎯 Velas grandes (×5 porque ratio max=5 → max 25pts)
        ops           * 15 +   # Operaciones reales
        pct_util      * 12 +   # % velas útiles
        consistencia  * 8      # Consistencia [0,1]
    )

    return round(score, 3), round(zigzag_score, 4), round(amplitude_ratio, 3)


# Mantener alias de compatibilidad para otros módulos que importen _calcular_score
def _calcular_score(ops, pct_util, consistencia, sim, osc, deriva, rango_vela_mediano=0.003):
    score, _, _ = _calcular_score_zigzag(ops, pct_util, consistencia, sim, osc, deriva, rango_vela_mediano)
    return score



def _analizar_simbolo_grid(exchange: Any, symbol: str, precio_vivo: float = None, timeframe: str = "5m", limit: int = 500) -> Optional[Dict[str, Any]]:
    """Función orquestadora que analiza un símbolo aplicando todos los cálculos modulares."""
    try:
        market = exchange.markets.get(symbol, {}) if hasattr(exchange, 'markets') and exchange.markets else {}
        fee_maker = market.get("maker")
        if fee_maker is None:
            fee_maker = 0.00020
        else:
            fee_maker = float(fee_maker)
            
        comision_rt_dinamica = fee_maker * 2
        config = ConfigGridAnalyzer(comision_rt=comision_rt_dinamica)
        
        # 1. Extracción de datos
        df5 = _fetch_5m(exchange, symbol, limit)
        df1 = _fetch_1m(exchange, symbol, limit)
        
        if df5.empty or df1.empty:
            return None

        # 2. Métricas base
        df5["range_pct"] = (df5.high - df5.low) / df5.open
        df5["ret"] = (df5.close - df5.open) / df5.open
        df5["body_pct"] = (df5.close - df5.open).abs() / df5.open

        # 3. Alineación del recorrido real
        rec = _calcular_recorrido_real(df1)
        df5 = _alinear_velas(df5, rec)

        # Filtro de liquidez / volatilidad inicial
        rango = df5.range_pct.median()
        if rango < 0.0005:
            return None

        # 4. Operatividad Dinámica
        rango_vela_mediano = float(df5.range_pct.median())
        
        # El espaciado de la malla (grid_step) debe adaptarse a la vela típica del símbolo
        # Pero nunca puede ser menor que (comisión_ida_y_vuelta + ganancia_minima_deseada)
        min_step_posible = config.comision_rt + config.ganancia_min
        grid_step_optimo = max(rango_vela_mediano * 0.8, min_step_posible)
        
        # Actualizamos la configuración para que el cálculo de operaciones use el paso dinámico
        config.grid_step = grid_step_optimo

        # Una vela es útil si su rango supera el tamaño de una celda del grid (grid_step)
        df5["vela_util"] = df5.range_pct >= config.grid_step
        pct_util = float(df5.vela_util.mean())

        df5["ops_teoricas"] = np.floor(df5.range_pct / config.grid_step)
        df5["ops_reales"] = np.floor(df5.recorrido_real / config.grid_step)
        ops = float(df5.ops_reales.mean())
        
        # 5. Cálculos avanzados
        consistencia = _calcular_consistencia_norm(df5)  # [0,1] normalizado
        sim = _calcular_simetria(df5)
        osc = _calcular_oscilacion(df5)
        
        # 6. Deriva, Amplitud y Score zig-zag
        deriva = (df5.high.max() - df5.low.min()) / df5.low.min()

        recorrido_real_mediano = float(df5.recorrido_real.median())
        
        high, low, close = df5['high'], df5['low'], df5['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr_pct = float((tr.rolling(14).mean().iloc[-1]) / df5.close.iloc[-1])

        score, zigzag_score, amplitude_ratio = _calcular_score_zigzag(
            ops, pct_util, consistencia, sim, osc, deriva, rango_vela_mediano
        )

        perfil_operativo = _calcular_perfil_operativo(
            df5,
            atr_pct=atr_pct,
            deriva=deriva,
            consistencia=consistencia,
            simetria=sim,
            oscilacion=osc,
            pct_util=pct_util,
            ops=ops,
            zigzag_score=zigzag_score,
            recorrido_real_mediano=recorrido_real_mediano,
            rango_vela_mediano=rango_vela_mediano,
            grid_step_optimo=grid_step_optimo,
        )

        # 7. Formatear y retornar mediante DataClass
        precio_actual = precio_vivo if precio_vivo else float(df5.close.iloc[-1])
        
        metrics = GridMetrics(
            symbol=symbol,
            precio=precio_actual,
            ops_promedio=round(ops, 2),
            velas_utiles_pct=round(pct_util * 100, 2),
            consistencia=round(consistencia, 3),
            simetria=round(sim, 3),
            oscilacion=round(osc, 3),
            deriva_pct=round(deriva * 100, 2),
            score=score,
            zigzag_score=zigzag_score,
            amplitude_ratio=amplitude_ratio,
            recorrido_real=round(recorrido_real_mediano, 6),
            grid_step_optimo=round(grid_step_optimo, 6),
            atr_pct=round(atr_pct, 6),
            rango_vela_mediano=round(rango_vela_mediano, 6),
            riesgo_volatilidad=perfil_operativo["riesgo_volatilidad"],
            indice_tendencia=perfil_operativo["indice_tendencia"],
            indice_reversion=perfil_operativo["indice_reversion"],
            eficiencia_grid=perfil_operativo["eficiencia_grid"],
            grid_quality=perfil_operativo["grid_quality"],
            riesgo=perfil_operativo["riesgo"],
            densidad_sugerida=perfil_operativo["densidad_sugerida"],
            capital_factor=perfil_operativo["capital_factor"],
            apalancamiento_factor=perfil_operativo["apalancamiento_factor"],
            modo_preferido=perfil_operativo["modo_preferido"],
        )
        
        return metrics.__dict__
    
    except Exception as e:
        logger.debug("%s %s", symbol, e)
        return None


def analizar_lote(exchange: Any, simbolos: List[str], tickers_info: dict = None, timeframe: str = "5m", limit: int = 500, workers: int = 10, delay: float = 0) -> pd.DataFrame:
    """Analiza múltiples símbolos en paralelo y retorna un DataFrame clasificado."""
    res = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        for s in simbolos:
            pv = tickers_info.get(s, {}).get("last") if tickers_info else None
            futs[ex.submit(_analizar_simbolo_grid, exchange, s, pv, timeframe, limit)] = s
        
        for i, f in enumerate(as_completed(futs)):
            r = f.result()
            if r: 
                res.append(r)
            if delay and i % 20 == 0:
                time.sleep(delay)
                
    if not res:
        return pd.DataFrame()
        
    return pd.DataFrame(res).sort_values("score", ascending=False).reset_index(drop=True)
