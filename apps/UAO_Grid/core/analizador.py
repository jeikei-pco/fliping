"""
analizador.py — Análisis Cuantitativo Optimizado para Grid Infinita.

Calcula la volatilidad local y el rango óptimo para alimentar el motor de inventario.
"""
import logging
import pandas as pd
import numpy as np
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

logger = logging.getLogger("UAO_Sclaping.GridAnalizador")

def _analizar_simbolo_grid(exchange: Any, symbol: str, timeframe: str, limit: int) -> Optional[Dict[str, Any]]:
    try:
        velas = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        if not velas or len(velas) < (limit // 2):
            return None

        df = pd.DataFrame(velas, columns=["timestamp", "open", "high", "low", "close", "volume"])
        
        # Rango real de la vela porcentual (evita mechas de 24h que pueden ser anomalías)
        df["rango_vela_pct"] = (df["high"] - df["low"]) / df["open"]
        
        # 1. Usar la MEDIANA en lugar del promedio (ignora mechazos atípicos)
        rango_pct_mediano = df["rango_vela_pct"].median()
        
        if rango_pct_mediano < 0.0015: # Filtro de volatilidad mínima (0.15% por vela 5m)
            return None
            
        precio_ult = df["close"].iloc[-1]
        
        # 2. Deriva REAL: Rango máximo histórico del periodo (evita el espejismo inicio-fin)
        max_precio = df["high"].max()
        min_precio = df["low"].min()
        deriva_real_pct = (max_precio - min_precio) / min_precio
        
        # 3. Consistencia: ¿Qué tan agrupados están los rangos de las velas? (Inverso de desviación estándar)
        std_rango = df["rango_vela_pct"].std()
        consistencia = 1.0 / (std_rango + 1e-6)
        
        # 4. Score optimizado para Grid: (rango_mediano² * consistencia) / (deriva_real + epsilon)
        score = (rango_pct_mediano ** 2 * consistencia) / (deriva_real_pct + 1e-4)

        return {
            "symbol": symbol,
            "precio_actual": precio_ult,
            "rango_pct_mediano": round(rango_pct_mediano, 6),
            "deriva_real_pct": round(deriva_real_pct, 6),
            "score": round(score, 4)
        }
    except Exception:
        return None

def analizar_lote(exchange: Any, simbolos: List[str], timeframe: str, limit: int, delay: float = 0.0) -> pd.DataFrame:
    resultados = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futuros = {executor.submit(_analizar_simbolo_grid, exchange, sym, timeframe, limit): sym for sym in simbolos}
        for idx, fut in enumerate(as_completed(futuros)):
            res = fut.result()
            if res: resultados.append(res)
            if delay > 0 and idx % 20 == 0: time.sleep(delay)
                
    if not resultados: return pd.DataFrame()
    df = pd.DataFrame(resultados).sort_values(by="score", ascending=False)
    return df
