import sys
import asyncio
import os
import logging
import json
import redis.asyncio as redis
from bullmq import Worker

# Cargar variables de entorno desde tests/.env (si existe)
env_path = os.path.join(os.path.dirname(__file__), "tests", ".env")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip("\"'")

# Importaciones del motor (Arquitectura Hexagonal / Inyección de dependencias)
from engine.exchange_ports import EnvironmentAdapter
from engine.ccxt_controller import CCXTController
from engine.okx_ws import OkxWsClient, detect_active_exchange_grid
from engine.consistency_screener import scan_all_usdt_futures
from engine.fast_backtester import run_vectorized_backtest
from engine.ai_optimizer import get_ai_grid_params_batch
from engine.optimizer_integrator import optimize_grid_params

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("GridWorker")

# Variables globales de estado
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
            except Exception:
                pass
        await asyncio.sleep(2)


async def watchdog_loop(controller: CCXTController):
    """
    Bucle principal de vigilancia.
    Recibe el controlador de CCXT inyectado y lo reparte a las sub-rutinas.
    """
    global engine_instance, redis_client, best_opportunity, current_task_name, ai_recommendation
    
    logger.info("Watchdog Loop iniciado.")
    
    while True:
        try:
            current_task_name = f"Consultando {controller.exchange_id}"
            
            # 1. Detectar si ya hay un grid activo usando el controlador
            grid_status = await detect_active_exchange_grid(controller)
            
            # 2. Obtener configuración del usuario desde Redis
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
                    # Instanciar Bot con inyección del controlador
                    engine_instance = OkxWsClient(
                        controller=controller, 
                        symbol=active_symbol, 
                        timeframe="15m", 
                        base_capital=base_capital, 
                        resume_existing_grid=True
                    )
                    asyncio.create_task(engine_instance.start())
                
                current_task_name = "Grid (Trading)"

            # --- RAMA 2: NO HAY GRID, INICIAR BÚSQUEDA ---
            else:
                current_task_name = "Screener"
                logger.info("No hay grid activo. Iniciando escaneo global...")

                # Pasar el controlador al Screener
                all_symbols = await scan_all_usdt_futures(controller)
                
                if all_symbols:
                    # Top 20 más prometedor
                    top_candidates = all_symbols[:20]
                    
                    current_task_name = "Optimizador IA"
                    logger.info(f"Solicitando parámetros a la IA para el Top {len(top_candidates)}...")
                    ai_batch_result = await get_ai_grid_params_batch(top_candidates, user_config)
                    
                    for sym_data in top_candidates:
                        symbol = sym_data['symbol']
                        if symbol in ai_batch_result:
                            sym_data['ai_params'] = ai_batch_result[symbol]
                        else:
                            sym_data['ai_params'] = optimize_grid_params(None, sym_data)

                    current_task_name = "Backtest"
                    logger.info("Ejecutando Backtest con los parámetros optimizados...")
                    
                    # Pasar el controlador al Backtester
                    results = await run_vectorized_backtest(
                        controller=controller, 
                        symbols=top_candidates, 
                        investment=base_capital, 
                        max_leverage=max_leverage
                    )

                    if results and len(results) > 0:
                        top_20_details = [f" - {r['symbol']} | PnL: {r['pnl_after_fees']:.2f} USDT | Ops: {r['trades']}" for r in results[:20]]
                        logger.info(f"Top ganadores por PnL en el Backtest:\n" + "\n".join(top_20_details))
                        
                        if redis_client:
                            await redis_client.set("grid:top20", json.dumps(results[:20]))
                            await redis_client.set("grid:backtest_top10", json.dumps(results[:10]))

                        winner = results[0]
                        best_opportunity = winner
                        ai_recommendation = winner.get('ai_params', {})
                        
                        logger.info(f"🏆 GANADOR: {winner['symbol']} | PnL Neto: {winner['pnl_after_fees']:.4f} USDT")

                        if not engine_instance or not engine_instance.running:
                            logger.info(f"Iniciando Grid en el ganador: {winner['symbol']}")
                            
                            # Instanciar nuevo Bot con el controlador
                            engine_instance = OkxWsClient(
                                controller=controller,
                                symbol=winner['symbol'], 
                                timeframe="15m", 
                                base_capital=base_capital,
                                ai_recommendation=ai_recommendation, 
                                resume_existing_grid=False
                            )
                            asyncio.create_task(engine_instance.start())

                        current_task_name = "Grid (Trading)"

        except Exception as e:
            logger.error(f"Error en el watchdog scanner: {e}", exc_info=True)
        
        logger.info(f"Watchdog durmiendo por 1 hora... (Current Task: {current_task_name})")
        await asyncio.sleep(60 * 60 * 1)


async def process_job(job, job_token):
    """ Procesador de comandos provenientes de BullMQ (Node.js) """
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
            
        try:
            # 1. Ensamblaje Arquitectónico: Instanciamos Adaptador y Controlador
            adapter = EnvironmentAdapter(api_key, secret, passphrase, sandbox)
            controller = CCXTController(exchange_id, adapter)
            
            # 2. Probamos conexión inicial a través del controlador
            await controller.get_instance().fetch_balance()
            logger.info(f"✅ Conexión validada exitosamente vía Controlador ({'SANDBOX' if sandbox else 'REAL'}).")
            
            # 3. Lanzamos el Watchdog pasándole el controlador
            watchdog_task = asyncio.create_task(watchdog_loop(controller))
            
            return {"status": "Started", "message": "Watchdog Autónomo iniciado con Inyección de Dependencias."}
            
        except Exception as e:
            logger.error(f"❌ Fallo de conexión o inicialización: {e}", exc_info=True)
            return {"status": "Error", "message": str(e)}

    elif job.name == "stop":
        logger.info("Recibido comando stop. Deteniendo operaciones...")
        if watchdog_task is not None and not watchdog_task.done():
            watchdog_task.cancel()
            watchdog_task = None
            
        if engine_instance:
            engine_instance.running = False 
            if hasattr(engine_instance, 'exchange'):
                # Cerramos la conexión compartida ordenadamente
                await engine_instance.exchange.close()
            
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