"""
backtester.py — Backtest Histórico de Malla (Grid) con Trailing y Direccionalidad.
Integra OptimizadorGrid, simula Long/Short/Neutral, aplica Slippage y mueve el grid.
Evalúa 20 horas de historia en 5m y 15m.
"""
import logging
import math
import pandas as pd
from typing import Any, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.optimizador import OptimizadorGrid

logger = logging.getLogger("UAO_Sclaping.GridBacktester")

# ==========================================
# 1. MOTOR DE SIMULACIÓN DINÁMICA
# ==========================================
def _simular_grid_dinamico(df: pd.DataFrame, params: Dict[str, Any], capital: float, leverage: float, fee_maker: float, fee_taker: float, slippage_pct: float) -> Dict[str, Any]:
    espaciado = params["espaciado_pct"]
    modo = params["modo"]
    
    capital_por_linea = (capital / params["num_grids"]) * leverage
    
    costos_totales_pct = fee_maker + fee_taker + (slippage_pct * 2)
    pnl_neto_trade_pct = espaciado - costos_totales_pct
    pnl_neto_fiat = capital_por_linea * pnl_neto_trade_pct
    
    pnl_acumulado = 0.0
    operaciones = 0
    
    precio_actual = df["open"].iloc[0]
    limite_sup = params["limite_superior"]
    limite_inf = params["limite_inferior"]
    
    inventario = params["num_grids"] // 2 if modo == "NEUTRAL" else 0
        
    for _, row in df.iterrows():
        movimientos = [row["open"], row["low"], row["high"], row["close"]]
        
        for precio in movimientos:
            # --- LÓGICA TRAILING ---
            if precio > limite_sup:
                ajuste = precio - limite_sup
                limite_sup += ajuste
                limite_inf += ajuste
                if modo in ["LONG", "NEUTRAL"]: inventario = max(0, inventario - 1)
                    
            elif precio < limite_inf:
                ajuste = limite_inf - precio
                limite_sup -= ajuste
                limite_inf -= ajuste
                if modo in ["SHORT", "NEUTRAL"]: inventario = max(0, inventario - 1)
            
            # --- LÓGICA DE EJECUCIÓN ---
            while precio >= precio_actual * (1 + espaciado):
                precio_actual *= (1 + espaciado)
                if modo in ["NEUTRAL", "LONG"] and inventario > 0:
                    inventario -= 1
                    operaciones += 1
                    pnl_acumulado += pnl_neto_fiat
                elif modo == "SHORT":
                    inventario += 1
                    
            while precio <= precio_actual * (1 - espaciado):
                precio_actual *= (1 - espaciado)
                if modo in ["NEUTRAL", "SHORT"] and inventario > 0:
                    inventario -= 1
                    operaciones += 1
                    pnl_acumulado += pnl_neto_fiat
                elif modo == "LONG":
                    inventario += 1

    return {
        "operaciones": operaciones,
        "pnl_neto": pnl_acumulado,
        "espaciado_pct": espaciado,
        "limite_sup_final": limite_sup,
        "limite_inf_final": limite_inf
    }

# ==========================================
# 2. ORQUESTADOR DE BACKTEST (20 Horas / 5m y 15m)
# ==========================================
def _backtest_grid_simbolo(exchange: Any, analisis: Dict[str, Any], capital: float, leverage_maximo: float, slippage_pct: float, ia_overrides: Dict[str, Any] = None) -> Dict[str, Any]:
    symbol = analisis["symbol"]
    # 20 horas = 1200 minutos. 5m = 240 velas, 15m = 80 velas.
    configuraciones = [("5m", 240), ("15m", 80)]
    mejor_resultado_global = None
    rechazos = []  # recolectar razones de rechazo para el log final

    market = exchange.markets.get(symbol, {}) if hasattr(exchange, 'markets') and exchange.markets else {}
    fee_maker = float(market.get("maker", 0.00020))
    fee_taker = float(market.get("taker", 0.00050))
    
    # Inicializamos el nuevo optimizador con el apalancamiento máximo permitido
    optimizador = OptimizadorGrid(max_leverage=int(leverage_maximo), overrides=ia_overrides)

    for timeframe, limit in configuraciones:
        try:
            velas = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not velas or len(velas) < (limit // 2):
                rechazos.append(f"{timeframe}:pocas_velas({len(velas) if velas else 0}<{limit//2})")
                continue
                
            df = pd.DataFrame(velas, columns=["timestamp", "open", "high", "low", "close", "volume"])
            
            # Probar los 3 escenarios direccionales
            for modo in ["NEUTRAL", "LONG", "SHORT"]:
                # 1. Obtenemos la configuración dinámica (riesgo, grid, apalancamiento)
                params_optimos = optimizador.optimizar_symbol(symbol, df, capital, analisis, modo=modo)
                
                if not params_optimos.get("valido", False):
                    razon = params_optimos.get("razon", "valido=False")
                    rechazos.append(f"{timeframe}/{modo}:{razon}")
                    continue

                # 2. Simulamos la ejecución con el apalancamiento seguro calculado
                res = _simular_grid_dinamico(
                    df=df, 
                    params=params_optimos, 
                    capital=capital, 
                    leverage=params_optimos["apalancamiento"], 
                    fee_maker=fee_maker, 
                    fee_taker=fee_taker, 
                    slippage_pct=slippage_pct
                )
                
                # 3. Consolidar resultados
                res["modo_optimo"] = modo
                res["timeframe_optimo"] = timeframe
                res["apalancamiento_usado"] = params_optimos["apalancamiento"]
                res["num_grids"] = params_optimos["num_grids"]
                res["params_optimos"] = params_optimos
                
                # Guardar el ganador absoluto basado en PNL
                if mejor_resultado_global is None or res["pnl_neto"] > mejor_resultado_global["pnl_neto"]:
                    mejor_resultado_global = res
                    
        except Exception as e:
            rechazos.append(f"{timeframe}:exception({e})")
            logger.warning("⚠️ [BT] Error en %s %s: %s", symbol, timeframe, e)
            continue

    # Retornar el diccionario estructurado para el orquestador
    if mejor_resultado_global:
        logger.info(
            "✔ [BT] %-25s → PnL=$%.4f | Modo:%-8s | Lev:%2dx | Ops:%4d | TF:%s",
            symbol, mejor_resultado_global["pnl_neto"], mejor_resultado_global["modo_optimo"],
            mejor_resultado_global["apalancamiento_usado"], mejor_resultado_global["operaciones"],
            mejor_resultado_global["timeframe_optimo"]
        )
        return {
            "symbol": symbol,
            "timeframe": mejor_resultado_global["timeframe_optimo"],
            "modo": mejor_resultado_global["modo_optimo"],
            "apalancamiento": mejor_resultado_global["apalancamiento_usado"],
            "operaciones": mejor_resultado_global["operaciones"],
            "pnl_neto": round(mejor_resultado_global["pnl_neto"], 4),
            "espaciado_pct": round(mejor_resultado_global["espaciado_pct"], 6),
            "num_grids": mejor_resultado_global["num_grids"],
            "analisis_original": analisis,
            "params_optimos": mejor_resultado_global.get("params_optimos", {}),
            "ai_overrides": ia_overrides or {}
        }
        
    logger.warning(
        "✘ [BT] %-25s → RECHAZADO. Razones: %s",
        symbol, " | ".join(rechazos) if rechazos else "sin_resultados"
    )
    return {"symbol": symbol, "pnl_neto": -999.0}

def backtest_grid_top(exchange: Any, top_analisis: List[Dict[str, Any]], capital: float, leverage: float, ia_overrides: Dict[str, Any] = None, slippage_pct: float = 0.0005) -> List[Dict[str, Any]]:
    resultados = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futs = {
            executor.submit(_backtest_grid_simbolo, exchange, analisis, capital, leverage, slippage_pct, ia_overrides): analisis["symbol"]
            for analisis in top_analisis
        }
        for fut in as_completed(futs):
            res = fut.result()
            if res.get("pnl_neto", -999.0) != -999.0:
                resultados.append(res)
                
    resultados.sort(key=lambda x: x["pnl_neto"], reverse=True)
    return resultados
