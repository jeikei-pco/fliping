import asyncio
import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import logging
from .math_core import calculate_cv
from .net_utils import patch_ccxt_resolver

logger = logging.getLogger("GridWorker.Screener")


async def scan_all_usdt_futures(exchange_id, api_key, secret, passphrase, sandbox=True, timeframe="15m", limit=288):
    """
    Escanea todos los mercados Swap USDT en el exchange especificado DIRECTAMENTE DESDE EL EXCHANGE.
    limit=288 representa 24 horas en velas de 5 minutos.
    Devuelve los símbolos ordenados por el Score de Promesa (Tamaño, Calidad y Predictibilidad).
    """
    from .okx_ws import _create_exchange
    modo_str = "DEMO/SANDBOX" if sandbox else "REAL"
    logger.info(f"Iniciando Screener Global en {exchange_id} (Modo {modo_str})...")
    
    exchange = _create_exchange(exchange_id, api_key, secret, passphrase, sandbox)
        
    try:
        # 1. SIEMPRE DESCARGAR DESDE EL EXCHANGE
        try:
            await exchange.load_markets()
        except Exception as e:
            logger.error(f"Fallo al cargar mercados en el Screener: {e}")
            return []
        
        # 2. FILTRO TRIPLE ESTRICTO PARA OKX
        symbols = []
        for symbol, market in exchange.markets.items():
            is_swap = market.get('swap') == True
            is_active = market.get('active') == True
            is_usdt_settled = market.get('settle') == 'USDT'
            
            if is_swap and is_active and is_usdt_settled:
                symbols.append(symbol)
                
        logger.info(f"Encontrados {len(symbols)} mercados Swap USDT 100% operables en {modo_str}.")
        
        # 3. Escaneo por lotes
        batch_size = 20
        results = []
        
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i+batch_size]
            tasks = []
            
            for symbol in batch:
                tasks.append(fetch_and_calculate_cv(exchange, symbol, timeframe, limit))
                
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for res in batch_results:
                if isinstance(res, dict) and res.get('score') is not None:
                    results.append(res)
                    
            logger.info(f"Progreso Screener: {min(i+batch_size, len(symbols))}/{len(symbols)}")
            await asyncio.sleep(0.5) 
            
        logger.info(f"Símbolos escaneados: {len(results)}")
        
        # 4. Ordenamiento por SCORE (Los más prometedores primero)
        valid_results = results
        valid_results.sort(key=lambda x: x.get('score', 0), reverse=True)
        
        logger.info(f"Screener terminado. {len(valid_results)} seleccionados y ordenados por Score.")
        
        return valid_results
        
    finally:
        await exchange.close()


async def fetch_and_calculate_cv(exchange, symbol, timeframe="15m", limit=288):
    try:
        # 1. Obtener límites de OKX para validación nominal
        market = exchange.markets.get(symbol, {})
        contract_size = float(market.get("contractSize", 1.0))
        
        # Descargar Velas
        ohlcvs = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        if not ohlcvs or len(ohlcvs) < 100:
            return {'symbol': symbol, 'score': None}
            
        df = pd.DataFrame(ohlcvs, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # 2. Cálculos de Vela (Tamaño y Calidad)
        df['body_pct'] = abs(df['close'] - df['open']) / df['open'] * 100
        df['range_pct'] = (df['high'] - df['low']) / df['low'] * 100
        
        # ---------------------------------------------------------
        # 🚨 NUEVO: FILTRO ANTI-ANOMALÍAS (PUMP & DUMP / MECHAZOS)
        # ---------------------------------------------------------
        max_range = df['range_pct'].max()
        max_body = df['body_pct'].max()
        median_body = max(df['body_pct'].median(), 0.001) # Evitar división por cero
        
        # Regla A: Si hay una vela que se movió más de 7% en 5 min, es un peligro para el grid.
        if max_range > 7.0:
            return {'symbol': symbol, 'score': None}
            
        # Regla B: Si la vela máxima es 10 veces más grande que la vela típica (mediana), no es constante.
        if (max_body / median_body) > 10.0:
            return {'symbol': symbol, 'score': None}
        # ---------------------------------------------------------
        
        # Calidad: Qué tanto del rango total es cuerpo (evita mechazos)
        df['quality'] = np.where(df['range_pct'] > 0, df['body_pct'] / df['range_pct'], 0)
        
        # 3. Contexto Direccional (EMAs rápidas para 15m)
        df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        precio_actual = df['close'].iloc[-1]
        ema9 = df['ema9'].iloc[-1]
        ema21 = df['ema21'].iloc[-1]
        
        if ema9 > ema21:
            trend = 'long'
        elif ema9 < ema21:
            trend = 'short'
        else:
            trend = 'neutral'
            
        # 4. Métricas Agregadas (Predictibilidad)
        avg_body = df['body_pct'].mean()
        avg_quality = df['quality'].mean()
        std_dev = df['body_pct'].std() + 1e-5  # Evitar división por cero
        
        # 5. SCORE: (Tamaño * Calidad) / Volatilidad Errática
        score = (avg_body * avg_quality) / std_dev
        
        return {
            'symbol': symbol,
            'score': float(score),
            'avg_body_pct': float(avg_body),
            'quality': float(avg_quality),
            'std_dev': float(std_dev),
            'trend': trend,
            'precio_actual': float(precio_actual)
        }
    except Exception as e:
       logger.error(f"Error al analizar el símbolo {symbol}: {e}")
    return {'symbol': symbol, 'score': None}
