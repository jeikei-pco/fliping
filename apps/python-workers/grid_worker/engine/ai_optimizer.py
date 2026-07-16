import asyncio
import time
import logging
import json
import os
import aiohttp

from engine.optimizer_integrator import optimize_grid_params

logger = logging.getLogger("GridWorker.AIOptimizer" )

# Caché local (en memoria) para evitar saturar rate limits
_ai_cache = {}
CACHE_TTL = 3600  # 1 hora en segundos

async def get_ai_grid_params_batch(batch, user_config):
    """
    Obtiene los parámetros óptimos del grid (vía IA o heurística) para un lote de símbolos.
    Implementa caché local de 1 hora y cascada de múltiples proveedores de IA.
    """
    results = {}
    symbols_to_fetch = []
    current_time = time.time()
    
    # 1. Revisar Caché
    for sym_data in batch:
        symbol = sym_data['symbol']
        if symbol in _ai_cache:
            cache_entry = _ai_cache[symbol]
            if current_time - cache_entry['timestamp'] < CACHE_TTL:
                logger.info(f"[Cache Hit] Parámetros IA para {symbol} obtenidos de caché.")
                results[symbol] = cache_entry['params']
                continue
        symbols_to_fetch.append(sym_data)
        
    # 2. Consultar IA para los faltantes
    if symbols_to_fetch:
        logger.info(f"Consultando parámetros IA para {len(symbols_to_fetch)} símbolos...")
        
        # 🚀 Cascada de Proveedores (Intenta uno por uno hasta que funcione)
        providers = [
            {"name": "Groq", "key": os.getenv("GROQ_API_KEY"), "endpoint": "https://api.groq.com/openai/v1/chat/completions", "model": "llama-3.1-70b-versatile"},
            {"name": "OpenAI 1", "key": os.getenv("OPENAI_API_KEY" ), "endpoint": "https://api.openai.com/v1/chat/completions", "model": "gpt-4o-mini"},
            {"name": "OpenAI 2", "key": os.getenv("OPENAI_API_KEY2" ), "endpoint": "https://api.openai.com/v1/chat/completions", "model": "gpt-4o-mini"},
            {"name": "Gemini 1", "key": os.getenv("GEMINI_API_KEY" ), "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "model": "gemini-1.5-flash"},
            {"name": "Gemini 2", "key": os.getenv("GEMINI_API_KEY2" ), "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "model": "gemini-1.5-flash"},
            {"name": "Gemini 3", "key": os.getenv("GEMINI_API_KEY3" ), "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "model": "gemini-1.5-flash"},
            {"name": "OpenRouter 1", "key": os.getenv("OPENROUTER_API_KEY" ), "endpoint": "https://openrouter.ai/api/v1/chat/completions", "model": "google/gemini-2.5-flash"},
            {"name": "OpenRouter 2", "key": os.getenv("OPENROUTER_API_KEY2" ), "endpoint": "https://openrouter.ai/api/v1/chat/completions", "model": "google/gemini-2.5-flash"}
        ]

        success = False
        
        # Preparar datos enriquecidos para el Prompt
        prompt_data = []
        for s in symbols_to_fetch:
            # Respetamos el maximo absoluto por usuario o por limite tecnico del exchange
            max_lev_allowed = min(float(s.get('max_leverage', 15.0)), float(user_config.get("maxLeverage", 15.0)))
            prompt_data.append({
                "symbol": s['symbol'],
                "tamaño_vela_pct": s.get('avg_body_pct', 0.5 ),
                "calidad_vela": s.get('quality', 0.5),
                "desviacion_estandar": s.get('std_dev', 0.1),
                "tendencia_macro": s.get('trend', 'neutral'),
                "score_promesa": s.get('score', 0),
                "max_leverage": max_lev_allowed
            })

        prompt = f"""
        Eres un experto en trading cuantitativo de criptomonedas.
        Se te proporciona una lista de mercados con métricas de volatilidad en velas de 5 minutos.
        Tu objetivo es recomendar parámetros para un Grid Trading Bot.
        
        Reglas estrictas:
        - grid_spacing_factor: Entre 0.15 y 1.5. Si la 'calidad_vela' es baja (<0.5, muchas mechas), aumenta el spacing.
        - grid_lines: Entero entre 4 y 20. Si la 'desviacion_estandar' es alta, usa más líneas para promediar.
        - leverage: Máximo estricto igual al campo 'max_leverage'. A mayor 'tamaño_vela_pct', menor apalancamiento.
        - recalculate_every_minutes: Entero (60, 120 o 240). Si es muy errático, recalcular rápido (60).
        - direction: Devuelve exactamente la 'tendencia_macro' ('long', 'short' o 'neutral').
        
        Datos de entrada:
        {json.dumps(prompt_data)}
        
        Debes responder UNICAMENTE con un objeto JSON válido. No uses markdown ni texto adicional.
        Ejemplo de salida esperada:
        {{
            "BTC/USDT:USDT": {{"grid_spacing_factor": 0.3, "grid_lines": 10, "leverage": 15, "recalculate_every_minutes": 120, "direction": "long"}}
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
            
            # Headers especiales para OpenRouter
            if "openrouter" in provider["endpoint"]:
                headers["HTTP-Referer"] = "http://localhost:4000"
                headers["X-Title"] = "Flipping JK"

            # Solo forzamos JSON mode en OpenAI y Groq para evitar errores de compatibilidad en Gemini/OpenRouter
            payload = {
                "model": provider["model"],
                "messages": [{"role": "user", "content": prompt}]
            }
            if "openai.com" in provider["endpoint"] or "groq.com" in provider["endpoint"]:
                payload["response_format"] = {"type": "json_object"}

            try:
                async with aiohttp.ClientSession( ) as session:
                    async with session.post(provider["endpoint"], headers=headers, json=payload, timeout=20) as response:
                        
                        if response.status == 200:
                            data = await response.json()
                            content = data['choices'][0]['message']['content'].strip()
                            
                            # Limpieza de Markdown
                            if content.startswith("```json"): content = content[7:-3].strip()
                            elif content.startswith("```"): content = content[3:-3].strip()
                                
                            ai_parsed = json.loads(content)
                            
                            for sym_data in symbols_to_fetch:
                                symbol = sym_data['symbol']
                                if symbol in ai_parsed:
                                    params = ai_parsed[symbol]
                                    params['source'] = f"ai_{provider['name'].lower().replace(' ', '_')}"
                                    results[symbol] = params
                                    _ai_cache[symbol] = {"params": params, "timestamp": current_time}
                                    logger.info(f"[AI Real - {provider['name']}] {symbol} -> {params}")
                                    
                            success = True
                            break # Salimos del loop de proveedores porque este funcionó
                        else:
                            error_text = await response.text()
                            logger.warning(f"Fallo en {provider['name']}: {response.status} - {error_text}")
            except Exception as e:
                logger.warning(f"Excepción en {provider['name']}: {e}")
                
        if not success:
            logger.warning("Todos los proveedores LLM fallaron. Usando fallback heurístico matemático.")

        # 3. Fallback Heurístico si fallaron todas las APIs
        for sym_data in symbols_to_fetch:
            symbol = sym_data['symbol']
            if symbol not in results:
                params = optimize_grid_params(None, sym_data)
                results[symbol] = params
                _ai_cache[symbol] = {"params": params, "timestamp": current_time}
                logger.info(f"[OptimizerIntegrator - Fallback] {symbol} -> {params}")
            
    return results


