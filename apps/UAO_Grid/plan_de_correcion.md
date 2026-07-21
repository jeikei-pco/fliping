Fase 1: Estabilidad Inmediata y Lógica Core (Prioridad 1)
Objetivo: Proteger las órdenes, evitar bloqueos y lograr el deslizamiento correcto de la malla.

Implementar TPs Inamovibles: Modificar _desplazar_grid en core/engine.py para que, al detectar un breakout, extraiga y proteja los TPs actuales (ej. abs(level) >= 100), regenere solo las órdenes base y las fusione evitando solapamientos.

Asegurar Mutación de Listas: Reemplazar el uso de .pop() dentro de iteraciones en core/engine.py por reconstrucciones limpias de la lista para evitar errores de concurrencia.

Activar WAL en SQLite: Añadir PRAGMA journal_mode=WAL; en la conexión de core/database.py para permitir lecturas y escrituras simultáneas sin bloquear el bot.

Calibrar el Watchdog: Ajustar temporalmente el WATCHDOG_PRICE_DRIFT_PCT en el .env para evitar reinicios en falso por alta volatilidad o spreads anchos.

Fase 2: Vectorización y Velocidad (Prioridad 2)
Objetivo: Eliminar los cuellos de botella para escanear cientos de símbolos en tiempo real.

Reescribir el Backtest Dinámico: Eliminar df.iterrows() en _simular_grid_dinamico (core/backtester.py).

Migrar a NumPy: Convertir los cálculos de iteración a vectores de NumPy, lo que reducirá el tiempo de procesamiento de cada símbolo de segundos a milisegundos.

Fase 3: Seguridad de la IA (Prioridad 3)
Objetivo: Evitar que fallos en la respuesta del LLM rompan la configuración.

Robustecer el Parser JSON: Reemplazar el conteo manual de llaves {} en core/ai_optimizer.py por un parseo estricto con expresiones regulares o una librería como json_repair.

Filtros de Seguridad (Clamping): Añadir topes matemáticos en GridBuilder para rechazar variables destructivas si la IA llega a alucinar un apalancamiento masivo o un espaciado imposible.

Fase 4: Limpieza y Eventos (Prioridad 4)
Objetivo: Dejar el código listo para escalar y añadir nuevos exchanges en el futuro.

Limpieza de Archivos: Eliminar copias de seguridad redundantes (database_clean.py, database_original.py, fix_db.py).

Completar Event Bus: Mover las llamadas directas de cancelación/creación de órdenes del GridOrquestador al bus de eventos (core/events.py) para que la infraestructura y el motor de trading queden totalmente desacoplados.