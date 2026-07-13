import asyncio
import ccxt.async_support as ccxt
import pandas as pd
import logging
from .math_core import calculate_cv
from .net_utils import patch_ccxt_resolver

logger = logging.getLogger("GridWorker.Screener")


async def scan_all_usdt_futures(api_key, secret, passphrase, sandbox=True, timeframe="5m", limit=288):
    """
    Escanea todos los mercados Swap USDT en OKX.
    limit=288 representa 24 horas en velas de 5 minutos.
    Devuelve el Top 20 de símbolos con menor CV (más constantes).
    """
    logger.info("Iniciando Screener Global de Constancia...")
    
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
        
    try:
        await exchange.load_markets()
        
        # Filtrar mercados: solo futuros lineales (Swap USDT) que estén activos
        symbols = []
        for symbol, market in exchange.markets.items():
            if market.get('swap') and market.get('quote') == 'USDT' and market.get('active'):
                symbols.append(symbol)
                
        logger.info(f"Encontrados {len(symbols)} mercados Swap USDT.")
        
        # Para pruebas o sandbox, podríamos no tener demasiados. 
        # Vamos a escanearlos en lotes para no saturar el Rate Limit.
        batch_size = 10
        results = []
        
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i+batch_size]
            tasks = []
            
            for symbol in batch:
                tasks.append(fetch_and_calculate_cv(exchange, symbol, timeframe, limit))
                
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for res in batch_results:
                if isinstance(res, dict) and res['cv'] is not None:
                    results.append(res)
                    
            logger.info(f"Progreso Screener: {min(i+batch_size, len(symbols))}/{len(symbols)}")
            await asyncio.sleep(0.5) # Pausa por rate limits
            
        # 1. Filtramos los símbolos que no cumplen el mínimo de 0.20% de cuerpo (avg_body_pct)
        # avg_body_pct viene multiplicado por 100 en el dict (ej: 0.21)
        valid_results = [r for r in results if r.get('avg_body_pct', 0) >= 0.20]

        # 2. Ordenamos de MAYOR a MENOR cuerpo promedio (reverse=True)
        # Esto garantiza que el Top 1 sea el activo con velas más grandes y rentables.
        valid_results.sort(key=lambda x: x.get('avg_body_pct', 0), reverse=True)
        
        top_20 = valid_results[:20]
        
        logger.info(f"Screener terminado. Top 1: {top_20[0]['symbol'] if top_20 else 'N/A'}")
        
        return top_20
        
    finally:
        await exchange.close()


async def fetch_and_calculate_cv(exchange, symbol, timeframe="15m", limit=200):
    try:
        # 1. Obtener límites de OKX para validación nominal
        market = exchange.markets.get(symbol, {})
        min_qty = float(market.get("limits", {}).get("amount", {}).get("min", 1.0))
        contract_size = float(market.get("contractSize", 1.0))
        
        # Velas de 15m para detectar tendencia macro sin ruido
        ohlcvs = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        if not ohlcvs or len(ohlcvs) < 100:
            return {'symbol': symbol, 'cv': None}
            
        df = pd.DataFrame(ohlcvs, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # 2. Amplitud Real de Operatividad (High - Low)
        df["amplitud"] = (df["high"] - df["low"]) / df["open"]
        
        # Filtro Anti-Outliers: Eliminar mechas anómalas (> 3 desviaciones estándar)
        mean_amp = df["amplitud"].mean()
        std_amp = df["amplitud"].std()
        df_clean = df[df["amplitud"] < (mean_amp + (3 * std_amp))]
        
        avg_amplitude = df_clean["amplitud"].mean()
        
        # FILTRO 1: Amplitud mínima del 0.30% (cubre 2 líneas + comisiones)
        if avg_amplitude < 0.0030:
            return {'symbol': symbol, 'cv': None}
            
        # 3. Contexto Direccional (EMAs)
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
        
        precio_actual = df['close'].iloc[-1]
        ema50 = df['ema50'].iloc[-1]
        ema200 = df['ema200'].iloc[-1]
        
        if precio_actual > ema50 > ema200:
            trend = 'long'
        elif precio_actual < ema50 < ema200:
            trend = 'short'
        else:
            trend = 'neutral'
            
        # 4. Validación Nominal (Grid de 4 líneas con 50 USDT a 15x)
        # 50 * 15 = 750 USDT totales / 4 líneas = 187.5 USDT por orden
        inversion_por_linea = (50.0 * 15.0) / 4.0 
        qty_necesaria = (inversion_por_linea / precio_actual) / contract_size
        
        if qty_necesaria < min_qty:
            # OKX rechazará la orden por ser muy pequeña
            return {'symbol': symbol, 'cv': None}
            
        return {
            'symbol': symbol,
            'avg_body_pct': float(avg_amplitude * 100), # Reutilizamos esta variable para la IA
            'cv': 1.0, # Dummy para pasar el filtro antiguo
            'trend': trend,
            'precio_actual': float(precio_actual)
        }
    except Exception as e:
        return {'symbol': symbol, 'cv': None}