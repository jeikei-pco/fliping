"""
backtester.py — Backtest Histórico de Malla (Grid).
Simula capital con leverage y buffer anti-liquidación.
Usa múltiplos de ATR para espaciado dinámico.
"""
import logging
import pandas as pd
import numpy as np
from typing import Any, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("UAO_Sclaping.GridBacktester")

def _simular_espaciado(df: pd.DataFrame, espaciado_pct: float, num_lineas_lado: int, capital_usdt: float, leverage: float, fee_maker: float, fee_taker: float) -> Dict[str, Any]:
    # --- NUEVA LÓGICA DE CAPITAL EXACTO (SIN BUFFER DEL 80%) ---
    total_lineas = num_lineas_lado * 2
    capital_por_linea = (capital_usdt / total_lineas) * leverage 
    precio_base = df["open"].iloc[0]
    
    longs = []
    shorts = []
    
    for i in range(1, num_lineas_lado + 1):
        longs.append({
            "entrada": precio_base * (1 - (espaciado_pct * i)),
            "tp": precio_base * (1 - (espaciado_pct * (i - 1))),
            "activo": False
        })
        shorts.append({
            "entrada": precio_base * (1 + (espaciado_pct * i)),
            "tp": precio_base * (1 + (espaciado_pct * (i - 1))),
            "activo": False
        })
        
    pnl_acumulado = 0.0
    operaciones = 0
    
    fee_total_pct = fee_maker + fee_taker
    
    pnl_bruto_trade = capital_por_linea * espaciado_pct
    comision_trade = capital_por_linea * fee_total_pct
    pnl_neto_trade = pnl_bruto_trade - comision_trade

    for _, row in df.iterrows():
        high = row["high"]
        low = row["low"]
        
        for l in longs:
            if not l["activo"]:
                if low <= l["entrada"]: l["activo"] = True
            else:
                if high >= l["tp"]:
                    l["activo"] = False
                    pnl_acumulado += pnl_neto_trade
                    operaciones += 1
                    
        for s in shorts:
            if not s["activo"]:
                if high >= s["entrada"]: s["activo"] = True
            else:
                if low <= s["tp"]:
                    s["activo"] = False
                    pnl_acumulado += pnl_neto_trade
                    operaciones += 1

    return {
        "espaciado_pct": espaciado_pct,
        "operaciones": operaciones,
        "pnl_neto": pnl_acumulado
    }

def _backtest_grid_simbolo(exchange: Any, symbol: str, timeframe: str, limit: int, capital: float, leverage: float) -> Dict[str, Any]:
    try:
        velas = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        if not velas or len(velas) < (limit // 2):
            return {"symbol": symbol, "pnl_neto": -999.0}
            
        df = pd.DataFrame(velas, columns=["timestamp", "open", "high", "low", "close", "volume"])
        
        # Calcular ATR
        df["tr"] = np.maximum(df["high"] - df["low"], 
                              np.maximum(abs(df["high"] - df["close"].shift(1)), 
                                         abs(df["low"] - df["close"].shift(1))))
        atr = df["tr"].rolling(14).mean().iloc[-1]
        precio_actual = df["close"].iloc[-1]
        
        if atr <= 0 or precio_actual <= 0:
            return {"symbol": symbol, "pnl_neto": -999.0}
            
        atr_pct = atr / precio_actual
        
        # Probar múltiplos de ATR
        espaciados_a_probar = [atr_pct * 0.5, atr_pct * 0.75, atr_pct * 1.0, atr_pct * 1.5, atr_pct * 2.0]
        mejor_resultado = None
        
        market = exchange.markets.get(symbol, {})
        fee_maker = float(market.get("maker") if market.get("maker") is not None else 0.00020)
        fee_taker = float(market.get("taker") if market.get("taker") is not None else 0.00050)
        
        for espaciado in espaciados_a_probar:
            res = _simular_espaciado(df, espaciado, 5, capital, leverage, fee_maker, fee_taker)
            if mejor_resultado is None or res["pnl_neto"] > mejor_resultado["pnl_neto"]:
                mejor_resultado = res
                
        return {
            "symbol": symbol,
            "operaciones": mejor_resultado["operaciones"],
            "pnl_neto": round(mejor_resultado["pnl_neto"], 4),
            "espaciado_pct": mejor_resultado["espaciado_pct"]
        }

    except Exception:
        return {"symbol": symbol, "pnl_neto": -999.0}

def backtest_grid_top(exchange: Any, top_symbols: List[str], timeframe: str, limit: int, capital: float, leverage: float, delay: float = 0.0) -> List[Dict[str, Any]]:
    resultados = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futs = {
            executor.submit(_backtest_grid_simbolo, exchange, sym, timeframe, limit, capital, leverage): sym
            for sym in top_symbols
        }
        for fut in as_completed(futs):
            res = fut.result()
            if res["pnl_neto"] != -999.0:
                resultados.append(res)
                
    resultados.sort(key=lambda x: x["pnl_neto"], reverse=True)
    return resultados
