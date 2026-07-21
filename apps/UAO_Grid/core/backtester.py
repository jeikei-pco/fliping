"""
backtester.py - Backtest de Malla (Grid) por Fuerza Bruta.
Simula el comportamiento Multidireccional (Neutral), Long y Short.
Aplica la relación entre Alto Apalancamiento = Menor Distancia.
"""
import logging
import time
import pandas as pd
from typing import Any, Dict, List, Optional
from core.models import ValidatedOptimizationProfile
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("UAO_Sclaping.GridBacktester")

WINDOW_24H_MS = 24 * 60 * 60 * 1000
FIVE_MINUTES_MS = 5 * 60 * 1000
ONE_SECOND_MS = 1000
FIVE_MIN_24H_LIMIT = 24 * 60 // 5
ONE_SEC_24H_LIMIT = 24 * 60 * 60
MAX_5M_STALENESS_MS = 10 * 60 * 1000
MAX_1S_STALENESS_MS = 5 * 60 * 1000


def _normalizar_analisis_backtest(analisis: Dict[str, Any]) -> Dict[str, Any]:
    from core.optimizador import _normalizar_analisis

    return _normalizar_analisis(analisis)


def _ohlcv_to_df(velas: Any) -> pd.DataFrame:
    if isinstance(velas, pd.DataFrame):
        return velas.copy()
    if not velas:
        return pd.DataFrame()
    return pd.DataFrame(velas, columns=["timestamp", "open", "high", "low", "close", "volume"])


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
    if espaciado <= 0 or apalancamiento <= 0 or num_grids_total <= 0:
        return {"operaciones": 0, "pnl_neto": -999.0}
    
    # Lógica de distribución de capital
    # En modo NEUTRAL (mitad arriba, mitad abajo), el capital máximo en riesgo 
    # es solo el de un lado a la vez.
    num_lineas_lado = max(1, num_grids_total // 2) if modo == "NEUTRAL" else num_grids_total
    
    capital_por_linea = capital_total / num_lineas_lado
    tamano_orden = capital_por_linea * apalancamiento
    
    # Costo de abrir + Costo de cerrar + Margen de ganancia neto deseado
    margen_neto_minimo = float(params.get("min_profit_pct", 0.0010))
    costos_totales_pct = float(params.get("fee_round_trip_pct", fee_maker + fee_taker))
    pnl_neto_trade_pct = espaciado - costos_totales_pct
    
    # Si la distancia no cubre los costos y el margen, descartar inmediatamente
    if pnl_neto_trade_pct < margen_neto_minimo:
        return {"operaciones": 0, "pnl_neto": -999.0}
        
    pnl_neto_fiat = tamano_orden * pnl_neto_trade_pct
    
    pnl_acumulado = 0.0
    max_equity = 0.0
    max_drawdown = 0.0
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
                        max_equity = max(max_equity, pnl_acumulado)
                        max_drawdown = max(max_drawdown, max_equity - pnl_acumulado)
                        operaciones += 1
                        posicion_neta -= 1
                    elif posicion_neta > -num_lineas_lado: # Estamos del centro hacia arriba, abrimos SHORT
                        posicion_neta -= 1
                        
                elif modo == "LONG":
                    if posicion_neta > 0: # Cobramos ganancia del LONG
                        pnl_acumulado += pnl_neto_fiat
                        max_equity = max(max_equity, pnl_acumulado)
                        max_drawdown = max(max_drawdown, max_equity - pnl_acumulado)
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
                        max_equity = max(max_equity, pnl_acumulado)
                        max_drawdown = max(max_drawdown, max_equity - pnl_acumulado)
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
                        max_equity = max(max_equity, pnl_acumulado)
                        max_drawdown = max(max_drawdown, max_equity - pnl_acumulado)
                        operaciones += 1
                        posicion_neta += 1

    return {
        "operaciones": operaciones,
        "pnl_neto": pnl_acumulado,
        "espaciado_pct": espaciado,
        "roi_pct": round((pnl_acumulado / capital_total) * 100, 4) if capital_total else 0.0,
        "drawdown": round(max_drawdown, 6),
        "win_rate": 100.0 if operaciones > 0 and pnl_acumulado > 0 else 0.0,
        "profit_factor": 99.0 if pnl_acumulado > 0 else 0.0,
    }


def _normalizar_resultado_backtest(
    res: Dict[str, Any],
    *,
    symbol: str,
    params: Dict[str, Any],
    analisis: Dict[str, Any],
    source: str,
    ai_overrides: Optional[Dict[str, Any]] = None,
    capital_total: float,
    fee_maker: float,
    fee_taker: float,
) -> ValidatedOptimizationProfile:
    modo = str(params.get("modo", "NEUTRAL")).upper()
    apalancamiento = int(params.get("apalancamiento", params.get("apalancamiento_usado", 1)))
    num_grids = int(params.get("num_grids", 0))
    espaciado_pct = float(params.get("espaciado_pct", res.get("espaciado_pct", 0.0)))

    optimization_params = {
        "grid_spacing_pct": espaciado_pct,
        "grid_lines": num_grids,
        "capital": capital_total, # Necesitamos pasar capital_total a esta función
        "leverage": apalancamiento,
        "min_profit_pct": params.get("min_profit_pct", 0.0010),
        "maker_fee": fee_maker, # Necesitamos pasar fee_maker y fee_taker
        "taker_fee": fee_taker,
        "preferred_mode": modo,
        "rebalance_distance": params.get("rebalance_distance", 0.0), # Asumir default por ahora
        "inventory_limit": params.get("inventory_limit", 0), # Asumir default por ahora
        "max_orders": params.get("max_orders", 0), # Asumir default por ahora
        "grid_direction": params.get("grid_direction", "NEUTRAL"), # Asumir default por ahora
    }

    backtest_metrics = {
        "ROI": res.get("roi_pct", 0.0),
        "PnL": res.get("pnl_neto", 0.0),
        "Drawdown": res.get("drawdown", 0.0),
        "ProfitFactor": res.get("profit_factor", 0.0),
        "WinRate": res.get("win_rate", 0.0),
        "Trades": res.get("operaciones", 0),
        "Expectancy": params.get("expectancy", 0.0), # Asumir default por ahora
        "Sharpe": params.get("sharpe", 0.0), # Asumir default por ahora
        "Calmar": params.get("calmar", 0.0), # Asumir default por ahora
    }

    analysis_metrics = {
        "atr": analisis.get("atr", 0.0),
        "atr_pct": analisis.get("atr_pct", 0.0),
        "volatility": analisis.get("volatility", 0.0),
        "risk": analisis.get("riesgo", 0.0),
        "trend": analisis.get("deriva_pct", 0.0),
        "grid_quality": analisis.get("grid_quality", 0.0),
        "market_phase": analisis.get("market_phase", "UNKNOWN"),
        "oscillation": analisis.get("oscilacion", 0.0),
        "score": analisis.get("score", 0.0),
    }

    metadata = {
        "source": source,
        "ai_overrides": ai_overrides or {},
        "recent_activity": res.get("recent_activity", {}),
    }

    return ValidatedOptimizationProfile(
        symbol=symbol,
        analysis=analysis_metrics,
        optimization=optimization_params,
        backtest=backtest_metrics,
        metadata=metadata,
    )



def _backtest_configuracion(
    exchange: Any,
    analisis: Dict[str, Any],
    capital_total: float,
    params: Dict[str, Any],
    *,
    source: str = "CONFIG",
    ai_overrides: Optional[Dict[str, Any]] = None,
    df: Optional[pd.DataFrame] = None,
) -> ValidatedOptimizationProfile:
    analisis = _normalizar_analisis_backtest(analisis)
    symbol = analisis["symbol"]
    market = exchange.markets.get(symbol, {}) if hasattr(exchange, 'markets') and exchange.markets else {}
    fee_maker = float(market.get("maker", 0.00020))
    fee_taker = float(market.get("taker", 0.00050))

    if not params or not params.get("valido", True):
        return ValidatedOptimizationProfile(
            symbol=symbol,
            analysis=analisis,
            optimization=params or {},
            backtest={"PnL": -999.0},
            metadata={"source": source}
        )

    try:
        if df is None:
            df = analisis.get("df_5m")
        if df is None or df.empty:
            return ValidatedOptimizationProfile(
                symbol=symbol,
                analysis=analisis,
                optimization=params,
                backtest={"PnL": -999.0},
                metadata={"source": source}
            )

        params_prueba = {
            "modo": str(params.get("modo", "NEUTRAL")).upper(),
            "apalancamiento": int(params.get("apalancamiento", 1)),
            "num_grids": int(params.get("num_grids", 4)),
            "espaciado_pct": float(params.get("espaciado_pct", 0.0)),
            "fee_round_trip_pct": float(params.get("fee_round_trip_pct", fee_maker + fee_taker)),
            "min_profit_pct": float(params.get("min_profit_pct", 0.0010)),
        }
        res = _simular_grid_dinamico(df, params_prueba, capital_total, fee_maker, fee_taker)
        if res.get("pnl_neto", -999.0) == -999.0:
            return ValidatedOptimizationProfile(
                symbol=symbol,
                analysis=analisis,
                optimization=params,
                backtest={"PnL": -999.0},
                metadata={"source": source}
            )

        resultado = _normalizar_resultado_backtest(
            res,
            symbol=symbol,
            params={**params, **params_prueba},
            analisis=analisis,
            source=source,
            ai_overrides=ai_overrides,
            capital_total=capital_total,
            fee_maker=fee_maker,
            fee_taker=fee_taker,
        )
        # El objeto ValidatedOptimizationProfile ya contiene recent_activity en metadata
        if resultado.backtest.get("PnL", -999.0) > 0:
            logger.info(
                "  [BT-WIN-%s] %-15s PnL=$%6.2f | Modo:%-7s | Lev:%2dx | Lineas:%2d | Dist:%.3f%% | Ops:%3d | Ventana:24h",
                source,
                symbol,
                resultado["pnl_neto"],
                resultado["modo_optimo"],
                resultado["apalancamiento_usado"],
                resultado["num_grids"],
                resultado["espaciado_pct"] * 100,
                resultado["operaciones"],
            )
        return resultado
    except Exception as e:
        logger.warning(f"  [BT-{source}] Error probando {symbol}: {e}")
        return ValidatedOptimizationProfile(
            symbol=symbol,
            analysis=analisis,
            optimization=params or {},
            backtest={"PnL": -999.0},
            metadata={"source": source}
        )


def _backtest_con_optimizador(
    exchange: Any,
    analisis: Dict[str, Any],
    capital_total: float,
    overrides: Optional[Dict[str, Any]] = None,
    modo: str = "NEUTRAL",
) -> ValidatedOptimizationProfile:
    from core.optimizador import OptimizadorGrid

    analisis = _normalizar_analisis_backtest(analisis)
    symbol = analisis["symbol"]
    try:
        df = analisis.get("df_5m")
        if df is None or df.empty:
            return ValidatedOptimizationProfile(
                symbol=symbol,
                analysis=analisis,
                backtest={"PnL": -999.0},
                metadata={"source": "OPTIMIZER"}
            )
        params = OptimizadorGrid(overrides=overrides).optimizar_symbol(symbol, df, capital_total, analisis, modo=modo)
        return _backtest_configuracion(
            exchange,
            analisis,
            capital_total,
            params,
            source="AI" if overrides else "MATH",
            ai_overrides=overrides,
            df=df,
        )
    except Exception as e:
        logger.warning(f"  [BT-OPT] Error optimizando {symbol}: {e}")
        return ValidatedOptimizationProfile(
            symbol=symbol,
            analysis=analisis,
            backtest={"PnL": -999.0},
            metadata={"source": "AI" if overrides else "MATH"}
        )

# ==========================================
# 2. ORQUESTADOR DE BACKTEST (Fuerza Bruta Múltiple)
# ==========================================
def _backtest_grid_simbolo(
    exchange: Any,
    analisis: Dict[str, Any],
    capital_total: float,
    overrides: Optional[Dict[str, Any]] = None,
    params_candidatos: Optional[Dict[str, Any]] = None,
) -> ValidatedOptimizationProfile:
    analisis = _normalizar_analisis_backtest(analisis)
    symbol = analisis["symbol"]

    if params_candidatos:
        return _backtest_configuracion(
            exchange,
            analisis,
            capital_total,
            params_candidatos,
            source="CONFIG",
            ai_overrides=overrides,
        )
    if overrides:
        return _backtest_con_optimizador(exchange, analisis, capital_total, overrides=overrides)

    return _backtest_con_optimizador(exchange, analisis, capital_total)

def backtest_grid_top(exchange: Any, top_analisis: List[Dict[str, Any]], capital: float, leverage: float = None, ia_overrides: Dict[str, Any] = None, slippage_pct: float = 0.0) -> List[ValidatedOptimizationProfile]:
    resultados = []
    
    # Hilos en paralelo para no bloquear el bot
    with ThreadPoolExecutor(max_workers=5) as executor:
        futs = {
            executor.submit(
                _backtest_grid_simbolo,
                exchange,
                analisis,
                capital,
                ia_overrides.get(analisis["symbol"]) if ia_overrides else None,
            ): analisis["symbol"]
            for analisis in top_analisis
        }
        for fut in as_completed(futs):
            res = fut.result()
            if res.backtest.get("PnL", -999.0) != -999.0:
                # res.metadata["timeframe"] = "5m" # No es necesario, ya está en analysis
                resultados.append(res)
                
    # Ordena de mayor ganancia a menor ganancia
    resultados.sort(key=lambda x: x.backtest.get("PnL", -999.0), reverse=True)
    return resultados
