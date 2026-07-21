"""
watchdog.py — Watchdog REST. V2.

RESPONSABILIDAD ÚNICA: Detectar WebSocket congelado y desincronización de estado
entre la RAM y el exchange. No realiza trading. No calcula nada.

Separado del orquestador para reducir la complejidad de ese módulo.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from core.config import AppConfig
from core.models import GridEngineEvent, GridEvent

logger = logging.getLogger("UAO_Grid.Watchdog")


class WatchdogREST(threading.Thread):
    """
    Verifica periódicamente:
      1. WebSocket congelado (sin ticks por más de ws_timeout_seconds)
      2. Desincronización precio RAM vs REST (drift > price_drift_pct)
      3. Desincronización posición RAM vs exchange real

    Cuando detecta un problema, llama los callbacks inyectados:
      - on_ws_frozen(symbol):  notifica que el WS está congelado
      - on_price_drift(symbol, precio_rest):  notifica drift de precio
      - on_position_drift(symbol, posicion_real):  notifica drift de posición
    """

    def __init__(
        self,
        config: AppConfig,
        get_engines_fn: Callable[[], Dict[str, Any]],         # () → {symbol: GridEngine}
        get_positions_fn: Callable[[Optional[str]], list],    # (symbol?) → List[Position]
        get_ticker_fn: Callable[[str], float],                # (symbol) → precio_actual
        on_ws_frozen: Callable[[str], None],
        on_price_drift: Callable[[str, float], None],
        on_position_drift: Callable[[str, Any], None],
    ) -> None:
        super().__init__(daemon=True, name="WatchdogREST")

        self.config            = config
        self.get_engines_fn    = get_engines_fn
        self.get_positions_fn  = get_positions_fn
        self.get_ticker_fn     = get_ticker_fn
        self.on_ws_frozen      = on_ws_frozen
        self.on_price_drift    = on_price_drift
        self.on_position_drift = on_position_drift

        self._lock              = threading.Lock()
        self._stop_event        = threading.Event()
        self._ultimos_precios   : Dict[str, float] = {}
        self._ultimos_ticks_ts  : Dict[str, float] = {}

    # ── API pública ────────────────────────────────────────────────────────────

    def registrar_tick(self, symbol: str, precio: float) -> None:
        """Llamado por el WebSocket en cada tick de precio."""
        with self._lock:
            self._ultimos_precios[symbol]  = precio
            self._ultimos_ticks_ts[symbol] = time.time()

    def stop(self) -> None:
        self._stop_event.set()

    # ── Loop principal ─────────────────────────────────────────────────────────

    def run(self) -> None:
        intervalo   = self.config.watchdog.interval_seconds
        ws_timeout  = self.config.watchdog.ws_timeout_seconds
        drift_umbral = self.config.watchdog.price_drift_pct / 100.0

        logger.info(
            "🐕 Watchdog iniciado: intervalo=%ds | ws_timeout=%ds | drift=%.1f%%",
            intervalo, ws_timeout, drift_umbral * 100,
        )

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=intervalo)
            if self._stop_event.is_set():
                break
            try:
                self._verificar(ws_timeout, drift_umbral)
            except Exception as exc:
                logger.error("🐕 Watchdog error: %s", exc)

    def _verificar(self, ws_timeout: float, drift_umbral: float) -> None:
        engines = self.get_engines_fn()
        if not engines:
            return

        now = time.time()

        # ── 1. Detectar WS congelado ──────────────────────────────────────────
        with self._lock:
            for symbol, ts in list(self._ultimos_ticks_ts.items()):
                if (now - ts) > ws_timeout and symbol in engines:
                    logger.critical(
                        "🚨 WATCHDOG: Sin tick WS por %.0fs en %s — WS congelado!",
                        now - ts, symbol,
                    )
                    self.on_ws_frozen(symbol)

        # ── 2. Drift precio REST vs RAM ────────────────────────────────────────
        for symbol in list(engines.keys()):
            try:
                precio_rest = self.get_ticker_fn(symbol)
                if precio_rest <= 0:
                    continue

                with self._lock:
                    precio_ram = self._ultimos_precios.get(symbol, 0.0)

                if precio_ram > 0:
                    drift = abs(precio_rest - precio_ram) / (precio_ram + 1e-9)
                    if drift > drift_umbral:
                        logger.warning(
                            "🐕 WATCHDOG: Drift precio %s | RAM=%.4f vs REST=%.4f (%.2f%%)",
                            symbol, precio_ram, precio_rest, drift * 100,
                        )
                        self.on_price_drift(symbol, precio_rest)

                # ── 3. Drift posición RAM vs real ──────────────────────────────
                engine = engines.get(symbol)
                if not engine:
                    continue

                posiciones_reales = self.get_positions_fn(symbol)
                pos_ram = getattr(engine, "posicion_neta", 0.0)
                qty_real = posiciones_reales[0].qty if posiciones_reales else 0.0

                if abs(qty_real - abs(pos_ram)) > 1e-6:
                    logger.warning(
                        "🐕 WATCHDOG: Posición desincronizada %s | RAM=%.4f vs OKX=%.4f",
                        symbol, pos_ram, qty_real,
                    )
                    if posiciones_reales:
                        self.on_position_drift(symbol, posiciones_reales[0])

            except Exception as exc:
                logger.debug("Watchdog verificar %s: %s", symbol, exc)
