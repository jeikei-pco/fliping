"""Eventos de ejecucion emitidos por el Provider y el GridOrquestador.

[FASE 4] Event Bus extendido con eventos de ciclo de vida de ordenes:
  - ORDER_CANCEL_REQUESTED  → solicita cancelar todas las ordenes de un simbolo
  - ORDERS_PLACE_REQUESTED  → solicita colocar un lote de ordenes
  - GRID_SLIDE_REQUESTED    → solicita deslizar la malla a un nuevo centro

Esto permite que GridOrquestador emita eventos en lugar de llamar directamente
a provider.cancel_all_orders / provider.create_order, desacoplando la
infraestructura del motor de trading.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List


class ExecutionEventType(str, Enum):
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_UPDATED = "ORDER_UPDATED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    POSITION_UPDATED = "POSITION_UPDATED"
    BALANCE_UPDATED = "BALANCE_UPDATED"
    # [FASE 4] Nuevos eventos de solicitud emitidos por el orquestador
    ORDER_CANCEL_REQUESTED = "ORDER_CANCEL_REQUESTED"
    ORDERS_PLACE_REQUESTED = "ORDERS_PLACE_REQUESTED"
    GRID_SLIDE_REQUESTED = "GRID_SLIDE_REQUESTED"


@dataclass(frozen=True)
class ExecutionEvent:
    event_type: ExecutionEventType
    payload: Any
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class ExecutionEventBus:
    def __init__(self):
        self._listeners: Dict[ExecutionEventType, List[Callable]] = {}

    def subscribe(self, event_type: ExecutionEventType, listener: Callable):
        self._listeners.setdefault(event_type, []).append(listener)

    def publish(self, event: ExecutionEvent):
        for listener in tuple(self._listeners.get(event.event_type, [])):
            listener(event)
