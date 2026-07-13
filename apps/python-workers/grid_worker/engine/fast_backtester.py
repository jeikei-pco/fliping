import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import logging
from .net_utils import patch_ccxt_resolver

logger = logging.getLogger("GridWorker.Backtester")

async def run_vectorized_backtest(api_key, secret, passphrase, symbols, sandbox=True, investment=1000, max_leverage=15.0):
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
                
                # 3. Velas de 5m (288 velas = 24 horas)
                ohlcvs = await exchange.fetch_ohlcv(symbol, '5m', limit=288)
                
                # Relajamos a 200 velas para evitar descartes masivos en Testnet
                if not ohlcvs or len(ohlcvs) < 200:
                    continue
                    
                df = pd.DataFrame(ohlcvs, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                center_price = df.iloc[0]['close']
                
                # 4. Leer parámetros de IA
                # 4. Leer parámetros de IA
                ai_params = sym_data.get('ai_params', {})
                grid_spacing_factor = float(ai_params.get('grid_spacing_factor', sym_data.get('avg_body_pct', 0.5)))
                spacing_pct = grid_spacing_factor / 100.0
                
                # Piso de seguridad: el spacing no puede ser menor a la comisión + margen
                if spacing_pct <= fee_pct:
                    spacing_pct = fee_pct + 0.0005
                    
                grid_spacing = center_price * spacing_pct
                
                # --- NUEVA LÓGICA SIMÉTRICA 50/50 ---
                total_grid_lines = int(ai_params.get('grid_lines', 10))
                half_lines = total_grid_lines // 2  # Si la IA pide 10, usa 5 por lado
                
                leverage_used = float(ai_params.get('leverage', max_leverage))
                
                # Construimos los niveles desde -half_lines hasta +half_lines
                levels = [center_price + (i * grid_spacing) for i in range(-half_lines, half_lines + 1)]
                
                # 5. Variables de Simulación
                pnl = 0.0
                max_drawdown = 0.0
                peak_capital = investment
                current_capital = investment
                
                # El capital de inversión se divide entre el TOTAL de líneas que pidió la IA
                capital_per_grid = investment / total_grid_lines 
                current_level_idx = half_lines # El centro exacto
                trades_won = 0
                
                # 6. Lógica 100% Neutral
                for row in df.itertuples():
                    high = row.high
                    low = row.low
                    
                    # Verificamos si tocamos el nivel de arriba
                    if current_level_idx < len(levels) - 1:
                        upper_level = levels[current_level_idx + 1]
                        if high >= upper_level:
                            # Ganancia real descontando la comisión exacta de OKX
                            profit = capital_per_grid * (spacing_pct - fee_pct) * leverage_used
                            pnl += profit
                            current_capital += profit
                            trades_won += 1
                            current_level_idx += 1
                            
                    # Verificamos si tocamos el nivel de abajo
                    if current_level_idx > 0:
                        lower_level = levels[current_level_idx - 1]
                        if low <= lower_level:
                            # Ganancia real descontando la comisión exacta de OKX
                            profit = capital_per_grid * (spacing_pct - fee_pct) * leverage_used
                            pnl += profit
                            current_capital += profit
                            trades_won += 1
                            current_level_idx -= 1
                            
                    # Cálculo de Drawdown
                    if current_capital > peak_capital:
                        peak_capital = current_capital
                        
                    if peak_capital > 0:
                        drawdown = (peak_capital - current_capital) / peak_capital * 100
                    else:
                        drawdown = 0
                        
                    if drawdown > max_drawdown:
                        max_drawdown = drawdown

                pnl_after_fees = pnl # Ya descontamos el fee en el ciclo
                
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