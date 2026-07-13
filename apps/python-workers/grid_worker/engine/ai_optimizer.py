import asyncio
import time
import logging
import json
import os
import aiohttp

logger = logging.getLogger("GridWorker.AIOptimizer")

# Caché local (en memoria) para evitar saturar rate limits
# Formato: { "SYMBOL": {"params": {...}, "timestamp": 1234567890} }
_ai_cache = {}
CACHE_TTL = 3600  # 1 hora en segundos

async def get_ai_grid_params_batch(batch, user_config):
    """
    Obtiene los parámetros óptimos del grid (vía IA o heurística) para un lote de símbolos.
    Implementa caché local de 1 hora para evitar golpear límites de API.
    
    Retorna un diccionario: { "BTC-USDT-SWAP": { "grid_spacing_factor": 0.5, "grid_lines": 10, "leverage": 15 }, ... }
    """
    results = {}
    symbols_to_fetch = []
    
    current_time = time.time()
    
    # 1. Revisar Caché
    for sym_data in batch:
        symbol = sym_data['symbol']
        
        # Validar en caché
        if symbol in _ai_cache:
            cache_entry = _ai_cache[symbol]
            if current_time - cache_entry['timestamp'] < CACHE_TTL:
                logger.info(f"[Cache Hit] Parámetros IA para {symbol} obtenidos de caché.")
                results[symbol] = cache_entry['params']
                continue
                
        # Si no está en caché o expiró, lo encolamos para consultar
        symbols_to_fetch.append(sym_data)
        
    # 2. Consultar "IA" para los faltantes
    if symbols_to_fetch:
        logger.info(f"Consultando parámetros IA para {len(symbols_to_fetch)} símbolos...")
        
        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("No hay OPENROUTER_API_KEY ni OPENAI_API_KEY. Se usará fallback heurístico.")
            
        success = False

        if api_key:
            is_openai = api_key.startswith("sk-proj")
            endpoint = "https://api.openai.com/v1/chat/completions" if is_openai else "https://openrouter.ai/api/v1/chat/completions"
            model = "gpt-4o-mini" if is_openai else "google/gemini-2.5-flash"

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            if not is_openai:
                headers["HTTP-Referer"] = "http://localhost:4000"
                headers["X-Title"] = "Flipping JK"

            prompt_data = []
            for s in symbols_to_fetch:
                prompt_data.append({
                    "symbol": s['symbol'],
                    "amplitud_vela_pct": s.get('avg_body_pct', 0.5), # Esto ahora es la amplitud real
                    "tendencia_macro": s.get('trend', 'neutral')
                })

            prompt = f"""
            Eres un experto en trading cuantitativo de criptomonedas.
            Se te proporciona una lista de mercados con su amplitud de volatilidad promedio (High-Low) y su tendencia macro.
            Tu objetivo es recomendar parámetros para un Grid Trading Bot (grid_spacing_factor, grid_lines, leverage, direction).
            
            Reglas:
            - grid_spacing_factor: Entre 0.15 y 1.5. Debe ser lo suficientemente grande para vencer la comisión (0.10%), usualmente 60% de amplitud_vela_pct.
            - grid_lines: Entero entre 4 y 20.
            - leverage: Máximo {user_config.get("maxLeverage", 15.0)}. A mayor amplitud, menor apalancamiento por seguridad.
            - direction: Si la tendencia es 'long', devuelve 'long' (para un grid sesgado a compras). Si es 'short', 'short'. Si es 'neutral', 'neutral'.
            
            Datos de entrada:
            {json.dumps(prompt_data)}
            
            Debes responder UNICAMENTE con un objeto JSON válido, donde las llaves sean los símbolos y los valores sean objetos con 'grid_spacing_factor', 'grid_lines', 'leverage' y 'direction'.
            Ejemplo:
            {{
              "BTC-USDT-SWAP": {{"grid_spacing_factor": 0.3, "grid_lines": 10, "leverage": 15, "direction": "long"}}
            }}
            """

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(endpoint, headers=headers, json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}]
                    }) as response:
                        if response.status == 200:
                            data = await response.json()
                            content = data['choices'][0]['message']['content'].strip()
                            if content.startswith("```json"):
                                content = content[7:-3].strip()
                            elif content.startswith("```"):
                                content = content[3:-3].strip()
                                
                            ai_parsed = json.loads(content)
                            
                            for sym_data in symbols_to_fetch:
                                symbol = sym_data['symbol']
                                if symbol in ai_parsed:
                                    params = ai_parsed[symbol]
                                    results[symbol] = params
                                    _ai_cache[symbol] = {"params": params, "timestamp": current_time}
                                    logger.info(f"[AI Real] {symbol} -> {params}")
                                    
                            success = True
                        else:
                            error_text = await response.text()
                            logger.error(f"Error AI API: {response.status} - {error_text}")
            except Exception as e:
                logger.error(f"Excepción en AI API: {e}")

        # Fallback Heurístico (Cálculo Matemático) si falló la API
        if not success:
            for sym_data in symbols_to_fetch:
                symbol = sym_data['symbol']
                if symbol not in results:
                    avg_body = sym_data.get('avg_body_pct', 0.5)
                    max_lev = user_config.get("maxLeverage", 15.0)
                    
                    # 1. Distancia del grid (Spacing): 75% del promedio de la vela para capturar ruido, mínimo 0.15%
                    grid_spacing = max(0.15, round(avg_body * 0.75, 2))
                    
                    # 2. Leverage: Inversamente proporcional a la volatilidad.
                    # Asumimos que 50.0 / avg_body da un leverage seguro (ej: avg_body=2.0% -> lev=25x). Limitamos con maxLeverage.
                    calculated_leverage = round(50.0 / (avg_body if avg_body > 0 else 0.5))
                    leverage = min(max_lev, max(2.0, calculated_leverage))
                    
                    # 3. Líneas del grid: A mayor volatilidad, más líneas para cubrir un rango más amplio.
                    if avg_body > 2.0:
                        grid_lines = 20
                    elif avg_body > 1.0:
                        grid_lines = 15
                    else:
                        grid_lines = 10
                        
                    params = {
                        "grid_spacing_factor": grid_spacing,
                        "grid_lines": grid_lines,
                        "leverage": leverage
                    }
                    results[symbol] = params
                    _ai_cache[symbol] = {
                        "params": params,
                        "timestamp": current_time
                    }
                    logger.info(f"[Cálculo Matemático - Fallback] {symbol} -> {params}")
            
    return results
