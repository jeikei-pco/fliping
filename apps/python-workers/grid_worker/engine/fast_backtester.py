import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import logging
from .net_utils import patch_ccxt_resolver

logger = logging.getLogger("GridWorker.Backtester")

async def run_vectorized_backtest(exchange_id, api_key, secret, passphrase, symbols, sandbox=True, investment=50.0, max_leverage=15.0):
    """
    Simula un Grid Bot usando los parámetros optimizados.
    Lógica estricta: Entry -> Crea TP -> Toca TP -> Crea Entry.
    """
    from .okx_ws import _create_exchange
    logger.info(f"Iniciando Backtest en {len(symbols)} símbolos (Velas 5m)...")
    
    exchange = _create_exchange(exchange_id, api_key, secret, passphrase, sandbox)
        
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
                fee_pct = taker_fee * 2 # Compra + Venta
                
                # 2. Descargar Velas (8 horas = 96 velas de 5m)
                ohlcvs = await exchange.fetch_ohlcv(symbol, '5m', limit=96)
                if not ohlcvs or len(ohlcvs) < 10:
                    continue
                    
                df = pd.DataFrame(ohlcvs, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                center_price = df.iloc[0]['close']
                
                # 3. Consumir Datos del Optimizador / Screener
                ai_params = sym_data.get('ai_params', {})
                
                # Spacing
                grid_spacing_factor = float(ai_params.get('grid_spacing_factor', sym_data.get('avg_body_pct', 0.5)))
                spacing_pct = grid_spacing_factor / 100.0
                if spacing_pct <= fee_pct:
                    spacing_pct = fee_pct + 0.0005 # Piso de seguridad
                grid_spacing = center_price * spacing_pct
                
                # Líneas y Apalancamiento
                total_grid_lines = int(ai_params.get('grid_lines', 10))
                half_lines = total_grid_lines // 2
                leverage_used = float(ai_params.get('leverage', max_leverage))
                direction = ai_params.get('direction', sym_data.get('trend', 'neutral'))
                
                # 4. Construir Niveles de la Malla
                levels = [center_price + (i * grid_spacing) for i in range(-half_lines, half_lines + 1)]
                
                # Variables de Simulación
                pnl = 0.0
                max_drawdown = 0.0
                peak_capital = investment
                current_capital = investment
                capital_per_grid = investment / total_grid_lines 
                trades_won = 0
                
                # Inicializar Órdenes (Resolviendo el limbo del Center Price)
                orders = {}
                for i, L in enumerate(levels):
                    if L < center_price:
                        if direction in ['neutral', 'long']: orders[i] = 'open_long'
                    elif L > center_price:
                        if direction in ['neutral', 'short']: orders[i] = 'open_short'
                    else: # L == center_price
                        if direction == 'short':
                            orders[i] = 'open_short'
                        else:
                            orders[i] = 'open_long'
                
                # 5. Motor de Simulación (Tick por Tick simulado)
                current_price = center_price
                for row in df.itertuples():
                    open_p, high_p, low_p, close_p = row.open, row.high, row.low, row.close
                    
                    # NOTA: Heurística estándar OHLC. Asume un sesgo optimista de ~2% en PnL.
                    path = [low_p, high_p, close_p] if close_p >= open_p else [high_p, low_p, close_p]
                        
                    for target in path:
                        if target < current_price: # El precio BAJA
                            crossed_levels = [i for i, L in enumerate(levels) if target <= L <= current_price]
                            crossed_levels.sort(reverse=True) # De arriba hacia abajo
                            
                            for i in crossed_levels:
                                if i in orders:
                                    if orders[i] == 'open_long':
                                        del orders[i]
                                        if i + 1 < len(levels):
                                            orders[i+1] = 'close_long'
                                            
                                    elif orders[i] == 'close_short':
                                        del orders[i]
                                        profit = capital_per_grid * (spacing_pct - fee_pct) * leverage_used
                                        pnl += profit
                                        current_capital += profit
                                        trades_won += 1
                                        if i + 1 < len(levels):
                                            orders[i+1] = 'open_short'
                                            
                        elif target > current_price: # El precio SUBE
                            crossed_levels = [i for i, L in enumerate(levels) if current_price <= L <= target]
                            crossed_levels.sort() # De abajo hacia arriba
                            
                            for i in crossed_levels:
                                if i in orders:
                                    if orders[i] == 'open_short':
                                        del orders[i]
                                        if i - 1 >= 0:
                                            orders[i-1] = 'close_short'
                                            
                                    elif orders[i] == 'close_long':
                                        del orders[i]
                                        profit = capital_per_grid * (spacing_pct - fee_pct) * leverage_used
                                        pnl += profit
                                        current_capital += profit
                                        trades_won += 1
                                        if i - 1 >= 0:
                                            orders[i-1] = 'open_long'
                                            
                        current_price = target
                        
                    # 6. Cálculo de Drawdown (Flotante Negativo Realista con Fee x2)
                    upnl = 0
                    for i, type_ in orders.items():
                        if type_ == 'close_long': 
                            entry_price = levels[i-1]
                            upnl += capital_per_grid * leverage_used * ((close_p - entry_price) / entry_price) - (capital_per_grid * leverage_used * taker_fee * 2)
                        elif type_ == 'close_short': 
                            entry_price = levels[i+1]
                            upnl += capital_per_grid * leverage_used * ((entry_price - close_p) / entry_price) - (capital_per_grid * leverage_used * taker_fee * 2)
                            
                    total_equity = current_capital + upnl
                    if total_equity > peak_capital:
                        peak_capital = total_equity
                    
                    drawdown = ((peak_capital - total_equity) / peak_capital * 100) if peak_capital > 0 else 0
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
                
        # 7. Ordenar por PnL Neto
        results.sort(key=lambda x: x['pnl_after_fees'], reverse=True)
        return results
        
    finally:
        await exchange.close()
