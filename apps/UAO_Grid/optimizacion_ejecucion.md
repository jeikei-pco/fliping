# Plan de Implementación Scrum

# Refactor de `providers.py` y `engine.py`

## Arquitectura Objetivo: Provider (Infraestructura) + Engine (Lógica de Negocio)

---

# Objetivo

Separar completamente las responsabilidades entre ambos módulos para que el sistema sea escalable, desacoplado y orientado a eventos.

El objetivo final es:

```text
Exchange
      │
      ▼
providers.py
      │
(Eventos + Objetos de Dominio)
      │
      ▼
engine.py
      │
(Ciclos del Grid)
      │
      ▼
Analizador
      │
      ▼
IA
      │
      ▼
Optimizador
      │
      ▼
Backtest
      │
      ▼
Trading
```

---

# Product Goal

Al finalizar el proyecto:

* `providers.py` será la única capa que conoce el Exchange (OKX/CCXT).
* `engine.py` no tendrá llamadas REST ni WebSocket.
* Toda decisión de trading estará centralizada en el Engine.
* Todo cambio en el Exchange llegará mediante eventos.

---

# Product Backlog

## Epic 1

### Refactor de providers.py

Objetivo

Convertir Provider en una capa de infraestructura.

---

## Epic 2

Refactor de engine.py

Objetivo

Convertir Engine en el cerebro del Grid.

---

## Epic 3

Arquitectura Event Driven

---

## Epic 4

Persistencia

---

## Epic 5

Integración IA

---

# Sprint 1

# Provider Domain Model

Objetivo

Normalizar todos los objetos del Exchange.

---

## Historia 1

Ampliar

```text
Order
```

Agregar

```text
client_order_id

exchange_order_id

filled_qty

remaining_qty

created_at

updated_at

strategy_id

cycle_id

order_role
```

---

## Historia 2

Ampliar

```text
Position
```

Agregar

```text
average_price

fees

realized_pnl

unrealized_pnl

mark_price

updated_at
```

---

## Historia 3

Crear

```text
TradeFill
```

```text
fill_id

order_id

symbol

side

price

qty

fee

maker

timestamp
```

---

## Entregable

Provider con modelos tipados.

---

# Sprint 2

# Provider Services

Objetivo

Eliminar cualquier acceso directo al Exchange desde Engine.

---

Crear servicios

```text
OrderService

PositionService

BalanceService

FillService

MarketService
```

Cada uno será responsable de una sola cosa.

---

Ejemplo

```text
OrderService

create()

cancel()

modify()

sync()
```

---

Entregable

Engine deja de usar CCXT.

---

# Sprint 3

# Provider Event Bus

Objetivo

Todo cambio llega mediante eventos.

---

Crear

```text
ExecutionEvent
```

Tipos

```text
ORDER_CREATED

ORDER_UPDATED

ORDER_FILLED

ORDER_CANCELLED

POSITION_UPDATED

BALANCE_UPDATED
```

---

Crear

```text
ExecutionListener
```

---

Engine se suscribe.

Nunca consulta continuamente el Exchange.

---

Entregable

Arquitectura Event Driven.

---

# Sprint 4

# Refactor Engine

Objetivo

Eliminar responsabilidades de infraestructura.

Mover fuera del Engine

* REST
* WebSocket
* Parseo JSON
* Conversión CCXT
* Reintentos de conexión
* Sincronización Exchange

Todo pasa al Provider.

---

El Engine conserva únicamente

```text
GridState

TradeCycle

Inventory

Rebalance

Dynamic TP

Risk

Recovery
```

---

Entregable

Engine simplificado.

---

# Sprint 5

# TradeCycle Manager

Nuevo módulo

```text
cycle_manager.py
```

Responsable

```text
crear ciclo

cerrar ciclo

cancelar ciclo

recuperar ciclo
```

---

Engine únicamente llama

```text
CycleManager
```

---

Entregable

Toda la lógica de ciclos desacoplada.

---

# Sprint 6

# GridState Manager

Crear

```text
grid_state.py
```

Mantiene

```text
niveles activos

niveles bloqueados

niveles disponibles

inventario

órdenes pendientes
```

---

Engine consulta

```text
GridState
```

Nunca listas propias.

---

Entregable

Estado centralizado.

---

# Sprint 7

# Rebalance Engine

Crear

```text
rebalance.py
```

Responsabilidad

Calcular

```text
TP

distancia rentable

fees

profit mínimo

cantidad TP

precio TP
```

---

Entrada

```text
TradeFill
```

Salida

```text
TakeProfitOrder
```

---

Engine solo ejecuta

```text
Provider.create_order()
```

---

Entregable

TP totalmente desacoplado.

---

# Sprint 8

# Inventory Manager

Crear

```text
inventory.py
```

Mantener

```text
cantidad disponible

cantidad bloqueada

cantidad vendida

cantidad comprada

capital utilizado
```

---

El Engine deja de calcular inventario.

---

# Sprint 9

# Persistencia

Crear

```text
persistence.py
```

Guardar

```text
GridState

TradeCycle

Inventory

Orders

Positions
```

---

Al reiniciar

```text
Provider

↓

Persistence

↓

Engine
```

reconstruye el estado automáticamente.

---

# Sprint 10

# Integración con Analizador e IA

Nuevo flujo

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

GridEngine

↓

Provider
```

El Engine recibe únicamente

```text
OptimizationProfile
```

Nunca calcula

* ATR
* Volatilidad
* Riesgo
* Score

Solo ejecuta la estrategia.

---

# Nueva Arquitectura

```text
apps/
└── UAO_Grid/
    └── core/
        ├── providers.py          # Infraestructura del Exchange
        ├── engine.py             # Orquestador del Grid
        ├── cycle_manager.py      # Gestión de ciclos
        ├── grid_state.py         # Estado del Grid
        ├── rebalance.py          # Cálculo TP y rebalanceo
        ├── inventory.py          # Gestión de inventario
        ├── persistence.py        # Recuperación y guardado
        ├── events.py             # Eventos del Provider
        ├── models.py             # Objetos de dominio
        └── services/
            ├── order_service.py
            ├── fill_service.py
            ├── position_service.py
            ├── balance_service.py
            └── market_service.py
```

---

# Responsabilidades Finales

## providers.py

Responsable de:

* Comunicación con OKX mediante REST y WebSocket.
* Conversión de respuestas del exchange a objetos de dominio (`Order`, `Position`, `TradeFill`).
* Gestión de órdenes, posiciones, balances y datos de mercado.
* Emisión de eventos (`ORDER_FILLED`, `POSITION_UPDATED`, etc.).
* Sincronización y reconciliación con el exchange.

**No debe contener:**

* Cálculo de TP.
* Rebalanceo.
* Gestión de ciclos.
* Estrategias de Grid.
* Reglas de negocio.

---

## engine.py

Responsable de:

* Orquestar el ciclo de vida del Grid.
* Mantener `TradeCycle` y `GridState`.
* Procesar eventos emitidos por el Provider.
* Calcular y solicitar la creación de órdenes derivadas (TP, rebalanceos) mediante módulos especializados.
* Coordinar el inventario y la recuperación del estado.
* Integrarse con el `OptimizationProfile` generado por Analizador → IA → Optimizador → Backtester.

**No debe contener:**

* Llamadas directas a CCXT.
* Manejo de REST o WebSocket.
* Parseo de respuestas del exchange.
* Reconexión o sincronización de infraestructura.

---

# Definition of Done (DoD)

Cada sprint se considerará completado cuando:

* No exista dependencia directa de `engine.py` hacia CCXT o la API del exchange.
* `providers.py` sea la única capa de acceso al exchange.
* Toda interacción entre ambos módulos ocurra mediante objetos de dominio y eventos.
* La lógica de negocio del Grid esté desacoplada en componentes (`CycleManager`, `GridState`, `Rebalance`, `Inventory`).
* El estado del Grid pueda recuperarse tras un reinicio sin inconsistencias.
* Existan pruebas unitarias e integradas para Provider, Engine y los módulos de apoyo.

## Beneficios esperados

* **Mantenibilidad:** cambios en OKX o CCXT afectan solo a `providers.py`.
* **Escalabilidad:** soporte para otros exchanges implementando nuevos Providers sin modificar el Engine.
* **Trazabilidad:** cada ciclo y cada orden quedan claramente identificados.
* **Testabilidad:** la lógica del Grid puede probarse con Providers simulados (mock) sin depender del exchange real.
* **Preparación para IA:** el Engine recibe configuraciones ya optimizadas y se concentra exclusivamente en ejecutarlas de forma consistente.
