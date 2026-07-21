# Plan de Implementación Scrum

# Epic: Eliminación de la Configuración Estática del Grid

## Arquitectura "Backtest Driven Grid"

---

# Objetivo General

Eliminar completamente cualquier cálculo estático dentro de `apps/UAO_Grid/core/engine.py`.

El `GridEngine` dejará de decidir:

* Espaciado del Grid
* Número de líneas
* Apalancamiento
* Capital
* Distancia de rebalanceo
* Profit mínimo
* Modo Long/Short/Neutral

Estos parámetros serán calculados por el pipeline:

```text
Scanner
      │
      ▼
Analizador
      │
      ▼
IA Runtime
      │
      ▼
Optimizador
      │
      ▼
Backtest
      │
      ▼
ValidatedOptimizationProfile
      │
      ▼
GridEngine
```

El Engine únicamente ejecutará la configuración validada.

---

# Objetivos Técnicos

Eliminar completamente del Engine:

```text
calcular_espaciado_atr()

calcular_lineas()

calcular_leverage()

calcular_spacing()

spacing fijo

grid dinámico por ATR

parámetros hardcodeados
```

Toda la información vendrá desde el Backtest.

---

# Product Backlog

## Epic

### Backtest Driven Grid

Objetivo

El Engine no calcula.

El Engine ejecuta.

---

# Sprint 1

# Definición del nuevo contrato

Objetivo

Crear un objeto único que represente la configuración ganadora.

---

## Historia 1

Crear

```python
ValidatedOptimizationProfile
```

---

Debe contener

```python
symbol

analysis

optimization

backtest

metadata
```

---

## optimization

```python
grid_spacing_pct

grid_lines

capital

capital_factor

leverage

rebalance_distance

min_profit_pct

maker_fee

taker_fee

preferred_mode

inventory_limit

max_orders

grid_direction
```

---

## analysis

```python
atr

atr_pct

volatility

risk

trend

grid_quality

market_phase

oscillation

score
```

---

## backtest

```python
ROI

PnL

Drawdown

ProfitFactor

WinRate

Trades

Expectancy

Sharpe

Calmar
```

---

### Entregable

Nuevo contrato entre módulos.

---

# Sprint 2

# Refactor Backtest

Objetivo

El Backtest ya no devolverá únicamente estadísticas.

Devolverá la configuración completa.

---

Actualmente

```python
return {

ROI

WinRate

PnL

}
```

Nuevo

```python
return ValidatedOptimizationProfile(...)
```

---

El Backtest almacenará exactamente

```text
spacing utilizado

líneas utilizadas

capital utilizado

apalancamiento utilizado

profit utilizado

rebalance utilizado
```

---

### Entregable

Configuración completamente reproducible.

---

# Sprint 3

# Eliminar lógica del Engine

Objetivo

Eliminar cálculos internos.

---

Eliminar

```python
calcular_espaciado_atr()
```

---

Eliminar

```python
grid_spacing
```

calculado internamente.

---

Eliminar

```python
spacing ATR
```

---

Eliminar

```python
lineas calculadas
```

---

Eliminar

```python
leverage automático
```

---

Eliminar

```python
capital automático
```

---

Eliminar

```python
profit fijo
```

---

Eliminar

```python
distancias estáticas
```

---

### Entregable

Engine completamente desacoplado.

---

# Sprint 4

# Refactor inicializar_grid()

Objetivo

La inicialización utilizará únicamente el OptimizationProfile.

---

Actualmente

```python
inicializar_grid(

precio,

spacing,

lineas

)
```

Nuevo

```python
inicializar_grid(

ValidatedOptimizationProfile

)
```

---

Internamente

```python
spacing =
profile.optimization.grid_spacing_pct

lineas =
profile.optimization.grid_lines

capital =
profile.optimization.capital

leverage =
profile.optimization.leverage

profit =
profile.optimization.min_profit_pct
```

Nunca se recalculan.

---

### Entregable

Inicialización 100% reproducible.

---

# Sprint 5

# Crear GridBuilder

Objetivo

Extraer completamente la construcción de la malla.

---

Crear

```text
grid_builder.py
```

---

Responsable

```text
crear niveles BUY

crear niveles SELL

calcular precios

calcular cantidades

crear inventario inicial
```

---

Entrada

```python
ValidatedOptimizationProfile
```

Salida

```python
GridDefinition
```

---

El Engine únicamente hace

```python
grid = GridBuilder.build(profile)
```

---

### Entregable

Engine simplificado.

---

# Sprint 6

# GridDefinition

Crear

```python
GridDefinition
```

---

Debe contener

```python
symbol

grid_levels

buy_levels

sell_levels

spacing

capital

leverage

inventory

mode

rebalance_distance

profit_target
```

---

Cada nivel

```python
GridLevel

level

price

qty

side

status

cycle
```

---

### Entregable

Modelo único del Grid.

---

# Sprint 7

# Validación de Consistencia

Objetivo

Garantizar que el Grid utilizado sea exactamente el probado.

---

Agregar validaciones

```python
assert

profile.spacing

==

grid.spacing
```

---

```python
assert

profile.grid_lines

==

grid.lines
```

---

```python
assert

profile.leverage

==

grid.leverage
```

---

```python
assert

profile.capital

==

grid.capital
```

---

Si existe diferencia

Cancelar inicialización.

---

### Entregable

Consistencia garantizada.

---

# Sprint 8

# Eliminar Configuración Duplicada

Eliminar completamente

```text
GRID_SPACING

GRID_LEVELS

GRID_ATR_FACTOR

GRID_MULTIPLIER

DEFAULT_SPACING

DEFAULT_LINES

DEFAULT_GRID

DEFAULT_PROFIT
```

del Engine.

Permanecerán únicamente como valores de respaldo para simulaciones o pruebas, nunca para producción.

---

### Entregable

Una sola fuente de configuración.

---

# Sprint 9

# Recuperación

Cuando el sistema reinicie

No volverá a calcular

```text
spacing

líneas

capital
```

Simplemente cargará

```text
ValidatedOptimizationProfile

↓

GridDefinition

↓

Engine
```

---

### Entregable

Reinicio determinístico.

---

# Sprint 10

# Integración Completa

Flujo final

```text
Scanner

↓

Analizador

↓

IA Runtime

↓

Optimizador

↓

Backtest

↓

ValidatedOptimizationProfile

↓

GridBuilder

↓

GridDefinition

↓

GridEngine

↓

ExecutionProvider

↓

Exchange
```

---

# Cambios por Archivo

## analyzer.py

### Debe entregar

```text
AnalysisProfile
```

No conoce el Engine.

---

## ai_runtime_optimizer.py

Debe modificar únicamente

```text
OptimizationProfile
```

---

## optimizer.py

Debe generar

```text
OptimizationProfile
```

---

## backtester.py

Debe devolver

```text
ValidatedOptimizationProfile
```

---

## grid_builder.py (nuevo)

Responsable de construir la malla.

---

## engine.py

Debe:

* recibir `ValidatedOptimizationProfile`;
* solicitar a `GridBuilder` la construcción de la malla;
* gestionar eventos, ciclos e inventario;
* no calcular parámetros.

---

## providers.py

Debe:

* ejecutar órdenes;
* devolver fills;
* emitir eventos.

No conoce el Grid.

---

# Criterios de Aceptación

El cambio se considerará completo cuando:

### Funcionales

* El `GridEngine` no invoque `calcular_espaciado_atr()`.
* El `GridEngine` no utilice parámetros estáticos para inicializar la malla.
* La malla se construya exclusivamente desde `ValidatedOptimizationProfile`.
* El Backtest devuelva todos los parámetros utilizados durante la optimización.
* La malla creada en producción sea idéntica a la validada en el Backtest.

### Técnicos

* Ningún parámetro de configuración del Grid se duplique entre Analizador, Optimizador, Backtest y Engine.
* Toda la inicialización del Grid sea determinista y reproducible.
* El reinicio del sistema recupere exactamente la misma configuración sin recalcular parámetros.
* Las pruebas de integración demuestren que dos ejecuciones con el mismo `ValidatedOptimizationProfile` generan una `GridDefinition` idéntica.

---

# Beneficios Esperados

| Antes                                              | Después                                                          |
| -------------------------------------------------- | ---------------------------------------------------------------- |
| El Engine recalcula el espaciado con ATR           | El espaciado proviene del Backtest validado                      |
| Posibles diferencias entre simulación y producción | La configuración es exactamente la misma                         |
| Parámetros repartidos entre varios módulos         | Una única fuente de verdad (`ValidatedOptimizationProfile`)      |
| Difícil reproducir resultados                      | Ejecución completamente determinista                             |
| El Engine mezcla lógica de estrategia y ejecución  | El Engine se dedica únicamente a ejecutar la estrategia validada |

## Resultado final

Con este cambio el sistema adopta una arquitectura **Backtest Driven**, donde el ciclo **Analizador → IA → Optimizador → Backtest** es el único responsable de decidir la estrategia. El `GridEngine` deja de "pensar" y pasa a ser un **motor de ejecución**, garantizando que lo que se opera en el mercado es exactamente lo que fue optimizado y validado previamente. Esto elimina inconsistencias, facilita las pruebas, mejora la trazabilidad y prepara la plataforma para incorporar nuevas estrategias sin modificar el motor de ejecución.
