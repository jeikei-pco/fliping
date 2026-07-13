 El Nuevo Flujo de Trabajo (Pipeline Cuantitativo)Para implementar esto, vamos a modificar main.py o el orquestador para que ejecute el pipeline de esta manera:Screener (Filtro Base): Obtenemos el Top 20 usando el filtro de min_qty y avg_body_pct (velas grandes y operables).  Optimización IA (Individual): Por cada uno de los 20 símbolos, llamamos al AIOptimizerWorker para obtener el grid_spacing y leverage ideal antes de probarlo.Backtest Optimizado: Corremos el fast_backtester usando, para cada símbolo, los parámetros específicos que la IA devolvió para él.Selección: El símbolo con el mayor PnL neto tras comisiones es el ganador.Operación: Se lanza el Grid con los parámetros ya optimizados.2. Implementación Técnica en main.py (Resumen del cambio)Debemos cambiar la lógica dentro de tu watchdog_loop en main.py para inyectar la IA antes del backtest:Python# --- Lógica Propuesta en main.py ---

# 1. Obtenemos Top 20
top_20 = await scan_all_usdt_futures(...) 

# 2. Optimizamos cada uno ANTES del backtest
optimized_targets = []
for symbol_data in top_20:
    # Pedimos a la IA la configuración ideal para este símbolo
    ai_params = await get_ai_grid_params(symbol_data, user_config)
    # Guardamos los parámetros junto a los datos del símbolo
    symbol_data['ai_params'] = ai_params
    optimized_targets.append(symbol_data)

# 3. Corremos Backtest usando esos parámetros optimizados
# Modificaremos run_vectorized_backtest para que use symbol_data['ai_params']
results = await run_vectorized_backtest(..., optimized_targets, ...)

# 4. Seleccionamos el ganador del backtest
winner = results[0] 

# 5. Operamos
engine_instance = OkxWsClient(..., ai_recommendation=winner['ai_params'], ...)
3. Modificaciones NecesariasA. En fast_backtester.pyDebes actualizar la función run_vectorized_backtest para que no calcule los niveles de forma estática (num_grids = 10), sino que lea ai_params del símbolo:  grid_spacing = center_price * symbol_data['ai_params']['grid_spacing_factor']num_grids = symbol_data['ai_params']['grid_lines']B. El problema del Rate Limit (Optimización)Llamar a la IA 20 veces seguidas (una por cada símbolo del Top 20) puede bloquear tu API Key de OpenRouter/Gemini o saturar el Rate Limit de OKX.Solución: Implementaremos un caché local en el worker: si ya optimizaste BTC/USDT hace menos de 1 hora, reutiliza esos parámetros en lugar de volver a consultar a la IA.