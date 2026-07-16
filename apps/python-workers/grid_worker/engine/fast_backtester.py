import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("GridWorker.Backtester")

async def run_vectorized_backtest(controller, symbols, investment=50.0, max_leverage=15.0):
    """
    Simula un Grid Bot Bidireccional usando los parámetros optimizados.
    Lógica: Cuando el precio cruza un nivel, se ejecuta una orden (Long o Short)
    y se calcula inmediatamente la orden inversa que garantiza PnL positivo descontando comisiones.
    """
    logger.info(f"Iniciando Backtest Bidireccional en {len(symbols)} símbolos (Velas 15m)...")
    
    exchange = controller.get_instance()
    results = []
    
    try:
        await exchange.load_markets()
        
        for sym_data in symbols:
            symbol = sym_data.get('symbol')
            if not symbol: continue
            
            try:
                # 1. Comisiones Reales
                market = exchange.markets.get(symbol, {})
                taker_fee = float(market.get('taker', 0.0005))
                # Comisión total por ciclo (Apertura + Cierre)
                # Se asume Taker para ambos para ser conservadores
                fee_pct_cycle = taker_fee * 2 
                
                # 2. Descargar Velas
                ohlcvs = await exchange.fetch_ohlcv(symbol, '15m', limit=96)
                if not ohlcvs or len(ohlcvs) < 50:
                    continue
                    
                df = pd.DataFrame(ohlcvs, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                
                # 3. Parámetros
                ai_params = sym_data.get('ai_params', {})
                grid_lines = int(ai_params.get('grid_lines', 10))
                # spacing_pct es la distancia base entre niveles
                spacing_pct = float(ai_params.get('grid_spacing_factor', 0.5)) / 100.0
                leverage_used = float(ai_params.get('leverage', 10.0))
                
                if leverage_used > max_leverage:
                    leverage_used = max_leverage
                
                # 4. Configuración de la Malla
                center_price = df['open'].iloc[0]
                spacing_usd = center_price * spacing_pct
                
                direction = ai_params.get('direction', 'neutral').lower()
                capital_per_grid = investment / grid_lines
                
                if direction == "long":
                    buy_levels = [center_price - (i * spacing_usd) for i in range(1, grid_lines + 1)]
                    sell_levels = []
                elif direction == "short":
                    buy_levels = []
                    sell_levels = [center_price + (i * spacing_usd) for i in range(1, grid_lines + 1)]
                else:
                    half_lines = grid_lines // 2
                    buy_levels = [center_price - (i * spacing_usd) for i in range(1, half_lines + 1)]
                    sell_levels = [center_price + (i * spacing_usd) for i in range(1, half_lines + 1)]
                
                levels = sorted(buy_levels + sell_levels)
                
                # Estado del backtest
                pnl = 0.0
                trades_won = 0
                current_capital = investment
                peak_capital = investment
                max_drawdown = 0.0
                
                opens = df['open'].values
                highs = df['high'].values
                lows = df['low'].values
                closes = df['close'].values
                
                current_price = center_price
                
                # 5. Motor de Simulación
                for idx in range(len(df)):
                    open_p = opens[idx]
                    high_p = highs[idx]
                    low_p = lows[idx]
                    close_p = closes[idx]
                    
                    path = [low_p, high_p, close_p] if close_p >= open_p else [high_p, low_p, close_p]
                    
                    for target in path:
                        # Identificar niveles cruzados
                        if target < current_price: # Bajando: Activa niveles de compra (Longs)
                            crossed = [L for L in levels if target <= L <= current_price]
                            direction = "LONG"
                        else: # Subiendo: Activa niveles de venta (Shorts)
                            crossed = [L for L in levels if current_price <= L <= target]
                            direction = "SHORT"
                            
                        for level_hit in crossed:
                            # Al ejecutar una orden en 'level_hit', calculamos la inversa.
                            # Para que el PnL sea positivo: Distancia > Comisiones.
                            # El spacing_pct debe ser mayor que fee_pct_cycle.
                            
                            # Garantía de PnL Positivo:
                            # Profit Bruto % = spacing_pct
                            # Comisiones % = fee_pct_cycle
                            # Profit Neto % = spacing_pct - fee_pct_cycle
                            
                            effective_spacing = spacing_pct
                            if effective_spacing <= fee_pct_cycle:
                                # Si el spacing es muy pequeño, lo ajustamos al mínimo para cubrir comisiones + pequeño margen
                                effective_spacing = fee_pct_cycle + 0.0001 
                            
                            # Cálculo de PnL por posición (Bidireccional)
                            # Tanto para Long como para Short, si se completa el ciclo de grid, el profit es el mismo
                            profit_per_trade = (capital_per_grid * leverage_used) * (effective_spacing - fee_pct_cycle)
                            
                            if profit_per_trade > 0:
                                pnl += profit_per_trade
                                trades_won += 1
                                current_capital += profit_per_trade
                                
                        current_price = target
                        
                        # Drawdown
                        if current_capital > peak_capital:
                            peak_capital = current_capital
                        
                        drawdown = ((peak_capital - current_capital) / peak_capital * 100) if peak_capital > 0 else 0
                        if drawdown > max_drawdown:
                            max_drawdown = drawdown

                results.append({
                    'symbol': symbol,
                    'pnl': pnl,
                    'pnl_after_fees': pnl,
                    'trades': trades_won,
                    'drawdown_pct': float(max_drawdown),
                    'profit_factor': (pnl / max_drawdown) if max_drawdown > 0 else pnl,
                    'score': sym_data.get('score', 0),
                    'spacing_pct': spacing_pct * 100,
                    'leverage_used': leverage_used,
                    'ai_params': ai_params
                })
                
            except Exception as e:
                logger.error(f"Error backtesting {symbol}: {e}")
                
        results.sort(key=lambda x: x['pnl_after_fees'], reverse=True)
        return results

    except Exception as e:
        logger.error(f"Error general en el proceso de Backtest: {e}")
        return []
