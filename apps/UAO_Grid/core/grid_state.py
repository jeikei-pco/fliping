"""Estado de niveles del Grid, separado del ciclo de ejecucion."""
from typing import Any, Dict, Iterable, List, Set


class GridState:
    def __init__(self):
        self.levels: List[Dict[str, Any]] = []
        self.blocked_levels: Set[int] = set()
        self.pending_orders: Dict[str, Any] = {}

    def set_levels(self, levels: Iterable[Dict[str, Any]]):
        self.levels = [dict(level) for level in levels]

    def available_levels(self):
        return [level for level in self.levels if level.get("level") not in self.blocked_levels]

    def block(self, level: int):
        self.blocked_levels.add(int(level))

    def unblock(self, level: int):
        self.blocked_levels.discard(int(level))

    def snapshot(self):
        return {"levels": self.levels, "blocked_levels": sorted(self.blocked_levels), "pending_orders": self.pending_orders}
