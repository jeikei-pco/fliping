"""
ai_optimizer.py — Worker Daemon para optimización IA.
Consulta a Claude periódicamente y guarda los overrides en SQLite.
"""
import json
import logging
import os
import urllib.request
import urllib.error
import re
import time
import threading
from typing import Dict, Any

from core.database import Database

logger = logging.getLogger("UAO_Sclaping.AIOptimizer")

class AIOptimizerWorker(threading.Thread):
    def __init__(self, db: Database):
        super().__init__(daemon=True)
        self.db = db
        self.intervalo = float(os.getenv("AI_OPTIMIZER_INTERVAL_HOURS", 24)) * 3600
        self.api_url = os.getenv("AI_OPTIMIZER_API_URL", "http://127.0.0.1:8082/v1/messages")

    def run(self):
        logger.info(f"🤖 AI Optimizer iniciado. Consultará IA cada {self.intervalo/3600:.1f}h")
        # Esperar un poco antes de la primera consulta
        time.sleep(60)
        
        while True:
            try:
                self._optimizar_global()
            except Exception as e:
                logger.error(f"❌ Error en AI Optimizer: {e}")
                
            time.sleep(self.intervalo)

    def _get_api_providers(self):
        providers = []
        
        # Gemini (Native SDK)
        for key_name in ["GEMINI_API_KEY", "GEMINI_API_KEY2", "GEMINI_API_KEY3"]:
            val = os.getenv(key_name)
            if val:
                providers.append({"key": val, "model": "gemini-2.0-flash", "type": "gemini"})
                
        # OpenRouter
        for key_name in ["OPENROUTER_API_KEY", "OPENROUTER_API_KEY2"]:
            val = os.getenv(key_name)
            if val:
                providers.append({"url": "https://openrouter.ai/api/v1/chat/completions", "key": val, "model": "google/gemini-2.0-flash-001", "type": "openai"})
                
        # OpenAI
        for key_name in ["OPENAI_API_KEY", "OPENAI_API_KEY2"]:
            val = os.getenv(key_name)
            if val:
                providers.append({"url": "https://api.openai.com/v1/chat/completions", "key": val, "model": "gpt-4o-mini", "type": "openai"})
                
        # Groq
        for key_name in ["GROQ_API_KEY"]:
            val = os.getenv(key_name)
            if val:
                providers.append({"url": "https://api.groq.com/openai/v1/chat/completions", "key": val, "model": "llama3-70b-8192", "type": "openai"})
                
        # Claude (Local Proxy / Anthropic format)
        providers.append({"url": self.api_url, "key": os.getenv("CLAUDE_CODE_PROXY_API_KEY", "freecc").strip(), "model": os.getenv("CLAUDE_CODE_MODEL", "claude-3-5-haiku-20241022"), "type": "anthropic"})
        
        return providers

    def _optimizar_global(self):
        logger.info("🧠 Solicitando micro-ajustes a la IA basados en contexto cuantitativo...")
        
        # 1. Obtener contexto de DB
        scanner_state = self.db.get_scanner_state() or []
        trades = self.db.get_recent_trades(limit=500)
        
        # 2. Calcular métricas reales y distribuciones
        total_trades = len(trades)
        win_trades = [t for t in trades if t["pnl"] > 0]
        loss_trades = [t for t in trades if t["pnl"] < 0]
        
        win_rate = round((len(win_trades) / total_trades * 100), 2) if total_trades > 0 else 0.0
        
        gross_profit = sum(t["pnl"] for t in win_trades)
        gross_loss = abs(sum(t["pnl"] for t in loss_trades))
        profit_factor = round((gross_profit / gross_loss), 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        pnl_neto = round(sum(t["pnl"] - t["fee"] for t in trades), 2)
        
        avg_profit = round(gross_profit / len(win_trades), 2) if win_trades else 0.0
        avg_loss = round(gross_loss / len(loss_trades), 2) if loss_trades else 0.0
        
        long_trades = [t for t in trades if t["side"] == "BUY"]
        short_trades = [t for t in trades if t["side"] == "SELL"]
        long_win = len([t for t in long_trades if t["pnl"] > 0])
        short_win = len([t for t in short_trades if t["pnl"] > 0])
        long_win_rate = round(long_win / len(long_trades) * 100, 2) if long_trades else 0.0
        short_win_rate = round(short_win / len(short_trades) * 100, 2) if short_trades else 0.0
        
        # Rachas (evaluando de más antiguo a más reciente, asumimos que 'trades' está ordenado por fecha desc)
        # Invertimos para calcular rachas cronológicamente
        max_wins, max_losses, cur_wins, cur_losses = 0, 0, 0, 0
        for t in reversed(trades):
            if t["pnl"] > 0:
                cur_wins += 1
                cur_losses = 0
                max_wins = max(max_wins, cur_wins)
            elif t["pnl"] < 0:
                cur_losses += 1
                cur_wins = 0
                max_losses = max(max_losses, cur_losses)
                
        # PnL por hora
        import datetime
        pnl_by_hour = {}
        for t in trades:
            try:
                # El DB guarda 'YYYY-MM-DDTHH:MM:SS.mmmmmm'
                dt_str = t["executed_at"].split(".")[0] if "." in t["executed_at"] else t["executed_at"]
                dt = datetime.datetime.fromisoformat(dt_str)
                hour = dt.strftime("%H")
                pnl_by_hour[hour] = round(pnl_by_hour.get(hour, 0.0) + t["pnl"] - t["fee"], 2)
            except Exception:
                pass
                
        # Por símbolo fusionando con scanner
        symbols_stats = {}
        scanner_dict = {s["symbol"]: s for s in scanner_state} if scanner_state else {}
        
        for t in trades:
            sym = t["symbol"]
            if sym not in symbols_stats:
                symbols_stats[sym] = {"trades": 0, "wins": 0, "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0}
            symbols_stats[sym]["trades"] += 1
            if t["pnl"] > 0:
                symbols_stats[sym]["wins"] += 1
                symbols_stats[sym]["gross_profit"] += t["pnl"]
            elif t["pnl"] < 0:
                symbols_stats[sym]["gross_loss"] += abs(t["pnl"])
            symbols_stats[sym]["pnl"] += t["pnl"] - t["fee"]
            
        combined_symbols = {}
        # Filtrar solo a los top símbolos con más volumen u operaciones recientes para no sobrecargar el prompt
        for sym, stat in sorted(symbols_stats.items(), key=lambda x: x[1]["trades"], reverse=True)[:15]:
            pf = stat["gross_profit"] / stat["gross_loss"] if stat["gross_loss"] > 0 else (99.0 if stat["gross_profit"] > 0 else 0)
            wr = stat["wins"] / stat["trades"] * 100
            scan_data = scanner_dict.get(sym, {})
            combined_symbols[sym] = {
                "score_analizador": scan_data.get("score", 0),
                "simetria_analizador": scan_data.get("simetria", 0),
                "consistencia": scan_data.get("consistencia", 0),
                "oscilacion": scan_data.get("oscilacion", 0),
                "ops_promedio": scan_data.get("ops_promedio", 0),
                "win_rate_real": round(wr, 2),
                "profit_factor_real": round(pf, 2),
                "pnl_real": round(stat["pnl"], 2),
                "total_trades": stat["trades"]
            }

        context = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "bot_metrics_last_500_trades": {
                "total_trades": total_trades,
                "win_rate_pct": win_rate,
                "profit_factor": profit_factor,
                "net_pnl": pnl_neto,
                "avg_profit": avg_profit,
                "avg_loss": avg_loss,
                "long_win_rate": long_win_rate,
                "short_win_rate": short_win_rate,
                "max_consecutive_wins": max_wins,
                "max_consecutive_losses": max_losses,
                "pnl_by_utc_hour": pnl_by_hour
            },
            "per_symbol_performance": combined_symbols
        }
        
        prompt = f"""
Estos son los resultados del análisis cuantitativo y el rendimiento reciente del bot:
{json.dumps(context, indent=2)}

Decide únicamente ajustes FINOS sobre los parámetros matemáticos base.
No cambies parámetros más del ±20% (rango 0.8 a 1.2).

Devuelve ESTRICTAMENTE un JSON con estas claves exactas:
- GRID_STEP_PCT (float, porcentaje EXACTO de distancia entre líneas, ej 0.22 para 0.22%)
- GRID_DENSITY_FACTOR (float, multiplicador de cantidad de lineas, ej 1.08)
- LEVERAGE_FACTOR (float, multiplicador de apalancamiento, ej 0.95)
- CAPITAL_FACTOR (float, multiplicador de asignacion de capital, ej 1.10)
- MAX_LEVERAGE (int, apalancamiento máximo absoluto, ej 12)
- MIN_SCORE (int, puntaje mínimo para operar, ej 82)
- MIN_CONSISTENCY (float, consistencia mínima 0-1, ej 0.74)
- MIN_OSCILLATION (float, oscilación mínima requerida, ej 2.7)

IMPORTANTE: Responde ÚNICAMENTE con un objeto JSON válido, sin comillas invertidas ni bloques de markdown ni texto extra.
Ejemplo:
{{"GRID_STEP_PCT": 0.25, "GRID_DENSITY_FACTOR": 1.1, "LEVERAGE_FACTOR": 0.9, "CAPITAL_FACTOR": 1.0, "MAX_LEVERAGE": 15, "MIN_SCORE": 75, "MIN_CONSISTENCY": 0.7, "MIN_OSCILLATION": 2.5}}
"""
        providers = self._get_api_providers()
        
        content = ""
        success = False
        
        for provider in providers:
            logger.info(f"Probando proveedor AI: {provider.get('url', 'Gemini Native')} (Modelo: {provider['model']})")
            
            if provider["type"] == "gemini":
                try:
                    from google import genai
                    from google.genai import types
                    
                    client = genai.Client(api_key=provider["key"])
                    response = client.models.generate_content(
                        model=provider['model'],
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction="Eres un experto cuantitativo de IA. Respondes únicamente en JSON crudo sin comillas invertidas ni bloques de markdown ni explicaciones previas.",
                            temperature=0.2
                        )
                    )
                    content = response.text
                    success = True
                    break
                except Exception as e:
                    logger.warning(f"Fallo proveedor Gemini: {e}")
                    continue

            elif provider["type"] == "openai":
                try:
                    import openai
                    
                    base_url = provider["url"].replace("/chat/completions", "") if "url" in provider else None
                    client = openai.OpenAI(api_key=provider["key"], base_url=base_url)
                    
                    kwargs = {
                        "model": provider["model"],
                        "messages": [
                            {"role": "system", "content": "Eres un experto cuantitativo de IA. Respondes únicamente en JSON crudo sin comillas invertidas ni bloques de markdown ni explicaciones previas."},
                            {"role": "user", "content": prompt}
                        ],
                        "max_tokens": 1000
                    }
                    
                    # Usar response_format solo si no es Groq
                    if "groq" not in provider.get("url", ""):
                        kwargs["response_format"] = {"type": "json_object"}
                        
                    response = client.chat.completions.create(**kwargs)
                    content = response.choices[0].message.content
                    success = True
                    break
                except Exception as e:
                    logger.warning(f"Fallo proveedor {provider.get('url')}: {e}")
                    continue
                    
            else:
                # Fallback para Anthropic/Claude Code Proxy manual
                body = {
                    "model": provider["model"],
                    "max_tokens": 1000,
                    "system": "Eres un experto cuantitativo de IA. Respondes únicamente en JSON crudo sin comillas invertidas ni bloques de markdown ni explicaciones previas.",
                    "messages": [{"role": "user", "content": prompt}],
                }
                headers = {
                    "content-type": "application/json", 
                    "x-api-key": provider["key"], 
                    "anthropic-version": "2023-06-01"
                }
            
                request = urllib.request.Request(
                    provider["url"],
                    data=json.dumps(body).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                
                try:
                    with urllib.request.urlopen(request, timeout=20) as response:
                        raw = response.read().decode("utf-8", "replace")
                        # Parseo Anthropic (SSE o JSON standard)
                        try:
                            payload = json.loads(raw)
                            for item in payload.get("content", []):
                                if isinstance(item, dict) and item.get("type") == "text":
                                    content += item.get("text", "")
                            success = True
                            break
                        except json.JSONDecodeError:
                            for line in raw.splitlines():
                                if line.startswith("data: "):
                                    try:
                                        data = json.loads(line[6:])
                                        if data.get("type") == "content_block_delta":
                                            delta = data.get("delta", {})
                                            if delta.get("type") == "text_delta":
                                                content += delta.get("text", "")
                                        elif data.get("type") == "message" and "content" in data:
                                            for item in data.get("content", []):
                                                if isinstance(item, dict) and item.get("type") == "text":
                                                    content += item.get("text", "")
                                    except Exception:
                                        pass
                            success = True
                            break
                except Exception as e:
                    logger.warning(f"Fallo proveedor {provider['url']}: {e}")
                    continue
                
        if not success:
            logger.warning("⚠️ Todos los proveedores de IA fallaron. Usando OptimizadorGrid (Matemático) como respaldo.")
            return

        # ── Extracción robusta de JSON ────────────────────────────────────
        def extraer_jsons(texto: str):
            """Extrae todos los candidatos JSON usando conteo de llaves."""
            candidatos = []
            depth = 0
            inicio = -1
            for i, c in enumerate(texto):
                if c == '{':
                    if depth == 0:
                        inicio = i
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0 and inicio != -1:
                        candidatos.append(texto[inicio:i+1])
                        inicio = -1
            return candidatos

        CLAVES_VALIDAS = {
            "GRID_STEP_PCT", "GRID_DENSITY_FACTOR", "LEVERAGE_FACTOR", "CAPITAL_FACTOR",
            "MAX_LEVERAGE", "MIN_SCORE", "MIN_CONSISTENCY", "MIN_OSCILLATION"
        }

        params = None
        for candidato in extraer_jsons(content):
            try:
                parsed = json.loads(candidato)
                if any(k in parsed for k in CLAVES_VALIDAS):
                    params = {k: v for k, v in parsed.items() if k in CLAVES_VALIDAS}
                    break
            except json.JSONDecodeError:
                continue

        if params:
            # Clamping de seguridad
            def clamp(val, min_val, max_val):
                return max(min_val, min(float(val), max_val))
                
            if "GRID_STEP_PCT" in params:
                # Limitamos el porcentaje absoluto a un rango sensato: 0.15% a 5%
                params["GRID_STEP_PCT"] = clamp(float(params["GRID_STEP_PCT"]), 0.15, 5.0)
            if "GRID_DENSITY_FACTOR" in params:
                params["GRID_DENSITY_FACTOR"] = clamp(float(params["GRID_DENSITY_FACTOR"]), 0.75, 1.25)
            if "LEVERAGE_FACTOR" in params:
                params["LEVERAGE_FACTOR"] = clamp(float(params["LEVERAGE_FACTOR"]), 0.8, 1.15)
            if "CAPITAL_FACTOR" in params:
                params["CAPITAL_FACTOR"] = clamp(float(params["CAPITAL_FACTOR"]), 0.7, 1.3)
                
            logger.info(f"🤖 IA sugiere overrides (limitados): {params}")
            self.db.update_config_overrides(params)
        else:
            logger.warning(f"⚠️ IA no devolvió JSON válido con claves conocidas. Contenido recibido: {content[:200]}")

