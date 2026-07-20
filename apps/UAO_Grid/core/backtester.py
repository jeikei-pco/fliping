"""
backtester.py - Backtest de Malla (Grid) por Fuerza Bruta.
Simula el comportamiento Multidireccional (Neutral), Long y Short.
Aplica la relación entre Alto Apalancamiento = Menor Distancia.
"""
import logging
import math
import pandas as pd
from typing import Any, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("UAO_Sclaping.GridBacktester")

# ==========================================
# 1. MOTOR DE SIMULACIÓN DINÁMICA (Réplica del Grid Futuros)
# ==========================================
def _simular_grid_dinamico(df: pd.DataFrame, params: Dict[str, Any], capital_total: float, fee_maker: float, fee_taker: float) -> Dict[str, Any]:
    """
    Simula el grid continuo multidireccional.
    Posicion Neta > 0 = LONG. Posicion Neta < 0 = SHORT.
    Si es NEUTRAL: Sube = Abre Short. Baja = Abre Long. Cobra ganancias al retroceder.
    """
    espaciado = params["espaciado_pct"]
    modo = params["modo"]
    apalancamiento = params["apalancamiento"]
    num_grids_total = params["num_grids"]
    
    # Lógica de distribución de capital
    # En modo NEUTRAL (mitad arriba, mitad abajo), el capital máximo en riesgo 
    # es solo el de un lado a la vez.
    num_lineas_lado = max(1, num_grids_total // 2) if modo == "NEUTRAL" else num_grids_total
    
    capital_por_linea = capital_total / num_lineas_lado
    tamano_orden = capital_por_linea * apalancamiento
    
    # Costo de abrir + Costo de cerrar + Margen de ganancia neto deseado
    margen_neto_minimo = 0.0010  # 0.10% de ganancia pura asegurada
    costos_totales_pct = fee_maker + fee_taker
    pnl_neto_trade_pct = espaciado - costos_totales_pct
    
    # Si la distancia no cubre los costos y el margen, descartar inmediatamente
    if pnl_neto_trade_pct < margen_neto_minimo:
        return {"operaciones": 0, "pnl_neto": -999.0}
        
    pnl_neto_fiat = tamano_orden * pnl_neto_trade_pct
    
    pnl_acumulado = 0.0
    operaciones = 0
    precio_actual = df["open"].iloc[0]
    
    posicion_neta = 0  # > 0 (LONG), < 0 (SHORT)
            
    for _, row in df.iterrows():
        movimientos = [row["open"], row["low"], row["high"], row["close"]]
        
        for precio in movimientos:
            # Sube el precio (Cruza línea hacia arriba)
            while precio >= precio_actual * (1 + espaciado):
                precio_actual *= (1 + espaciado)
                
                if modo == "NEUTRAL":
                    if posicion_neta > 0: # Teníamos un LONG abajo, cobramos ganancia
                        pnl_acumulado += pnl_neto_fiat
                        operaciones += 1
                        posicion_neta -= 1
                    elif posicion_neta > -num_lineas_lado: # Estamos del centro hacia arriba, abrimos SHORT
                        posicion_neta -= 1
                        
                elif modo == "LONG":
                    if posicion_neta > 0: # Cobramos ganancia del LONG
                        pnl_acumulado += pnl_neto_fiat
                        operaciones += 1
                        posicion_neta -= 1
                        
                elif modo == "SHORT":
                    if posicion_neta > -num_lineas_lado: # Acumulamos SHORT arriba
                        posicion_neta -= 1
            
            # Baja el precio (Cruza línea hacia abajo)
            while precio <= precio_actual * (1 - espaciado):
                precio_actual *= (1 - espaciado)
                
                if modo == "NEUTRAL":
                    if posicion_neta < 0: # Teníamos un SHORT arriba, cobramos ganancia
                        pnl_acumulado += pnl_neto_fiat
                        operaciones += 1
                        posicion_neta += 1
                    elif posicion_neta < num_lineas_lado: # Estamos del centro hacia abajo, abrimos LONG
                        posicion_neta += 1
                        
                elif modo == "LONG":
                    if posicion_neta < num_lineas_lado: # Acumulamos LONG abajo
                        posicion_neta += 1
                        
                elif modo == "SHORT":
                    if posicion_neta < 0: # Cobramos ganancia del SHORT
                        pnl_acumulado += pnl_neto_fiat
                        operaciones += 1
                        posicion_neta += 1

    return {
        "operaciones": operaciones,
        "pnl_neto": pnl_acumulado,
        "espaciado_pct": espaciado
    }

# ==========================================
# 2. ORQUESTADOR DE BACKTEST (Fuerza Bruta Múltiple)
# ==========================================
def _backtest_grid_simbolo(exchange: Any, analisis: Dict[str, Any], capital_total: float) -> Dict[str, Any]:
    symbol = analisis["symbol"]
    mejor_resultado_global = None

    market = exchange.markets.get(symbol, {}) if hasattr(exchange, 'markets') and exchange.markets else {}
    fee_maker = float(market.get("maker", 0.00020))
    fee_taker = float(market.get("taker", 0.00050))
    min_spread_rentable = fee_maker + fee_taker + 0.0010

    # Matrices de Fuerza Bruta
    modos_estrategia = ["NEUTRAL", "LONG", "SHORT"]
    variaciones_apalancamiento = [10, 15, 20] 
    variaciones_lineas = [6, 8, 10, 14, 20] # Probamos mallas más densas
    
    rango_vela_mediano = analisis.get("rango_vela_mediano", 0.001)

    try:
        # Extraer 1 día entero de velas en 5m para el backtest
        velas = exchange.fetch_ohlcv(symbol, "5m", limit=288)
        if not velas or len(velas) < 100:
            return {"symbol": symbol, "pnl_neto": -999.0}
            
        df = pd.DataFrame(velas, columns=["timestamp", "open", "high", "low", "close", "volume"])
        
        for modo in modos_estrategia:
            for lev in variaciones_apalancamiento:
                for num_lin in variaciones_lineas:
                    
                    # Lógica Juan: Si aumento el apalancamiento, busco distancias más pegadas
                    if lev == 20:
                        multiplicadores_distancia = [0.4, 0.6, 0.8] 
                    elif lev == 15:
                        multiplicadores_distancia = [0.8, 1.0, 1.2]
                    else: # 10x
                        multiplicadores_distancia = [1.2, 1.5, 2.0]
                        
                    for mult_dist in multiplicadores_distancia:
                        
                        # Garantizamos que NUNCA probará algo que dé pérdidas por comisiones
                        espaciado_pct = max(min_spread_rentable, rango_vela_mediano * mult_dist)

                        params_prueba = {
                            "modo": modo,
                            "apalancamiento": lev,
                            "num_grids": num_lin,
                            "espaciado_pct": espaciado_pct
                        }

                        res = _simular_grid_dinamico(
                            df=df, 
                            params=params_prueba, 
                            capital_total=capital_total, 
                            fee_maker=fee_maker, 
                            fee_taker=fee_taker
                        )
                        
                        # Guardar el ganador absoluto de esta moneda
                        if res["pnl_neto"] > -999.0:
                            if mejor_resultado_global is None or res["pnl_neto"] > mejor_resultado_global["pnl_neto"]:
                                res.update({
                                    "symbol": symbol,
                                    "modo_optimo": modo,
                                    "apalancamiento_usado": lev,
                                    "num_grids": num_lin,
                                    "espaciado_pct": espaciado_pct,
                                    "params_optimos": params_prueba,
                                    "analisis_original": analisis
                                })
                                mejor_resultado_global = res

    except Exception as e:
        logger.warning(f"  [BT] Error probando {symbol}: {e}")

    if mejor_resultado_global and mejor_resultado_global["pnl_neto"] > 0:
        logger.info(
            "  [BT-WIN] %-15s PnL=$%6.2f | Modo:%-7s | Lev:%2dx | Lineas:%2d | Dist:%.3f%% | Ops:%3d",
            symbol, mejor_resultado_global["pnl_neto"], mejor_resultado_global["modo_optimo"], 
            mejor_resultado_global["apalancamiento_usado"], mejor_resultado_global["num_grids"], 
            mejor_resultado_global["espaciado_pct"]*100, mejor_resultado_global["operaciones"]
        )
        return mejor_resultado_global
        
    return {"symbol": symbol, "pnl_neto": -999.0}

def backtest_grid_top(exchange: Any, top_analisis: List[Dict[str, Any]], capital: float, leverage: float = None, ia_overrides: Dict[str, Any] = None, slippage_pct: float = 0.0) -> List[Dict[str, Any]]:
    resultados = []
    
    # Hilos en paralelo para no bloquear el bot
    with ThreadPoolExecutor(max_workers=5) as executor:
        futs = {
            executor.submit(_backtest_grid_simbolo, exchange, analisis, capital): analisis["symbol"]
            for analisis in top_analisis
        }
        for fut in as_completed(futs):
            res = fut.result()
            if res.get("pnl_neto", -999.0) != -999.0:
                res["timeframe"] = "5m"
                res["modo"] = res["modo_optimo"]
                res["apalancamiento"] = res["apalancamiento_usado"]
                resultados.append(res)
                
    # Ordena de mayor ganancia a menor ganancia
    resultados.sort(key=lambda x: x["pnl_neto"], reverse=True)
    return resultados