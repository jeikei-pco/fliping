"""Cliente WebSocket privado de OKX.

Suscribe los canales privados de OKX en tiempo real:
  - orders  → fills ejecutados
  - account → balance USDT
  - positions → posiciones abiertas + PnL no realizado
  - fills   → historial de operaciones recientes
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import ssl
import time
from threading import Event, Thread, Lock
from typing import Callable, Dict, Any, Optional, List

import websocket

logger = logging.getLogger("UAO_Sclaping.OKXPrivateWS")


class OKXPrivateWS(Thread):
    """
    WebSocket privado de OKX.

    Además de disparar on_fill_callback cuando una orden se llena,
    mantiene datos live en atributos públicos que el orquestador
    puede leer directamente sin llamar a la API REST:

        ws.live_balance   → {"usdt_total": float, "usdt_available": float}
        ws.live_positions → [{"symbol", "side", "qty", "entry_price", "pnl", "upnl_pct"}, ...]
        ws.live_orders    → [{"order_id", "symbol", "side", "price", "qty", "status", "reduce_only"}, ...]
        ws.live_fills     → [{"side", "price", "qty", "realized_pnl", "total_monto", "time", "symbol"}, ...]  (últimos 50)
    """

    def __init__(
        self,
        key: str,
        secret: str,
        passphrase: str,
        is_demo: bool,
        on_fill_callback: Callable[[dict], None],
    ):
        super().__init__(daemon=True)
        self.key = key
        self.secret = secret
        self.passphrase = passphrase
        self.is_demo = bool(is_demo)
        self.callback = on_fill_callback
        self.stop_event = Event()
        self.ws: Optional[websocket.WebSocket] = None

        # ── Datos live en memoria ─────────────────────────────────────────────
        self._lock = Lock()
        self.live_balance: Dict[str, float] = {}
        self.live_positions: List[Dict[str, Any]] = []
        self.live_orders: List[Dict[str, Any]] = []
        self.live_fills: List[Dict[str, Any]] = []
        # Timestamps de última actualización por canal
        self._ts_balance:   float = 0.0
        self._ts_positions: float = 0.0
        self._ts_orders:    float = 0.0
        # ─────────────────────────────────────────────────────────────────────

        if self.is_demo:
            self.url = "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999"
        else:
            self.url = "wss://ws.okx.com:8443/ws/v5/private"

    # ── Propiedades de lectura thread-safe ───────────────────────────────────

    def get_live_balance(self) -> Dict[str, float]:
        with self._lock:
            return dict(self.live_balance)

    def get_live_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.live_positions)

    def get_live_orders(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.live_orders)

    def get_live_fills(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.live_fills)

    def has_live_balance(self) -> bool:
        return self._ts_balance > 0

    def has_live_positions(self) -> bool:
        return self._ts_positions > 0

    def has_live_orders(self) -> bool:
        return self._ts_orders > 0

    # ── Procesadores de canales ───────────────────────────────────────────────

    def _process_account(self, data: list):
        """Canal 'account': balance total y disponible en USDT."""
        for entry in data:
            details = entry.get("details") or []
            for d in details:
                if str(d.get("ccy", "")).upper() == "USDT":
                    eq   = float(d.get("eq")    or d.get("cashBal") or 0)
                    avail = float(d.get("availEq") or d.get("availBal") or eq)
                    with self._lock:
                        self.live_balance = {"usdt_total": eq, "usdt_available": avail}
                    self._ts_balance = time.time()
                    logger.debug("💰 [WS] Balance USDT: total=%.4f avail=%.4f", eq, avail)

    def _process_positions(self, data: list):
        """Canal 'positions': posiciones abiertas con PnL no realizado."""
        positions = []
        for p in data:
            pos_sz = float(p.get("pos") or p.get("notionalUsd") or 0)
            if abs(pos_sz) < 1e-9:
                continue
            symbol   = p.get("instId", "")
            side_raw = str(p.get("posSide") or "").lower()
            if side_raw == "net":
                side_raw = "long" if pos_sz > 0 else "short"
            side  = "LONG" if side_raw == "long" else "SHORT"
            qty   = abs(float(p.get("pos") or 0))
            entry = float(p.get("avgPx") or 0)
            upnl  = float(p.get("upl")   or 0)
            positions.append({
                "symbol":      symbol,
                "side":        side,
                "qty":         qty,
                "entry_price": entry,
                "pnl":         upnl,
            })
        with self._lock:
            self.live_positions = positions
        self._ts_positions = time.time()
        logger.debug("📊 [WS] Posiciones live: %d", len(positions))

    def _process_orders(self, data: list):
        """Canal 'orders': órdenes abiertas actualizadas en tiempo real."""
        with self._lock:
            # Actualizar/insertar cada orden recibida
            orders_map = {o["order_id"]: o for o in self.live_orders}
            for o in data:
                state = str(o.get("state") or "").lower()
                ord_id = str(o.get("ordId") or "")
                if state in {"filled", "canceled"}:
                    orders_map.pop(ord_id, None)
                    continue
                if state in {"live", "partially_filled"}:
                    orders_map[ord_id] = {
                        "order_id":    ord_id,
                        "symbol":      o.get("instId", ""),
                        "side":        str(o.get("side") or "").upper(),
                        "price":       float(o.get("px") or 0),
                        "qty":         float(o.get("sz") or 0),
                        "status":      "OPEN",
                        "reduce_only": bool(o.get("reduceOnly") or False),
                    }
            self.live_orders = list(orders_map.values())
        self._ts_orders = time.time()
        logger.debug("📋 [WS] Órdenes abiertas live: %d", len(self.live_orders))

    def _process_fills(self, data: list):
        """Canal 'orders' con fill: guarda historial de operaciones."""
        now = time.time()
        for o in data:
            fill_sz = float(o.get("fillSz") or o.get("accFillSz") or 0)
            state   = str(o.get("state") or "").lower()
            if fill_sz > 0 and state in {"filled", "partially_filled"}:
                price_f = float(o.get("fillPx") or o.get("avgPx") or o.get("px") or 0)
                ts_ms   = int(o.get("fillTime") or o.get("uTime") or o.get("cTime") or (now * 1000))
                bucket  = (ts_ms // 1000 // 300) * 300
                entry = {
                    "side":         str(o.get("side") or "").upper(),
                    "price":        price_f,
                    "qty":          fill_sz,
                    "total_monto":  price_f * fill_sz,
                    "realized_pnl": float(o.get("pnl") or 0),
                    "time":         bucket,
                    "symbol":       o.get("instId", ""),
                }
                with self._lock:
                    self.live_fills.append(entry)
                    self.live_fills = self.live_fills[-50:]
                # Disparar callback para que el engine actualice su estado
                self.callback(o)

    # ── Conexión ─────────────────────────────────────────────────────────────

    def _login_payload(self) -> dict:
        timestamp = str(time.time())
        message   = f"{timestamp}GET/users/self/verify"
        digest    = hmac.new(self.secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
        sign      = base64.b64encode(digest).decode("utf-8")
        return {
            "op": "login",
            "args": [{"apiKey": self.key, "passphrase": self.passphrase, "timestamp": timestamp, "sign": sign}],
        }

    def _connect_once(self):
        headers = ["x-simulated-trading: 1"] if self.is_demo else None
        self.ws = websocket.create_connection(
            self.url, header=headers, sslopt={"cert_reqs": ssl.CERT_NONE}, timeout=20
        )
        self.ws.send(json.dumps(self._login_payload()))
        login_msg = json.loads(self.ws.recv())
        if login_msg.get("event") == "error" or login_msg.get("code") not in (None, "0"):
            raise RuntimeError(f"login privado OKX fallo: {login_msg}")

        # Suscribir TODOS los canales privados necesarios
        self.ws.send(json.dumps({
            "op": "subscribe",
            "args": [
                {"channel": "orders",    "instType": "SWAP"},
                {"channel": "account",   "ccy": "USDT"},
                {"channel": "positions", "instType": "SWAP"},
            ],
        }))
        logger.info("✅ Conectado WS Privado OKX (%s) — canales: orders + account + positions",
                    "DEMO" if self.is_demo else "REAL")

        while not self.stop_event.is_set():
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                self.ws.send("ping")
                continue

            if not raw or raw == "pong":
                continue

            try:
                msg = json.loads(raw)
            except Exception:
                continue

            event = msg.get("event")
            if event in {"subscribe", "login", "channel-conn-count"}:
                continue
            if event == "error":
                logger.error("Error WS privado OKX: %s", msg)
                continue

            channel = msg.get("arg", {}).get("channel", "")
            data    = msg.get("data") or []

            if channel == "account":
                self._process_account(data)
            elif channel == "positions":
                self._process_positions(data)
            elif channel == "orders":
                self._process_orders(data)
                self._process_fills(data)

    def run(self):
        if not (self.key and self.secret and self.passphrase):
            logger.warning("WS Privado OKX no iniciado: faltan credenciales")
            return

        while not self.stop_event.is_set():
            try:
                self._connect_once()
            except Exception as exc:
                logger.error("WS Privado OKX desconectado/error: %s", exc)
                try:
                    if self.ws:
                        self.ws.close()
                except Exception:
                    pass
                if not self.stop_event.wait(5):
                    logger.info("Reconectando WS Privado OKX...")

    def stop(self):
        self.stop_event.set()
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass