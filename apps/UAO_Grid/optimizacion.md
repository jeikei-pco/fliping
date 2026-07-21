## Plan de Implementación Scrum

### Proyecto: Integración del Analizador → IA → Optimizador → Backtester → Trading

---

# Objetivo General

Rediseñar el flujo de decisión para que **cada símbolo sea completamente independiente**, donde el Analizador produzca un perfil cuantitativo completo, la IA genere recomendaciones específicas, el Optimizador construya la configuración final, el Backtester la valide y el Orquestador opere únicamente configuraciones rentables.

---

# Arquitectura Objetivo

```text
            Scanner
                │
                ▼
      Analizador Cuantitativo
                │
                ▼
      Perfil Completo Symbol
                │
                ▼
        IA Táctica (LLM)
                │
                ▼
      Overrides por Symbol
                │
                ▼
      Optimizador Matemático
                │
                ▼
 Configuración Final del Grid
                │
                ▼
         Backtester
                │
                ▼
      Score de Rentabilidad
                │
                ▼
         Ranking Final
                │
                ▼
        Grid Engine
                │
                ▼
         Trading Real

──────────────────────────────────────

        IA Estratégica (24h)

 Histórico de operaciones
        ↓
Aprendizaje por símbolo
        ↓
Overrides persistentes
```

---

# Product Backlog

## Epic 1

### Nuevo Pipeline Inteligente

Objetivo

Separar completamente cada etapa del proceso.

Historias

* Como motor de trading quiero que el Analizador entregue un perfil completo por símbolo.
* Como Optimizador quiero recibir únicamente el perfil del Analizador y los ajustes IA.
* Como Backtester quiero ejecutar exactamente la configuración optimizada.
* Como Orquestador quiero recibir un único objeto consolidado.

---

## Epic 2

IA Táctica

Objetivo

Crear una IA online que participe durante cada ciclo de escaneo.

---

## Epic 3

IA Estratégica

Objetivo

Mantener aprendizaje histórico independiente.

---

## Epic 4

Nuevo modelo de datos

Objetivo

Eliminar múltiples diccionarios dispersos.

---

# Sprint 1

## Refactor Analizador

Duración

1 semana

Objetivo

Que el Analizador produzca absolutamente toda la información necesaria.

### Tareas

### Crear AnalysisProfile

```python
AnalysisProfile

symbol

market_data

volatility

trend

grid

risk

capital

execution

metadata
```

---

### Analizador devuelve

```python
{
    symbol

    analysis
}
```

Eliminar múltiples variables sueltas.

---

### Agregar

* Grid Profile

* Risk Profile

* Capital Profile

* Trend Profile

* Execution Profile

---

### Entregable

```python
analysis_profile
```

único.

---

# Sprint 2

## IA Táctica

Duración

1 semana

---

Crear nuevo módulo

```
ai_runtime_optimizer.py
```

Responsabilidad

Recibir

```
AnalysisProfile
```

y responder

```
RuntimeOverrides
```

Ejemplo

```python
{
    leverage_factor

    density_factor

    grid_step_factor

    capital_factor

    preferred_mode
}
```

No toca base de datos.

No aprende.

No guarda nada.

---

Entregable

```
RuntimeOverrides
```

---

# Sprint 3

## Refactor del Optimizador

Duración

1 semana

---

Entrada

```
AnalysisProfile

+

RuntimeOverrides

+

HistoricalOverrides
```

Salida

```
OptimizationProfile
```

Ejemplo

```python
{

grid_spacing

grid_lines

leverage

capital

risk

mode

expected_profit

}
```

No vuelve a calcular ATR.

No vuelve a calcular Score.

No vuelve a calcular Riesgo.

Solo transforma.

---

# Sprint 4

## Refactor Backtester

Duración

1 semana

---

Entrada

```
OptimizationProfile
```

Salida

```
BacktestProfile
```

Ejemplo

```python
{

ROI

PnL

PF

WinRate

DD

Trades

}
```

No modifica parámetros.

No optimiza.

Solo valida.

---

# Sprint 5

## Refactor Orquestador

Duración

1 semana

Pipeline

```
Scanner

↓

Analizador

↓

IA Runtime

↓

Optimizador

↓

Backtester

↓

Ranking

↓

Trading
```

Cada símbolo mantiene

```
AnalysisProfile

OptimizationProfile

BacktestProfile
```

sin mezclarse.

---

# Sprint 6

## Nuevo Ranking

Actualmente

```
Score
```

Nuevo

```
Ranking =
40% Backtest

30% Analysis

20% IA

10% Liquidez
```

Ejemplo

```
BTC

Analysis 91

Backtest 97

IA 88

Liquidity 100

Final 94.3
```

---

# Sprint 7

## Persistencia

Guardar

```
AnalysisProfile

OptimizationProfile

BacktestProfile
```

por símbolo.

Esto permitirá posteriormente entrenar modelos.

---

# Sprint 8

## IA Estratégica

Mantener

```
AIOptimizerWorker
```

Pero cambiar su entrada.

Actualmente

```
Trades

Scanner
```

Nuevo

```
Analysis Profiles

Optimization Profiles

Backtests

Trades

Drawdowns

Rotaciones

Profit Factor

Tiempo en mercado

Capital utilizado
```

La IA aprenderá sobre decisiones completas.

---

# Sprint 9

## Telemetría

Dashboard

Por símbolo visualizar

```
Analysis

↓

IA Runtime

↓

Optimization

↓

Backtest

↓

Trading

↓

Resultado Real
```

Permitirá saber exactamente dónde falla un símbolo.

---

# Sprint 10

## Validación automática

Agregar pruebas

```
Analizador

↓

Optimizador

↓

Backtest
```

para 100 símbolos.

Comparar

```
Configuración antigua

vs

Nueva arquitectura
```

---

# Definición de Done (DoD)

Cada historia estará completa cuando:

* El componente tenga pruebas unitarias.
* El flujo no recalcule métricas ya obtenidas.
* Cada símbolo conserve su contexto independiente.
* El tiempo de procesamiento por símbolo no aumente más de un 10%.
* El backtest utilice exactamente los parámetros producidos por el optimizador.
* El orquestador solo opere configuraciones validadas.

---

# Riesgos identificados

| Riesgo                                        | Impacto | Mitigación                                                                                                         |
| --------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------ |
| Incremento de latencia por IA táctica         | Alto    | Ejecutar IA en paralelo y usar caché con tiempo de vida configurable.                                              |
| Inconsistencia entre IA táctica y estratégica | Medio   | Definir reglas claras de precedencia (por ejemplo, IA estratégica como base e IA táctica como ajuste fino).        |
| Regresiones durante el refactor               | Alto    | Mantener compatibilidad temporal mediante adaptadores y pruebas de regresión.                                      |
| Sobrecarga del LLM                            | Medio   | Limitar llamadas por símbolo, reutilizar respuestas cuando el perfil cambie poco y definir un modo de degradación. |

---

# Roadmap estimado

| Sprint    | Objetivo                                                     |
| --------- | ------------------------------------------------------------ |
| Sprint 1  | Refactor del Analizador y creación de `AnalysisProfile`      |
| Sprint 2  | Implementación de la IA táctica (`RuntimeOverrides`)         |
| Sprint 3  | Refactor del Optimizador y creación de `OptimizationProfile` |
| Sprint 4  | Refactor del Backtester y creación de `BacktestProfile`      |
| Sprint 5  | Integración del nuevo pipeline en el Orquestador             |
| Sprint 6  | Nuevo sistema de ranking multicriterio                       |
| Sprint 7  | Persistencia de perfiles y trazabilidad                      |
| Sprint 8  | Evolución de la IA estratégica basada en perfiles completos  |
| Sprint 9  | Dashboard y telemetría del flujo de decisión                 |
| Sprint 10 | Validación, pruebas de carga y despliegue                    |

Este plan mantiene entregas incrementales y funcionales en cada sprint, reduce el riesgo de una reescritura completa y permite validar el rendimiento del nuevo pipeline antes de avanzar al siguiente nivel de complejidad.
