"""Ciclo transaccional del Grid, sin dependencias de OKX/CCXT."""
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Dict, Optional, Set

from core.models import TradeFill


class CycleState(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    WAITING_TP = "WAITING_TP"
    TP_FILLED = "TP_FILLED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


@dataclass
class GridCycle:
    cycle_id: str
    base_level: int
    entry_side: str
    entry_price: float = 0.0
    entry_qty: float = 0.0
    entry_fee: float = 0.0
    entry_time: float = 0.0
    tp_side: str = ""
    tp_price: float = 0.0
    tp_qty: float = 0.0
    tp_fee: float = 0.0
    tp_time: float = 0.0
    state: CycleState = CycleState.PENDING
    order_id: str = ""
    level_id: Optional[int] = None

    def to_dict(self):
        data = asdict(self)
        data["state"] = self.state.value
        return data


class CycleManager:
    transitions = {
        CycleState.PENDING: {CycleState.FILLED, CycleState.CANCELLED},
        CycleState.FILLED: {CycleState.WAITING_TP, CycleState.CANCELLED},
        CycleState.WAITING_TP: {CycleState.TP_FILLED, CycleState.CANCELLED},
        CycleState.TP_FILLED: {CycleState.COMPLETED},
        CycleState.COMPLETED: set(),
        CycleState.CANCELLED: set(),
    }

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.cycles: Dict[str, GridCycle] = {}
        self.blocked_levels: Set[int] = set()

    def _move(self, cycle: GridCycle, state: CycleState):
        if state not in self.transitions[cycle.state]:
            raise ValueError(f"Transicion invalida {cycle.state.value} -> {state.value}")
        cycle.state = state

    def create_cycle(self, fill: TradeFill, base_level: int) -> GridCycle:
        cycle = GridCycle(
            cycle_id=f"{self.symbol}:{int(fill.timestamp * 1000)}:{uuid.uuid4().hex[:8]}",
            base_level=int(base_level), entry_side=fill.side,
            entry_price=fill.price, entry_qty=fill.qty,
            entry_fee=fill.fee, entry_time=fill.timestamp,
            order_id=fill.order_id, level_id=fill.level_id,
        )
        self._move(cycle, CycleState.FILLED)
        self._move(cycle, CycleState.WAITING_TP)
        self.cycles[cycle.cycle_id] = cycle
        self.blocked_levels.add(cycle.base_level)
        return cycle

    def complete_cycle(self, cycle_id: str, fill: TradeFill) -> GridCycle:
        cycle = self.cycles[cycle_id]
        if cycle.state != CycleState.WAITING_TP:
            raise ValueError(f"Ciclo {cycle_id} no espera TP")
        cycle.tp_price = fill.price
        cycle.tp_qty += fill.qty
        cycle.tp_fee += fill.fee
        cycle.tp_time = fill.timestamp
        self._move(cycle, CycleState.TP_FILLED)
        self._move(cycle, CycleState.COMPLETED)
        self.blocked_levels.discard(cycle.base_level)
        return cycle

    def cancel_cycle(self, cycle_id: str) -> GridCycle:
        cycle = self.cycles[cycle_id]
        if cycle.state not in (CycleState.COMPLETED, CycleState.CANCELLED):
            self._move(cycle, CycleState.CANCELLED)
            self.blocked_levels.discard(cycle.base_level)
        return cycle

    def snapshot(self):
        return {key: cycle.to_dict() for key, cycle in self.cycles.items()}

    def restore(self, raw_cycles: dict, blocked_levels=None):
        self.cycles = {}
        for cycle_id, raw in (raw_cycles or {}).items():
            data = dict(raw)
            data["state"] = CycleState(data.get("state", CycleState.CANCELLED))
            fields = {key: data[key] for key in GridCycle.__dataclass_fields__ if key != "cycle_id" and key in data}
            self.cycles[cycle_id] = GridCycle(cycle_id=cycle_id, **fields)
        active = {c.base_level for c in self.cycles.values() if c.state in (CycleState.FILLED, CycleState.WAITING_TP, CycleState.TP_FILLED)}
        self.blocked_levels = set(blocked_levels or active)
