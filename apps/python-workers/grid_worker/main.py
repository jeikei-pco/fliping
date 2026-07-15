import sys
import asyncio
import os
import os

# Cargar variables de entorno desde tests/.env
env_path = os.path.join(os.path.dirname(__file__), "tests", ".env")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip("\"'")

import logging
import json
import redis.asyncio as redis
from bullmq import Worker

from engine.okx_ws import OkxWsClient, detect_active_exchange_grid, _create_okx_exchange
from engine.consistency_screener import scan_all_usdt_futures
from engine.fast_backtester import run_vectorized_backtest
from engine.ai_optimizer import get_ai_grid_params_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("GridWorker")

# Variables globales para el estado del engine
engine_instance = None
redis_client = None
metrics_task = None
watchdog_task = None
best_opportunity = None
current_task_name = "Standby"
ai_recommendation = None

async def update_redis_metrics():
    """ Tarea en segundo plano para publicar las métricas a Redis periódicamente """
    global engine_instance, redis_client, best_opportunity, current_task_name, ai_recommendation
    while True:
        if redis_client:
            payload = {
                "status": "Running" if (engine_instance and engine_instance.running) else "Online",
                "task": current_task_name
            }
            
            if engine_instance and engine_instance.running:
                payload.update(engine_instance.metrics)
            
            if best_opportunity:
                payload["best_opportunity"] = best_opportunity
            
            if ai_recommendation:
                payload["ai_recommendation"] = ai_recommendation

            try:
                await redis_client.set("grid:metrics", json.dumps(payload))
            except Exception as e:
                pass
        await asyncio.sleep(2)

async def test_okx_connection(api_key, secret, passphrase, sandbox):
    """
    Comprueba si las credenciales son válidas para acceder a la API privada.
    """
    mode = "DEMO/SANDBOX" if sandbox else "REAL"
    logger.info(f"Probando conexión a OKX ({mode})...")
    exchange = _create_okx_exchange(api_key, secret, passphrase, sandbox=sandbox)
    try:
        # fetch_balance es un endpoint privado ideal para validar credenciales y conexión
        await exchange.fetch_balance()
        logger.info(f"✅ Conexión EXITOSA a OKX ({mode}) - API Privada accesible.")
        return True
    except Exception as e:
        logger.error(f"❌ Fallo en conexión a OKX ({mode}): {e}")
        return False
    finally:
        await exchange.close()

async def watchdog_loop(api_key, secret, passphrase, sandbox):
    global engine_instance, redis_client, best_opportunity, current_task_name
    
    logger.info("Watchdog Loop iniciado.")
    
    while True:
        try:
            # 1. Consultar a OKX el estado real de la cuenta
            current_task_name = "Consultando OKX"
            logger.info("Consultando OKX para verificar si existe un grid real activo...")
            
            grid_status = await detect_active_exchange_grid(api_key, secret, passphrase, sandbox)
            
            # Leer configuración del usuario
            user_config = {}
            if redis_client:
                try:
                    config_raw = await redis_client.get("grid:config")
                    if config_raw:
                        user_config = json.loads(config_raw)
                except Exception:
                    pass
            base_capital = float(user_config.get("baseCapital", 50))
            max_leverage = float(user_config.get("maxLeverage", 15))

            # --- RAMA 1: EXISTE GRID ACTIVO ---
            if grid_status['has_active_grid']:
                active_symbol = grid_status['symbol']
                logger.info(f"Grid real detectado en {active_symbol} (Posiciones: {grid_status['position_count']}, Órdenes: {grid_status['entry_order_count']}).")
                logger.info("Reanudando monitoreo sin recrear órdenes...")
                
                if not engine_instance or not engine_instance.running:
                    engine_instance = OkxWsClient(
                        api_key, secret, passphrase, sandbox, 
                        symbol=active_symbol, 
                        timeframe="5m",
                        base_capital=base_capital,
                        resume_existing_grid=True
                    )
                    asyncio.create_task(engine_instance.start())
                
                current_task_name = "Grid (Trading)"

            # --- RAMA 2: NO HAY GRID, INICIAR BÚSQUEDA ---
            else:
                current_task_name = "Screener"
                logger.info("No hay grid activo. Iniciando escaneo global y backtest automático...")

                all_symbols = await scan_all_usdt_futures(api_key, secret, passphrase, sandbox)
                if all_symbols:
                    
                    from engine.optimizer_integrator import optimize_grid_params
                    
                    current_task_name = "Optimizador (Heurístico)"
                    logger.info(f"Optimizando {len(all_symbols)} símbolos rápidamente con integrador heurístico...")

                    optimized_targets = []
                    for sym_data in all_symbols:
                        # Asignar directamente usando el fallback matemático
                        sym_data['ai_params'] = optimize_grid_params(None, sym_data)
                        optimized_targets.append(sym_data)

                    logger.info(f"Optimización completada. {len(optimized_targets)} símbolos listos para backtest.")

                    # --- Backtest masivo ---
                    current_task_name = "Backtest (Masivo)"
                    results = await run_vectorized_backtest(
                        api_key, secret, passphrase, optimized_targets, sandbox, base_capital, max_leverage
                    )
                    logger.info(f"Backtest terminado en {len(results)} símbolos.")

                    if results and len(results) > 0:
                        # Extraer Top 20 por PnL neto
                        top_20 = results[:20]
                        top_20_details = [f" - {r['symbol']} | PnL: {r['pnl_after_fees']:.2f} USDT | Ops: {r['trades']}" for r in top_20]
                        logger.info(f"Top 20 ganadores por PnL en el Backtest:\n" + "\n".join(top_20_details))
                        
                        if redis_client:
                            await redis_client.set("grid:top20", json.dumps(top_20))

                        winner = top_20[0]
                        
                        # --- Optimización IA FINAL solo para el ganador ---
                        logger.info(f"🏆 Ganador seleccionado: {winner['symbol']}. Solicitando validación/optimización final a IA...")
                        try:
                            # Importar la función de IA
                            from engine.ai_optimizer import get_ai_grid_params_batch
                            # Intentar optimizar el ganador (enviando una lista de 1 elemento)
                            ai_batch_result = await get_ai_grid_params_batch([winner], user_config)
                            
                            # Si la IA responde correctamente para ese símbolo, reemplazamos sus parámetros
                            if winner['symbol'] in ai_batch_result and ai_batch_result[winner['symbol']]:
                                logger.info(f"✅ IA optimizó exitosamente el ganador {winner['symbol']}.")
                                winner['ai_params'] = ai_batch_result[winner['symbol']]
                            else:
                                logger.warning(f"⚠️ IA no respondió o falló para el ganador. Manteniendo parámetros heurísticos.")
                        except Exception as e:
                            logger.error(f"Error al llamar a IA para el ganador: {e}. Manteniendo parámetros heurísticos.")
                            
                        best_opportunity = winner

                        if redis_client:
                            await redis_client.set("grid:backtest_top10", json.dumps(results[:10]))

                        logger.info(
                            f"Mejor símbolo: {winner['symbol']} | "
                            f"PnL: {winner['pnl']:.4f} | "
                            f"PnL neto (fees): {winner['pnl_after_fees']:.4f} | "
                            f"Leverage IA: {winner.get('leverage_used', '?')}x"
                        )

                        # ai_params ya viene dentro del winner (sin llamada extra a la IA)
                        ai_rec = winner.get('ai_params', {})

                        global ai_recommendation
                        ai_recommendation = ai_rec
                        logger.info(f"Parámetros IA aplicados: {ai_rec}")

                        if not engine_instance or not engine_instance.running:
                            logger.info(f"Iniciando Grid en el ganador: {winner['symbol']}")
                            engine_instance = OkxWsClient(
                                api_key, secret, passphrase, sandbox,
                                symbol=winner['symbol'],
                                timeframe="5m",
                                base_capital=base_capital,
                                ai_recommendation=ai_rec,
                                resume_existing_grid=False
                            )
                            asyncio.create_task(engine_instance.start())

                        current_task_name = "Grid (Trading)"

        except Exception as e:
            logger.error(f"Error en el watchdog scanner: {e}")
        
        # Esperar 1 hora antes de la próxima comprobación
        logger.info(f"Watchdog durmiendo por 1 hora... (Current Task: {current_task_name})")
        await asyncio.sleep(60 * 60 * 1)

async def process_job(job, job_token):
    global engine_instance, metrics_task, watchdog_task
    
    logger.info(f"Procesando job {job.name} (ID: {job.id})")
    
    if job.name == "auto_start":
        if watchdog_task is not None and not watchdog_task.done():
            return {"status": "Already Running", "message": "El Watchdog autónomo ya está corriendo."}
            
        data = job.data
        api_key = data.get("apiKey")
        secret = data.get("secret")
        passphrase = data.get("passphrase")
        # Usar el valor de sandbox que viene en el job (configurado en la API)
        sandbox = data.get("sandbox", True)
        
        mode_str = "DEMO/SANDBOX" if sandbox else "REAL"
        logger.info(f"🔐 Modo {mode_str} activo: Credenciales OKX obtenidas desde la base de datos (vía Node).")
        
        if not api_key or not secret:
            logger.error("Credenciales incompletas enviadas al worker.")
            return {"status": "Error", "message": "Credenciales incompletas"}
            
        # Comprobación de conexión a la red correspondiente según configuración
        mode_str = "DEMO/SANDBOX" if sandbox else "REAL"
        logger.info(f"Realizando comprobación de conexión a la API de OKX en modo {mode_str}...")
        await test_okx_connection(api_key, secret, passphrase, sandbox=sandbox)
        
        watchdog_task = asyncio.create_task(watchdog_loop(api_key, secret, passphrase, sandbox))
            
        return {"status": "Started", "message": "Watchdog Autónomo iniciado."}

    elif job.name == "stop":
        logger.info("Recibido comando stop. Deteniendo operaciones...")
        if watchdog_task is not None and not watchdog_task.done():
            watchdog_task.cancel()
            watchdog_task = None
            
        if engine_instance:
            await engine_instance.stop()
            
        # Actualizamos redis con el estado Off
        if redis_client:
            payload = {"status": "Offline", "message": "Apagado por el usuario"}
            await redis_client.set("grid:metrics", json.dumps(payload))
            
        return {"status": "Stopped", "message": "Motor Grid detenido."}

    logger.warning(f"Job ignorado en modo autónomo: {job.name}")
    return {"status": "Ignored", "message": "El motor ahora es 100% autónomo y rechaza comandos manuales."}

async def main():
    global redis_client
    
    redis_host = os.getenv("REDIS_HOST", "127.0.0.1")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    
    logger.info(f"Conectando a Redis en {redis_host}:{redis_port}")
    
    redis_client = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
    
    worker = Worker(
        "GridWorkerQueue",
        process_job,
        {"connection": {"host": redis_host, "port": redis_port}}
    )
    
    # Start metrics task immediately so the app shows it as Online
    global metrics_task
    if metrics_task is None:
        metrics_task = asyncio.create_task(update_redis_metrics())
    
    logger.info("🚀 Grid Worker (Python) Autónomo iniciado y escuchando comandos de bootstrap")
    
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Cerrando worker...")
    finally:
        if engine_instance:
            await engine_instance.stop()
        if redis_client:
            await redis_client.close()
        await worker.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker detenido por el usuario.")
