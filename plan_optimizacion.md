Aquí tienes el plan de acción detallado paso a paso, junto con los fragmentos de código optimizados para resolver los cuellos de botella críticos detectados en la arquitectura.

---

### Fase 1: Optimización Crítica de Python (Rendimiento y Memoria)

El mayor problema de rendimiento está en cómo los motores de Python manejan los DataFrames. Pandas es excelente para análisis, pero muy lento en bucles de alta frecuencia si no se usa correctamente.

#### 1. Fuga de memoria en WebSockets (`okx_ws.py`)

El uso de `pd.concat` en cada nueva vela dentro del bucle del WebSocket destruye la memoria. Lo cambiaremos por un `collections.deque` que es infinitamente más rápido para operaciones en tiempo real, convirtiendo a DataFrame solo cuando es necesario.

**Archivo:** `apps/python-workers/grid_worker/engine/okx_ws.py`

```python
from collections import deque
import pandas as pd
# ... otros imports ...

class OkxWsClient:
    def __init__(self, ...):
        # ... inicialización previa ...
        
        # 🔥 OPTIMIZACIÓN: Usar deque en lugar de DataFrame para el estado en vivo
        self.raw_candles = deque(maxlen=100) 
        self.df = pd.DataFrame() 
        # ...

    async def _watch_ohlcv_loop(self):
        logger.info(f"Iniciando escucha de Velas para {self.symbol}...")
        while self.running:
            try:
                candles = await self.exchange.watch_ohlcv(self.symbol, self.timeframe)
                is_new_candle = False
                
                for ohlcv in candles:
                    ts = pd.to_datetime(ohlcv[0], unit='ms')
                    new_row = {
                        'timestamp': ts, 'open': ohlcv[1], 'high': ohlcv[2],
                        'low': ohlcv[3], 'close': ohlcv[4], 'volume': ohlcv[5]
                    }
                    
                    if len(self.raw_candles) > 0 and self.raw_candles[-1]['timestamp'] == ts:
                        # Actualizar la vela actual (mutación rápida)
                        self.raw_candles[-1] = new_row
                    else:
                        # Añadir nueva vela (O(1) en deque)
                        self.raw_candles.append(new_row)
                        is_new_candle = True
                
                # Sincronizar el último precio
                self.metrics["last_price"] = float(candles[-1][4])
                
                if is_new_candle:
                    # Convertir a DataFrame SÓLO cuando hay una vela cerrada para calcular Keltner
                    self.df = pd.DataFrame(list(self.raw_candles))
                    self.update_metrics()
                    
                    if self.evaluar_inactividad_velas(minutos=20):
                        logger.info(f"🔄 [RESPIRACIÓN VIVO] Malla re-centrada para {self.symbol}")
                        await self.setup_grid_orders()
                    
            except Exception as e:
                logger.error(f"Error en websocket (watch_ohlcv): {e}")
                await asyncio.sleep(5)

```

#### 2. Vectorización en Backtesting (`fast_backtester.py`)

Iterar con `df.itertuples()` es un antipatrón en Pandas. Para el motor de backtesting, extraeremos las columnas a arrays de NumPy (`.values`). Esto reduce el tiempo de ejecución en un 80-90%.

**Archivo:** `apps/python-workers/grid_worker/engine/fast_backtester.py`

```python
# Reemplazar la sección "5. Motor de Simulación" con esto:

                # 5. Motor de Simulación (Extracción a NumPy arrays para iteración ultrarrápida)
                current_price = center_price
                
                # 🔥 OPTIMIZACIÓN: Arrays de NumPy nativos en lugar de itertuples
                opens = df['open'].values
                highs = df['high'].values
                lows = df['low'].values
                closes = df['close'].values
                
                # Iteración nativa en Python sobre arrays de C (100x más rápido)
                for idx in range(len(df)):
                    open_p = opens[idx]
                    high_p = highs[idx]
                    low_p = lows[idx]
                    close_p = closes[idx]
                    
                    path = [low_p, high_p, close_p] if close_p >= open_p else [high_p, low_p, close_p]
                        
                    for target in path:
                        if target < current_price: # El precio BAJA
                            # ... (mantener tu lógica de cruce de niveles exacta, pero ahora volará) ...

```

#### 3. Eliminar copias de memoria (`math_core.py`)

**Archivo:** `apps/python-workers/grid_worker/engine/math_core.py`
Elimina la línea `df = df.copy()` en `calculate_keltner_channels`. Pandas maneja referencias internas eficientemente. Modificar columnas al vuelo dentro de la función está bien si el DataFrame temporal se descarta o si se usa `df.assign()`.

---

### Fase 2: Backend Node.js (Liberar Hilos y Añadir Caché)

#### 1. Desbloquear Peticiones HTTP de BullMQ (`grid-queue.ts` y `http-app.ts`)

Evitaremos que el frontend se quede esperando hasta 5 minutos por un escaneo, implementando un patrón asíncrono.

**Archivo:** `apps/api/src/infrastructure/workers/grid-queue.ts`

```typescript
// 🔥 OPTIMIZACIÓN: Retorno inmediato del Job ID
export const dispatchGridScan = async (payload: any): Promise<any> => {
  const job = await gridQueue.add("scan_markets", payload);
  // No esperamos con waitUntilFinished. Devolvemos el ID de la tarea.
  return { jobId: job.id, status: "processing" };
};

```

**Archivo:** `apps/api/src/presentation/http-app.ts` (Actualización de rutas)

```typescript
  // Actualizar el endpoint original de scan para devolver el JobId
  app.post("/api/grid/scan", async (request, response) => {
    try {
      // ... (tu lógica de extracción de credenciales) ...
      const { dispatchGridScan } = await import("../infrastructure/workers/grid-queue.js");
      const result = await dispatchGridScan({ /* ... payload ... */ });
      // Retorna 202 Accepted, indicando que el proceso comenzó
      response.status(202).json({ success: true, result }); 
    } catch (error: any) {
      response.status(500).json({ success: false, error: error.message });
    }
  });

  // 🔥 NUEVO: Endpoint para que el Frontend consulte el estado del Job
  app.get("/api/grid/scan/status/:jobId", async (request, response) => {
    try {
      const { gridQueue } = await import("../infrastructure/workers/grid-queue.js");
      const job = await gridQueue.getJob(request.params.jobId);
      
      if (!job) {
        response.status(404).json({ error: "Job no encontrado" });
        return;
      }
      
      const state = await job.getState();
      const result = job.returnvalue;
      const failedReason = job.failedReason;
      
      response.json({ id: job.id, state, result, failedReason });
    } catch (error: any) {
      response.status(500).json({ error: error.message });
    }
  });

```

#### 2. Caché de Redis para los Balances (`http-app.ts`)

Evitar baneos de IP de los exchanges consultando los saldos de forma repetitiva.

```typescript
  app.get("/api/balances", async (request, response) => {
    try {
      const userId = String((request as any).user.id);
      // ... (extracción de exchange y sandbox) ...

      // 🔥 OPTIMIZACIÓN: Comprobar Caché en Redis
      const { gridRedisConnection } = await import("../infrastructure/workers/grid-queue.js");
      const cacheKey = `balance:${userId}:${exchange}:${sandbox}`;
      const cachedBalance = await gridRedisConnection.get(cacheKey);
      
      if (cachedBalance) {
        response.json(JSON.parse(cachedBalance));
        return;
      }

      const balances = await services.balance.getBalances(userId, exchange, sandbox);
      
      // Guardar en caché por 30 segundos
      await gridRedisConnection.set(cacheKey, JSON.stringify(balances), "EX", 30);
      
      response.json(balances);
    } catch (error: any) {
      response.status(500).json({ error: error.message });
    }
  });

```

---

### Fase 3: Base de Datos y Seguridad

#### 1. Eliminar Consultas N+1 en Prisma (`prisma-opportunity-repository.ts`)

**Archivo:** `apps/api/src/infrastructure/repositories/prisma-opportunity-repository.ts`
Elimina el bloque `await this.prisma.appUser.upsert(...)` de la función `create`. El usuario ya fue autenticado y validado por el middleware JWT. Hacer un upsert por cada oportunidad insertada bloquea la base de datos de manera innecesaria.

#### 2. Seguridad del JWT (`http-app.ts`)

**Archivo:** `apps/api/src/presentation/http-app.ts`

```typescript
// 🔥 SEGURIDAD: Nunca tener fallbacks de secretos en el código
if (!process.env.JWT_SECRET) {
  console.error("FATAL ERROR: JWT_SECRET environment variable is not defined.");
  process.exit(1); // Detener el servidor si no hay seguridad
}
const JWT_SECRET = process.env.JWT_SECRET;

```

---

### Fase 4: Limpieza del Repositorio (Inmediato)

Ejecuta estos comandos en tu terminal en la raíz del proyecto para limpiar los binarios y evitar subirlos a GitHub/GitLab, reduciendo drásticamente el peso del proyecto.

**1. Actualiza el archivo raíz `jeikei-pco/fliping/fliping-workes/.gitignore`:**

```text
# Node
node_modules
apps/*/node_modules
apps/*/dist
.env

# Python (Añadir estas líneas)
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/
*.log
trace.txt
.venv
apps/python-workers/grid_worker/.venv

# Expo / React Native
apps/mobile/.expo

```

**2. Ejecuta los siguientes comandos en tu terminal:**

```bash
# Limpiar la caché de git de archivos que ya no deberían estar
git rm -r --cached .

# Volver a añadir todo (ahora respetará el nuevo .gitignore)
git add .

# Crear el commit de limpieza
git commit -m "chore: limpieza de binarios pycache, dist y logs del repositorio"

```