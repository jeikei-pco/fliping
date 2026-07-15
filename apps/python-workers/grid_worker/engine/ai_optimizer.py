import asyncio
import time
import logging
import json
import os
import aiohttp

from engine.optimizer_integrator import optimize_grid_params

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
        
        providers = [
            {
                "name": "OpenAI 1",
                "key": os.getenv("OPENAI_API_KEY"),
                "endpoint": "https://api.openai.com/v1/chat/completions",
                "model": "gpt-4o-mini"
            },
            {
                "name": "OpenAI 2",
                "key": os.getenv("OPENAI_API_KEY2"),
                "endpoint": "https://api.openai.com/v1/chat/completions",
                "model": "gpt-4o-mini"
            },
            {
                "name": "Groq",
                "key": os.getenv("GROQ_API_KEY"),
                "endpoint": "https://api.groq.com/openai/v1/chat/completions",
                "model": "llama-3.1-70b-versatile"
            },
            {
                "name": "OpenRouter",
                "key": os.getenv("OPENROUTER_API_KEY"),
                "endpoint": "https://openrouter.ai/api/v1/chat/completions",
                "model": "google/gemini-2.5-flash"
            },
            {
                "name": "Gemini",
                "key": os.getenv("GEMINI_API_KEY"),
                "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                "model": "gemini-1.5-flash"
            }
        ]

        success = False
        
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
        
        Debes responder UNICAMENTE con un objeto JSON válido, donde las llaves sean los símbolos y los valores sean objetos con 'grid_spacing_factor', 'grid_lines', 'leverage' y 'direction'. No uses markdown.
        Ejemplo:
        {{
            "BTC-USDT-SWAP": {{"grid_spacing_factor": 0.3, "grid_lines": 10, "leverage": 15, "direction": "long"}}
        }}
        """

        for provider in providers:
            if not provider["key"]:
                continue
                
            logger.info(f"Intentando optimizar con {provider['name']}...")
            
            headers = {
                "Authorization": f"Bearer {provider['key']}",
                "Content-Type": "application/json"
            }
            if "openrouter" in provider["endpoint"]:
                headers["HTTP-Referer"] = "http://localhost:4000"
                headers["X-Title"] = "Flipping JK"

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(provider["endpoint"], headers=headers, json={
                        "model": provider["model"],
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"} if "openai.com" in provider["endpoint"] or "groq.com" in provider["endpoint"] else None
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
                                    logger.info(f"[AI Real - {provider['name']}] {symbol} -> {params}")
                                    
                            success = True
                            break
                        else:
                            error_text = await response.text()
                            logger.error(f"Fallo en {provider['name']}: {response.status} - {error_text}")
            except Exception as e:
                logger.error(f"Excepción en {provider['name']}: {e}")
                
        if not success:
            logger.warning("Todos los proveedores fallaron o no hay claves. Usando fallback heurístico.")

        # Fallback Heurístico si falló la API o si omitió algún símbolo
        for sym_data in symbols_to_fetch:
            symbol = sym_data['symbol']
            if symbol not in results:
                # Usar el integrador de fallback que el usuario creó
                params = optimize_grid_params(None, sym_data)
                results[symbol] = params
                _ai_cache[symbol] = {
                    "params": params,
                    "timestamp": current_time
                }
                logger.info(f"[OptimizerIntegrator - Fallback] {symbol} -> {params}")
            
    return results
