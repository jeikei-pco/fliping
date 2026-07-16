"""
grid_analizador.py — Análisis Cuantitativo Optimizado para Grid Infinita.

Calcula la volatilidad local y el rango óptimo para alimentar el motor de inventario.
"""
import logging
import pandas as pd
import numpy as np
from typing import Any, Dict, List, Optional

logger = logging.getLogger("UAO_Sclaping.GridAnalizador")

def _analizar_simbolo_grid(exchange: Any, symbol: str, timeframe: str, limit: int) -> Optional[Dict[str, Any]]:
    try:
        velas = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        if not velas or len(velas) < (limit // 2):
            return None

        df = pd.DataFrame(velas, columns=["timestamp", "open", "high", "low", "close", "volume"])
        
        # Rango real de la vela porcentual (evita mechas de 24h que pueden ser anomalías)
        df["rango_vela_pct"] = (df["high"] - df["low"]) / df["open"]
        
        rango_pct_promedio = df["rango_vela_pct"].mean()
        
        if rango_pct_promedio < 0.0015: # Filtro de volatilidad mínima (0.15% por vela 5m)
            return None
            
        precio_ult = df["close"].iloc[-1]
        precio_ini = df["open"].iloc[0]
        deriva_total_pct = abs((precio_ult - precio_ini) / precio_ini)
        
        # Consistencia: ¿Qué tan agrupados están los rangos de las velas? (Inverso de desviación estándar)
        std_rango = df["rango_vela_pct"].std()
        consistencia = 1.0 / (std_rango + 1e-6)
        
        # Score optimizado para Grid: (rango_medio² * consistencia) / (drift + epsilon)
        score = (rango_pct_promedio ** 2 * consistencia) / (deriva_total_pct + 1e-4)

        return {
            "symbol": symbol,
            "precio_actual": precio_ult,
            "rango_pct_promedio": round(rango_pct_promedio, 6),
            "deriva_total_pct": round(deriva_total_pct, 6),
            "score": round(score, 4)
        }
    except Exception:
        return None

def analizar_lote(exchange: Any, simbolos: List[str], timeframe: str, limit: int, delay: float = 0.0) -> pd.DataFrame:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time
    
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
