import sys
import asyncio
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

from engine.okx_ws import OkxWsClient, detect_active_exchange_grid, _create_exchange
from engine.consistency_screener import scan_all_usdt_futures
from engine.fast_backtester import run_vectorized_backtest
from engine.ai_optimizer import get_ai_grid_params_batch
from engine.optimizer_integrator import optimize_grid_params

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

async def test_exchange_connection(exchange_id, api_key, secret, passphrase, sandbox):
    mode = "DEMO/SANDBOX" if sandbox else "REAL"
    logger.info(f"Probando conexión a {exchange_id} ({mode})...")
    exchange = _create_exchange(exchange_id, api_key, secret, passphrase, sandbox=sandbox)
    try:
        await exchange.fetch_balance()
        logger.info(f"✅ Conexión EXITOSA a {exchange_id} ({mode}) - API Privada accesible.")
        return True
    except Exception as e:
        logger.error(f"❌ Fallo en conexión a {exchange_id} ({mode}): {e}")
        return False
    finally:
        await exchange.close()

async def watchdog_loop(exchange_id, api_key, secret, passphrase, sandbox):
    global engine_instance, redis_client, best_opportunity, current_task_name, ai_recommendation
    
    logger.info("Watchdog Loop iniciado.")
    
    while True:
        try:
            current_task_name = f"Consultando {exchange_id}"
            grid_status = await detect_active_exchange_grid(exchange_id, api_key, secret, passphrase, sandbox)
            
            user_config = {}
            if redis_client:
                try:
                    config_raw = await redis_client.get("grid:config")
                    if config_raw: user_config = json.loads(config_raw)
                except Exception: pass
                
            base_capital = float(user_config.get("baseCapital", 50))
            max_leverage = float(user_config.get("maxLeverage", 15))

            # --- RAMA 1: EXISTE GRID ACTIVO ---
            if grid_status['has_active_grid']:
                active_symbol = grid_status['symbol']
                logger.info(f"Grid real detectado en {active_symbol}. Reanudando monitoreo...")
                
                if not engine_instance or not engine_instance.running:
                    engine_instance = OkxWsClient(
                        exchange_id, api_key, secret, passphrase, sandbox, 
                        symbol=active_symbol, timeframe="5m", base_capital=base_capital, resume_existing_grid=True
                    )
                    asyncio.create_task(engine_instance.start())
                
                current_task_name = "Grid (Trading)"

            # --- RAMA 2: NO HAY GRID, INICIAR BÚSQUEDA ---
            else:
                current_task_name = "Screener"
                logger.info("No hay grid activo. Iniciando escaneo global...")

                all_symbols = await scan_all_usdt_futures(exchange_id, api_key, secret, passphrase, sandbox)
                
                if all_symbols:
                    # 1. Tomar solo el Top 20 más prometedor (Ahorra tokens de IA y tiempo)
                    top_candidates = all_symbols[:20]
                    
                    # 2. Optimización IA en bloque para el Top 20
                    current_task_name = "Optimizador IA"
                    logger.info(f"Solicitando parámetros a la IA para el Top {len(top_candidates)}...")
                    ai_batch_result = await get_ai_grid_params_batch(top_candidates, user_config)
                    
                    # Inyectar resultados (o fallback) en los candidatos
                    for sym_data in top_candidates:
                        symbol = sym_data['symbol']
                        if symbol in ai_batch_result:
                            sym_data['ai_params'] = ai_batch_result[symbol]
                        else:
                            sym_data['ai_params'] = optimize_grid_params(None, sym_data)

                    # 3. Backtest Masivo (Ahora sí, usando los parámetros de la IA)
                    current_task_name = "Backtest"
                    logger.info("Ejecutando Backtest con los parámetros optimizados...")
                    results = await run_vectorized_backtest(
                        exchange_id, api_key, secret, passphrase, top_candidates, sandbox, base_capital, max_leverage
                    )

                    if results and len(results) > 0:
                        top_20_details = [f" - {r['symbol']} | PnL: {r['pnl_after_fees']:.2f} USDT | Ops: {r['trades']}" for r in results[:20]]
                        logger.info(f"Top ganadores por PnL en el Backtest:\n" + "\n".join(top_20_details))
                        
                        if redis_client:
                            await redis_client.set("grid:top20", json.dumps(results[:20]))
                            await redis_client.set("grid:backtest_top10", json.dumps(results[:10]))

                        # 4. Selección del Ganador Definitivo
                        winner = results[0]
                        best_opportunity = winner
                        ai_recommendation = winner.get('ai_params', {})
                        
                        logger.info(f"🏆 GANADOR: {winner['symbol']} | PnL Neto: {winner['pnl_after_fees']:.4f} USDT | Origen Params: {ai_recommendation.get('source', 'desconocido')}")

                        # 5. Iniciar Motor de Trading
                        if not engine_instance or not engine_instance.running:
                            logger.info(f"Iniciando Grid en el ganador: {winner['symbol']}")
                            engine_instance = OkxWsClient(
                                exchange_id, api_key, secret, passphrase, sandbox,
                                symbol=winner['symbol'], timeframe="5m", base_capital=base_capital,
                                ai_recommendation=ai_recommendation, resume_existing_grid=False
                            )
                            asyncio.create_task(engine_instance.start())

                        current_task_name = "Grid (Trading)"

        except Exception as e:
            logger.error(f"Error en el watchdog scanner: {e}")
        
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
        sandbox = data.get("sandbox", True)
        exchange_id = data.get("exchange", "okx")
        
        if not api_key or not secret:
            return {"status": "Error", "message": "Credenciales incompletas"}
            
        await test_exchange_connection(exchange_id, api_key, secret, passphrase, sandbox=sandbox)
        watchdog_task = asyncio.create_task(watchdog_loop(exchange_id, api_key, secret, passphrase, sandbox))
            
        return {"status": "Started", "message": "Watchdog Autónomo iniciado."}

    elif job.name == "stop":
        logger.info("Recibido comando stop. Deteniendo operaciones...")
        if watchdog_task is not None and not watchdog_task.done():
            watchdog_task.cancel()
            watchdog_task = None
            
        if engine_instance:
            engine_instance.running = False # Apagado seguro del loop
            
        if redis_client:
            await redis_client.set("grid:metrics", json.dumps({"status": "Offline", "message": "Apagado por el usuario"}))
            
        return {"status": "Stopped", "message": "Motor Grid detenido."}

    return {"status": "Ignored", "message": "Comando no reconocido."}

async def main():
    global redis_client, metrics_task
    
    redis_host = os.getenv("REDIS_HOST", "127.0.0.1")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    
    logger.info(f"Conectando a Redis en {redis_host}:{redis_port}")
    redis_client = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
    
    worker = Worker("GridWorkerQueue", process_job, {"connection": {"host": redis_host, "port": redis_port}})
    
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
            engine_instance.running = False
        if redis_client:
            await redis_client.close()
        await worker.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker detenido por el usuario.")
