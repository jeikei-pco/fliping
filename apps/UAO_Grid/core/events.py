"""Eventos de ejecucion emitidos por el Provider."""
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
