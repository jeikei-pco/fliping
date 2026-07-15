import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import logging
from .net_utils import patch_ccxt_resolver

logger = logging.getLogger("GridWorker.Backtester")

async def run_vectorized_backtest(api_key, secret, passphrase, symbols, sandbox=True, investment=50.0, max_leverage=15.0):
    """
    Toma una lista de símbolos, descarga sus últimas 24h en velas de 5 minutos,
    y simula un Grid NEUTRAL descontando comisiones REALES del exchange.
    """
    logger.info(f"Iniciando Backtest en {len(symbols)} símbolos (Velas 5m)...")
    
    exchange = ccxt.okx({
        'apiKey': api_key,
        'secret': secret,
        'password': passphrase,
        'enableRateLimit': True,
        'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        },
        'options': {
            'defaultType': 'swap',
            'fetchMarkets': ['swap']
        }
    })
    
    if sandbox:
        exchange.set_sandbox_mode(True)
        
    patch_ccxt_resolver(exchange)
        
    results = []
    
    try:
        # 1. Cargar mercados para obtener las comisiones reales de la cuenta/exchange
        await exchange.load_markets()
        
        for sym_data in symbols:
            symbol = sym_data['symbol']
            
            try:
                # 2. Extraer Taker Fee dinámico del símbolo
                market = exchange.markets.get(symbol, {})
                # Extraemos el 'taker' fee. Fallback a 0.0005 (0.05%) si la API no lo reporta
                taker_fee = float(market.get('taker') if market.get('taker') is not None else 0.0005)
                # Multiplicamos por 2 para obtener el costo del ciclo completo (compra + venta)
                fee_pct = taker_fee * 2 
                
                # 3. Velas de 5m (8 horas = 96 velas)
                ohlcvs = await exchange.fetch_ohlcv(symbol, '5m', limit=96)
                
                # Relajamos a 10 velas para evitar descartes masivos en Testnet
                if not ohlcvs or len(ohlcvs) < 10:
                    continue
                    
                df = pd.DataFrame(ohlcvs, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                center_price = df.iloc[0]['close']
                
                # 4. Leer parámetros de IA
                ai_params = sym_data.get('ai_params', {})
                grid_spacing_factor = float(ai_params.get('grid_spacing_factor', sym_data.get('avg_body_pct', 0.5)))
                spacing_pct = grid_spacing_factor / 100.0
                
                # Piso de seguridad: el spacing no puede ser menor a la comisión + margen
                if spacing_pct <= fee_pct:
                    spacing_pct = fee_pct + 0.0005
                    
                grid_spacing = center_price * spacing_pct
                
                total_grid_lines = int(ai_params.get('grid_lines', 10))
                half_lines = total_grid_lines // 2  # Si la IA pide 10, usa 5 por lado
                
                leverage_used = float(ai_params.get('leverage', max_leverage))
                direction = ai_params.get('direction', sym_data.get('trend', 'neutral'))
                
                # Construimos los niveles desde -half_lines hasta +half_lines
                levels = [center_price + (i * grid_spacing) for i in range(-half_lines, half_lines + 1)]
                
                # 5. Variables de Simulación
                pnl = 0.0
                max_drawdown = 0.0
                peak_capital = investment
                current_capital = investment
                
                capital_per_grid = investment / total_grid_lines 
                trades_won = 0
                
                orders = {}
                for i, L in enumerate(levels):
                    if L < center_price:
                        if direction in ['neutral', 'long']:
                            orders[i] = ('buy', 'open_long')
                    elif L > center_price:
                        if direction in ['neutral', 'short']:
                            orders[i] = ('sell', 'open_short')
                
                # 6. Lógica de Simulación de Grid Realista
                current_price = center_price
                for row in df.itertuples():
                    open_p, high_p, low_p, close_p = row.open, row.high, row.low, row.close
                    
                    if close_p >= open_p:
                        path = [low_p, high_p, close_p]
                    else:
                        path = [high_p, low_p, close_p]
                        
                    for target in path:
                        if target < current_price:
                            crossed_levels = [i for i, L in enumerate(levels) if target <= L <= current_price]
                            crossed_levels.sort(reverse=True)
                            for i in crossed_levels:
                                if i in orders and orders[i][0] == 'buy':
                                    _, type_ = orders[i]
                                    del orders[i]
                                    if type_ == 'close_short':
                                        profit = capital_per_grid * (spacing_pct - fee_pct) * leverage_used
                                        pnl += profit
                                        current_capital += profit
                                        trades_won += 1
                                        if direction in ['neutral', 'short'] and i+1 < len(levels):
                                            orders[i+1] = ('sell', 'open_short')
                                    elif type_ == 'open_long':
                                        if i+1 < len(levels):
                                            orders[i+1] = ('sell', 'close_long')
                        elif target > current_price:
                            crossed_levels = [i for i, L in enumerate(levels) if current_price <= L <= target]
                            crossed_levels.sort()
                            for i in crossed_levels:
                                if i in orders and orders[i][0] == 'sell':
                                    _, type_ = orders[i]
                                    del orders[i]
                                    if type_ == 'close_long':
                                        profit = capital_per_grid * (spacing_pct - fee_pct) * leverage_used
                                        pnl += profit
                                        current_capital += profit
                                        trades_won += 1
                                        if direction in ['neutral', 'long'] and i-1 >= 0:
                                            orders[i-1] = ('buy', 'open_long')
                                    elif type_ == 'open_short':
                                        if i-1 >= 0:
                                            orders[i-1] = ('buy', 'close_short')
                        current_price = target
                        
                    # Drawdown tracking con Unrealized PnL
                    upnl = 0
                    for i, (action, type_) in orders.items():
                        if type_ == 'close_long':
                            entry_price = levels[i-1]
                            upnl += capital_per_grid * leverage_used * ((close_p - entry_price) / entry_price) - (capital_per_grid * leverage_used * taker_fee)
                        elif type_ == 'close_short':
                            entry_price = levels[i+1]
                            upnl += capital_per_grid * leverage_used * ((entry_price - close_p) / entry_price) - (capital_per_grid * leverage_used * taker_fee)
                            
                    total_equity = current_capital + upnl
                    if total_equity > peak_capital:
                        peak_capital = total_equity
                    if peak_capital > 0:
                        drawdown = (peak_capital - total_equity) / peak_capital * 100
                    else:
                        drawdown = 0
                    if drawdown > max_drawdown:
                        max_drawdown = drawdown

                pnl_after_fees = pnl
                
                results.append({
                    'symbol': symbol,
                    'pnl': pnl,
                    'pnl_after_fees': pnl_after_fees,
                    'trades': trades_won,
                    'drawdown_pct': float(max_drawdown),
                    'profit_factor': (pnl / max_drawdown) if max_drawdown > 0 else pnl,
                    'cv': sym_data.get('cv', 1.0),
                    'spacing_pct': spacing_pct * 100,
                    'leverage_used': leverage_used,
                    'ai_params': ai_params
                })
                
            except Exception as e:
                logger.error(f"Error backtesting {symbol}: {e}")
                
        # 7. Ordenar estrictamente por PnL Neto
        results.sort(key=lambda x: x['pnl_after_fees'], reverse=True)
        return results
        
    finally:
        await exchange.close()