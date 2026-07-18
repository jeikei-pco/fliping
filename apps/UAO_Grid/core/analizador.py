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
    recorrido_real: float
    grid_step_optimo: float
    atr_pct: float
    rango_vela_mediano: float


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
    """Calcula la consistencia de la volatilidad a lo largo del tiempo."""
    media = df5.range_pct.mean()
    std = df5.range_pct.std()
    cv = std / (media + 1e-9)
    consistencia = 1 / (cv + 0.01)
    
    return consistencia


def _calcular_oscilacion(df5: pd.DataFrame) -> float:
    """Mide la cantidad de ruido u oscilación interna en comparación con el rango de la vela."""
    osc = (df5.recorrido_real.mean() + 1e-9) / (df5.range_pct.mean() + 1e-9)
    return osc


def _calcular_score(ops: float, pct_util: float, consistencia: float, sim: float, osc: float, deriva: float) -> float:
    """Calcula la calificación final para operar en grid."""
    score = (ops * 35 + pct_util * 20 + consistencia * 15 + sim * 10 + osc * 20) / (1 + deriva)
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

        # 4. Operatividad
        df5["vela_util"] = df5.range_pct >= config.min_mov
        pct_util = float(df5.vela_util.mean())

        df5["ops_teoricas"] = np.floor(df5.range_pct / config.grid_step)
        df5["ops_reales"] = np.floor(df5.recorrido_real / config.grid_step)
        ops = float(df5.ops_reales.mean())
        
        # 5. Cálculos avanzados
        consistencia = _calcular_consistencia(df5)
        sim = _calcular_simetria(df5)
        osc = _calcular_oscilacion(df5)
        
        # 6. Deriva y Score
        deriva = (df5.high.max() - df5.low.min()) / df5.low.min()
        score = _calcular_score(ops, pct_util, consistencia, sim, osc, deriva)

        recorrido_real_mediano = float(df5.recorrido_real.median())
        grid_step_optimo = recorrido_real_mediano / max(1.0, ops)
        
        high, low, close = df5['high'], df5['low'], df5['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr_pct = float((tr.rolling(14).mean().iloc[-1]) / df5.close.iloc[-1])
        rango_vela_mediano = float(((high - low) / df5["open"]).median())

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
            score=round(score, 3),
            recorrido_real=round(recorrido_real_mediano, 6),
            grid_step_optimo=round(grid_step_optimo, 6),
            atr_pct=round(atr_pct, 6),
            rango_vela_mediano=round(rango_vela_mediano, 6)
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
