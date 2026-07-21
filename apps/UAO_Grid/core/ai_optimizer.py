import json
import logging
import os
import re
import time
import threading
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional

from core.database import Database
from core.adapters.ai_provider_factory import get_api_providers

logger = logging.getLogger("UAO_Sclaping.AIOptimizer")


def _extraer_json_robusto(texto: str) -> Optional[Dict]:
    """
    [FASE 3a] Parser JSON robusto con expresiones regulares.
    Tolera respuestas malformadas del LLM: markdown fences, texto extra, llaves
    incompletas, etc. Devuelve el primer objeto JSON dict valido encontrado o None.
    """
    if not texto:
        return None

    # 1. Limpiar bloques markdown (```json ... ``` o ``` ... ```)
    texto_limpio = re.sub(r"```(?:json)?\s*", "", texto).replace("```", "").strip()

    # 2. Intentar extraccion con regex: busca el bloque mas grande de { ... }
    #    usando un patron greedy de apertura a cierre, con anidamiento controlado.
    candidatos: List[str] = []
    profundidad = 0
    inicio = -1
    for i, c in enumerate(texto_limpio):
        if c == "{":
            if profundidad == 0:
                inicio = i
            profundidad += 1
        elif c == "}":
            profundidad -= 1
            if profundidad == 0 and inicio != -1:
                candidatos.append(texto_limpio[inicio : i + 1])
                inicio = -1

    # Ordenamos por longitud descendente para intentar el JSON mas completo primero
    candidatos.sort(key=len, reverse=True)

    for candidato in candidatos:
        try:
            parsed = json.loads(candidato)
            if isinstance(parsed, dict) and len(parsed) > 0:
                return parsed
        except json.JSONDecodeError:
            # Intentar reparar comillas simples o trailing commas comunes
            try:
                reparado = re.sub(r",\s*([}\]])", r"\1", candidato)
                parsed = json.loads(reparado)
                if isinstance(parsed, dict) and len(parsed) > 0:
                    return parsed
            except json.JSONDecodeError:
                continue

    # 3. Fallback: intentar parsear el texto limpio directamente
    try:
        parsed = json.loads(texto_limpio)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    return None





def _perfil_analisis(item: Dict[str, Any]) -> Dict[str, Any]:
    analisis = item.get("analisis_original") if isinstance(item.get("analisis_original"), dict) else item
    profile = analisis.get("analysis_profile") or analisis.get("analysis")
    return profile if isinstance(profile, dict) else {}


def _contexto_cuantitativo_symbol(scan_data: Dict[str, Any]) -> Dict[str, Any]:
    profile = _perfil_analisis(scan_data)
    if profile:
        return profile

    return {
        "symbol": scan_data.get("symbol"),
        "metadata": {"score": scan_data.get("score", 0)},
        "volatility": {
            "consistencia": scan_data.get("consistencia", 0),
            "atr_pct": scan_data.get("atr_pct", 0),
        },
        "grid": {
            "oscilacion": scan_data.get("oscilacion", 0),
            "ops_promedio": scan_data.get("ops_promedio", 0),
            "grid_quality": scan_data.get("grid_quality", 0),
            "densidad_sugerida": scan_data.get("densidad_sugerida", 1.0),
        },
        "risk": {"riesgo": scan_data.get("riesgo", 0)},
        "trend": {"modo_preferido": scan_data.get("modo_preferido", "NEUTRAL")},
        "capital": {
            "capital_factor": scan_data.get("capital_factor", 1.0),
            "apalancamiento_factor": scan_data.get("apalancamiento_factor", 1.0),
        },
    }


class AIOptimizerWorker(threading.Thread):
    def __init__(self, db: Database):
        super().__init__(daemon=True)
        self.db = db
        self.intervalo = float(os.getenv("AI_OPTIMIZER_INTERVAL_HOURS", 24)) * 3600

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

    def _optimizar_global(self):
        logger.info("🧠 Solicitando micro-ajustes a la IA basados en contexto cuantitativo por símbolo...")
        
        # 1. Obtener contexto de DB
        scanner_state = self.db.get_scanner_state() or []
        trades = self.db.get_recent_trades(limit=500)
        
        # 2. Calcular métricas reales y distribuciones globales (mantenidas para referencia general)
        total_trades = len(trades)
        win_trades = [t for t in trades if t["pnl"] > 0]
        loss_trades = [t for t in trades if t["pnl"] < 0]
        
        win_rate = round((len(win_trades) / total_trades * 100), 2) if total_trades > 0 else 0.0
        
        gross_profit = sum(t["pnl"] for t in win_trades)
        gross_loss = abs(sum(t["pnl"] for t in loss_trades))
        profit_factor = round((gross_profit / gross_loss), 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        pnl_neto = round(sum(t["pnl"] - t["fee"] for t in trades), 2)
        
        # Por símbolo fusionando con scanner y métricas detalladas
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
        # Filtrar los top símbolos con más operaciones recientes
        for sym, stat in sorted(symbols_stats.items(), key=lambda x: x[1]["trades"], reverse=True)[:10]:
            pf = stat["gross_profit"] / stat["gross_loss"] if stat["gross_loss"] > 0 else (99.0 if stat["gross_profit"] > 0 else 0)
            wr = stat["wins"] / stat["trades"] * 100
            scan_data = scanner_dict.get(sym, {})
            combined_symbols[sym] = {
                "analysis_profile": _contexto_cuantitativo_symbol(scan_data),
                "win_rate_real": round(wr, 2),
                "profit_factor_real": round(pf, 2),
                "pnl_real": round(stat["pnl"], 2),
                "total_trades": stat["trades"]
            }

        import datetime
        context = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "global_net_pnl": pnl_neto,
            "per_symbol_performance": combined_symbols
        }
        
        prompt = f"""
Estos son los resultados del análisis cuantitativo y el rendimiento reciente del bot por símbolo:
{json.dumps(context, indent=2)}

Decide ajustes FINOS adaptados INDIVIDUALMENTE para cada símbolo especificado en 'per_symbol_performance'.
No cambies parámetros más del ±20% (rango 0.8 a 1.2).

Devuelve ESTRICTAMENTE un JSON con un diccionario donde cada llave sea el nombre exacto del símbolo (ej. "KAITO/USDT:USDT") y su valor un objeto con estas claves exactas:
- GRID_STEP_PCT (float, porcentaje EXACTO de distancia entre líneas, ej 0.22)
- GRID_DENSITY_FACTOR (float, multiplicador de cantidad de líneas, ej 1.08)
- LEVERAGE_FACTOR (float, multiplicador de apalancamiento, ej 0.95)
- CAPITAL_FACTOR (float, multiplicador de asignación de capital, ej 1.10)
- MAX_LEVERAGE (int, apalancamiento máximo absoluto, ej 12)
- MIN_SCORE (int, puntaje mínimo para operar, ej 82)
- MIN_CONSISTENCY (float, consistencia mínima 0-1, ej 0.74)
- MIN_OSCILLATION (float, oscilación mínima requerida, ej 2.7)

IMPORTANTE: Responde ÚNICAMENTE con un objeto JSON válido, sin comillas invertidas ni bloques de markdown ni texto extra.
Ejemplo:
{{
  "KAITO/USDT:USDT": {{
    "GRID_STEP_PCT": 0.25,
    "GRID_DENSITY_FACTOR": 1.1,
    "LEVERAGE_FACTOR": 0.9,
    "CAPITAL_FACTOR": 1.0,
    "MAX_LEVERAGE": 15,
    "MIN_SCORE": 75,
    "MIN_CONSISTENCY": 0.7,
    "MIN_OSCILLATION": 2.5
  }}
}}
"""
        providers = get_api_providers()                     
        content = ""
        success = False                     

        for provider in providers:
            logger.info(f"Probando proveedor AI: {provider.provider_name}")                         
            result = provider.generate_json(prompt)
            
            if result:
                content = result
                success = True
                logger.info(f"¡Proveedor {provider.provider_name} respondió con éxito! Omitiendo el resto.")
                break  # Detenemos la búsqueda en cuanto uno responde

        if not success:
            logger.warning("  Todos los proveedores de IA fallaron. Usando OptimizadorGrid (Matemático) como respaldo.")
            return

        # [FASE 3a] Usar parser robusto centralizado en lugar de conteo manual de llaves
        parsed_data = _extraer_json_robusto(content)

        if parsed_data:
            def clamp(val, min_val, max_val):
                return max(min_val, min(float(val), max_val))

            for symbol, params in parsed_data.items():
                if isinstance(params, dict):
                    if "GRID_STEP_PCT" in params:
                        params["GRID_STEP_PCT"] = clamp(float(params["GRID_STEP_PCT"]), 0.15, 5.0)
                    if "GRID_DENSITY_FACTOR" in params:
                        params["GRID_DENSITY_FACTOR"] = clamp(float(params["GRID_DENSITY_FACTOR"]), 0.75, 1.25)
                    if "LEVERAGE_FACTOR" in params:
                        params["LEVERAGE_FACTOR"] = clamp(float(params["LEVERAGE_FACTOR"]), 0.8, 1.15)
                    if "CAPITAL_FACTOR" in params:
                        params["CAPITAL_FACTOR"] = clamp(float(params["CAPITAL_FACTOR"]), 0.7, 1.3)

                    logger.info(f"  IA sugiere overrides especificos para {symbol}: {params}")
                    if hasattr(self.db, "update_symbol_config_overrides"):
                        self.db.update_symbol_config_overrides(symbol, params)
                    else:
                        self.db.update_config_overrides(params)
        else:
            logger.warning(f"  IA no devolvio JSON valido estructurado por simbolos. Contenido recibido: {content[:200]}")


    def optimizar_lote_top3(self, top_3_symbols_data: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """
        Recibe el Top 3 de símbolos con sus métricas y backtest base,
        envía todo en un solo JSON a la IA y retorna un diccionario con los overrides granulares por símbolo.
        """
        import datetime
        top_candidates = []
        for item in top_3_symbols_data:
            top_candidates.append({
                "symbol": item.get("symbol"),
                "analysis_profile": _contexto_cuantitativo_symbol(item),
                "base_backtest": {
                    "pnl_neto": item.get("pnl_neto", 0.0),
                    "roi_pct": item.get("roi_pct", 0.0),
                    "drawdown": item.get("drawdown", 0.0),
                    "win_rate": item.get("win_rate", 0.0),
                    "profit_factor": item.get("profit_factor", 0.0),
                    "operaciones": item.get("operaciones", 0),
                },
                "params_optimos": item.get("params_optimos", {}),
            })

        context = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "top_candidates": top_candidates
        }
        
        prompt = f"""
Estos son los resultados del análisis y backtest base para los mejores candidatos actuales:
{json.dumps(context, indent=2)}

Decide ajustes FINOS adaptados INDIVIDUALMENTE para cada símbolo listado en un solo objeto JSON.
No cambies parámetros más del ±20% (rango 0.8 a 1.2).

Devuelve ESTRICTAMENTE un JSON con un diccionario donde cada llave sea el nombre exacto del símbolo (ej. "KAITO/USDT:USDT") y su valor un objeto con estas claves exactas:
- GRID_STEP_PCT (float, porcentaje exacto de distancia entre líneas, ej 0.22)
- GRID_DENSITY_FACTOR (float, multiplicador de cantidad de líneas, ej 1.08)
- LEVERAGE_FACTOR (float, multiplicador de apalancamiento, ej 0.95)
- CAPITAL_FACTOR (float, multiplicador de asignación de capital, ej 1.10)
- MAX_LEVERAGE (int, apalancamiento máximo absoluto, ej 12)
- MIN_SCORE (int, puntaje mínimo, ej 82)
- MIN_CONSISTENCY (float, consistencia 0-1, ej 0.74)
- MIN_OSCILLATION (float, oscilación mínima, ej 2.7)

IMPORTANTE: Responde ÚNICAMENTE con un objeto JSON válido que contenga todos los símbolos, sin comillas invertidas ni bloques de markdown ni texto extra.
"""
        providers = get_api_providers()
        content = ""
        success = False

        for provider in providers:
            logger.info(f"Probando proveedor AI para Lote Top 3: {provider.provider_name}")
            result = provider.generate_json(prompt)
            if result:
                content = result
                success = True
                logger.info(f"¡Proveedor {provider.provider_name} respondió con éxito para el lote Top 3!")
                break

        if not success:
            logger.warning("Todos los proveedores de IA fallaron para la optimizacion del lote Top 3.")
            return {}

        # [FASE 3a] Usar parser robusto centralizado
        parsed = _extraer_json_robusto(content)
        if isinstance(parsed, dict) and len(parsed) > 0:
            return parsed

        logger.warning("optimizar_lote_top3: IA no devolvio JSON valido. Contenido recibido: %s", content[:200])
        return {}

