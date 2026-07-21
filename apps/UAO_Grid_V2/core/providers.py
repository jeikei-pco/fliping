"""
providers.py — Única capa que conoce CCXT y OKX. V2.

REGLA: Sin lógica de trading. Solo órdenes, posiciones, balances,
       datos de mercado (OHLCV, tickers) y reconciliación.

Cambios respecto a V1:
  - Recibe AppConfig en lugar de leer os.getenv directamente
  - Order y Position movidos a models.py
  - fetch_ohlcv() y fetch_tickers() son métodos públicos (analizador + backtester)
  - Base abstracta para multi-exchange en el futuro
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional

import ccxt
import pandas as pd

from core.config import AppConfig
from core.models import Order, OrderSide, OrderStatus, Position, PositionSide

logger = logging.getLogger("UAO_Grid.Providers")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _chunked(items: Iterable[Any], n: int = 20):
    """Divide una lista en chunks de tamaño n."""
    items = list(items)
    for i in range(0, len(items), n):
        yield items[i : i + n]


# ─────────────────────────────────────────────────────────────────────────────
# Interfaz abstracta (para futuros exchanges)
# ─────────────────────────────────────────────────────────────────────────────

class ExchangeProvider(ABC):
    """
    Interfaz de abstracción del exchange.

    Todos los módulos del sistema interactúan con esta interfaz,
    no con CCXT directamente.
    """

    @abstractmethod
    def get_balance(self) -> Dict[str, float]:
        """Retorna {'usdt_total': float, 'usdt_available': float}."""
        ...

    @abstractmethod
    def get_open_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """Retorna posiciones abiertas."""
        ...

    @abstractmethod
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Retorna órdenes abiertas."""
        ...

    @abstractmethod
    def reconciliar_ordenes(
        self, deseadas: List[Order], actuales: List[Order]
    ) -> None:
        """
        Sincroniza la malla deseada con las órdenes activas en el exchange.
        Cancela excedentes, crea faltantes, modifica las que cambiaron.
        """
        ...

    @abstractmethod
    def cancel_all_orders(self, symbol: str) -> None:
        """Cancela todas las órdenes abiertas de un símbolo."""
        ...

    @abstractmethod
    def close_position_market(self, symbol: str) -> None:
        """Cierra la posición abierta del símbolo a precio de mercado."""
        ...

    @abstractmethod
    def set_leverage(self, leverage: int, symbol: str) -> None:
        """Configura el apalancamiento para un símbolo."""
        ...

    @abstractmethod
    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "5m", limit: int = 200
    ) -> pd.DataFrame:
        """Descarga velas OHLCV. Retorna DataFrame con columnas [ts, open, high, low, close, volume]."""
        ...

    @abstractmethod
    def fetch_tickers(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        """Retorna tickers de uno o varios símbolos."""
        ...

    @abstractmethod
    def fetch_markets(self) -> Dict[str, Any]:
        """Retorna el mapa completo de mercados del exchange."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Implementación OKX (CCXT)
# ─────────────────────────────────────────────────────────────────────────────

class OKXProvider(ExchangeProvider):
    """
    Provider de OKX usando CCXT.

    Soporta modo DEMO (sandbox) y REAL.
    Usa Batch API para operaciones de órdenes (cancel/create/amend).
    Anti-rate-limit: sleep(0.5) entre chunks.
    """

    def __init__(
        self,
        config: AppConfig,
        exchange: Optional[ccxt.Exchange] = None,
    ) -> None:
        self.config = config
        self.is_demo = config.exchange.is_demo
        self._exchange = exchange or self._build_exchange()
        self._exchange.load_markets()
        mode_label = "DEMO" if self.is_demo else "REAL"
        logger.info("✅ OKXProvider iniciado en modo %s", mode_label)

    # ── Inicialización ────────────────────────────────────────────────────────

    def _build_exchange(self) -> ccxt.Exchange:
        cfg = {
            "options": {"defaultType": "swap"},
            "timeout": 20_000,
            "enableRateLimit": True,
            "apiKey": self.config.exchange.okx_api_key,
            "secret": self.config.exchange.okx_api_secret,
            "password": self.config.exchange.okx_passphrase,
        }
        exchange = ccxt.okx(cfg)
        if self.is_demo:
            exchange.set_sandbox_mode(True)
        return exchange

    @property
    def exchange(self) -> ccxt.Exchange:
        """Acceso directo al exchange subyacente (para el WS, scanner, etc.)."""
        return self._exchange

    # ── Balance ───────────────────────────────────────────────────────────────

    def get_balance(self) -> Dict[str, float]:
        try:
            bal = self._exchange.fetch_balance()
            return {
                "usdt_total":     float(bal.get("total", {}).get("USDT", 0.0) or 0.0),
                "usdt_available": float(bal.get("free",  {}).get("USDT", 0.0) or 0.0),
            }
        except Exception as exc:
            logger.error("get_balance error: %s", exc)
            return {"usdt_total": 0.0, "usdt_available": 0.0}

    # ── Posiciones ────────────────────────────────────────────────────────────

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Position]:
        try:
            raw = (
                [self._exchange.fetch_position(symbol)]
                if symbol
                else self._exchange.fetch_positions()
            )
            result: List[Position] = []
            for p in raw:
                if not p:
                    continue
                contracts = float(p.get("contracts") or 0.0)
                raw_pos   = float(p.get("info", {}).get("pos") or contracts)
                if abs(contracts) <= 0:
                    continue
                sym       = p.get("symbol") or symbol
                side_raw  = str(p.get("side") or "").lower()
                side      = PositionSide.SHORT if (side_raw == "short" or raw_pos < 0) else PositionSide.LONG
                entry     = float(p.get("entryPrice") or p.get("markPrice") or 0.0)
                result.append(Position(sym, side, abs(contracts), entry))
            return result
        except Exception as exc:
            logger.error("get_open_positions error: %s", exc)
            return []

    # ── Órdenes abiertas ──────────────────────────────────────────────────────

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        try:
            raw_orders = self._exchange.fetch_open_orders(symbol)
            result: List[Order] = []
            for o in raw_orders:
                cl_id = o.get("clientOrderId") or o.get("info", {}).get("clOrdId", "")
                grid_level = 0
                if cl_id.startswith("glvl"):
                    try:
                        level_str  = cl_id.split("x", 1)[0].replace("glvl", "")
                        grid_level = int(level_str.replace("m", "-"))
                    except (ValueError, IndexError):
                        grid_level = 0

                side_raw   = str(o.get("side", "")).upper()
                ro_raw     = str(o.get("reduceOnly") or o.get("info", {}).get("reduceOnly", "")).lower()
                result.append(Order(
                    order_id    = str(o.get("id") or cl_id),
                    symbol      = o.get("symbol") or symbol,
                    side        = OrderSide.BUY if side_raw == "BUY" else OrderSide.SELL,
                    price       = float(o.get("price") or 0.0),
                    qty         = float(o.get("amount") or 0.0),
                    status      = OrderStatus.OPEN,
                    reduce_only = ro_raw == "true",
                    grid_level  = grid_level,
                ))
            return result
        except Exception as exc:
            logger.error("get_open_orders error: %s", exc)
            return []

    # ── Reconciliación ────────────────────────────────────────────────────────

    def reconciliar_ordenes(
        self, deseadas: List[Order], actuales: List[Order]
    ) -> None:
        """
        Diff entre malla deseada y estado real del exchange.
        Usa grid_level como clave primaria de matching (si disponible),
        luego igualdad exacta de precio/lado como fallback.
        """
        a_cancelar: List[Order] = []
        a_crear: List[Order] = []
        a_modificar: List[tuple[Order, Order]] = []

        deseadas_pendientes = list(deseadas)
        actuales_pendientes = list(actuales)

        # Match por grid_level (prioridad)
        for act in actuales:
            if act.grid_level == 0:
                continue
            match = next(
                (d for d in deseadas_pendientes
                 if d.grid_level == act.grid_level
                 and d.side == act.side
                 and d.reduce_only == act.reduce_only),
                None,
            )
            if match:
                deseadas_pendientes.remove(match)
                actuales_pendientes.remove(act)
                if act != match:
                    a_modificar.append((act, match))

        # Match por precio/lado exacto (fallback)
        for act in list(actuales_pendientes):
            perfect = next((d for d in deseadas_pendientes if d == act), None)
            if perfect:
                deseadas_pendientes.remove(perfect)
                actuales_pendientes.remove(act)
            else:
                a_cancelar.append(act)

        a_crear.extend(deseadas_pendientes)

        if not a_cancelar and not a_crear and not a_modificar:
            return

        logger.debug(
            "Reconciliar: +%d crear, -%d cancelar, ~%d modificar",
            len(a_crear), len(a_cancelar), len(a_modificar),
        )

        try:
            # 1. Cancelar excedentes
            for chunk in _chunked(a_cancelar, 20):
                payload = []
                for o in chunk:
                    if not o.order_id or str(o.order_id).lower() == "none":
                        continue
                    item = {"instId": o.inst_id}
                    if str(o.order_id).startswith("glvl"):
                        item["clOrdId"] = str(o.order_id)
                    else:
                        item["ordId"] = str(o.order_id)
                    payload.append(item)
                if payload:
                    logger.info("Cancelando %d órdenes batch", len(payload))
                    self._exchange.private_post_trade_cancel_batch_orders(payload)
                    time.sleep(0.5)

            # 2. Modificar (amend)
            for chunk in _chunked(a_modificar, 20):
                payload = []
                for act, des in chunk:
                    try:
                        px_str = self._exchange.price_to_precision(des.inst_id, des.price)
                    except Exception:
                        px_str = str(des.price)
                    try:
                        sz_str = self._exchange.amount_to_precision(des.inst_id, des.qty)
                    except Exception:
                        sz_str = str(des.qty)
                    item = {"instId": act.inst_id, "newPx": px_str, "newSz": sz_str}
                    if str(act.order_id).startswith("glvl"):
                        item["clOrdId"] = str(act.order_id)
                    else:
                        item["ordId"] = str(act.order_id)
                    payload.append(item)
                if payload:
                    logger.info("Modificando %d órdenes batch", len(payload))
                    self._exchange.private_post_trade_amend_batch_orders(payload)
                    time.sleep(0.5)

            # 3. Crear nuevas
            now = int(time.time())
            for chunk in _chunked(a_crear, 20):
                payload = []
                for i, o in enumerate(chunk):
                    safe_level = str(o.grid_level).replace("-", "m")
                    try:
                        px_str = self._exchange.price_to_precision(o.inst_id, o.price)
                    except Exception:
                        px_str = str(o.price)
                    try:
                        sz_str = self._exchange.amount_to_precision(o.inst_id, o.qty)
                    except Exception:
                        sz_str = str(o.qty)
                    item = {
                        "instId":  o.inst_id,
                        "tdMode":  "cross",
                        "side":    "buy" if o.side == OrderSide.BUY else "sell",
                        "ordType": "limit",
                        "px":      px_str,
                        "sz":      sz_str,
                        "clOrdId": f"glvl{safe_level}x{now}x{i}",
                    }
                    if o.reduce_only:
                        item["reduceOnly"] = "true"
                    payload.append(item)
                if payload:
                    logger.info("Creando %d órdenes batch", len(payload))
                    self._exchange.private_post_trade_batch_orders(payload)
                    time.sleep(0.5)

        except Exception as exc:
            if "51155" in str(exc):
                raise  # Re-lanzar: símbolo restringido por OKX (capturado por orquestador)
            logger.error("reconciliar_ordenes error: %s", exc)

    # ── Cancel / Close ────────────────────────────────────────────────────────

    def cancel_all_orders(self, symbol: str) -> None:
        try:
            abiertas = self.get_open_orders(symbol)
            for chunk in _chunked(abiertas, 20):
                payload = []
                for o in chunk:
                    if not o.order_id or str(o.order_id).lower() == "none":
                        continue
                    item = {"instId": o.inst_id}
                    if str(o.order_id).startswith("glvl"):
                        item["clOrdId"] = str(o.order_id)
                    else:
                        item["ordId"] = str(o.order_id)
                    payload.append(item)
                if payload:
                    self._exchange.private_post_trade_cancel_batch_orders(payload)
                    time.sleep(0.5)
            if abiertas:
                logger.info("Canceladas %d órdenes de %s", len(abiertas), symbol)
        except Exception as exc:
            logger.error("cancel_all_orders error: %s", exc)

    def close_position_market(self, symbol: str) -> None:
        try:
            positions = self.get_open_positions(symbol)
            if not positions:
                return
            p = positions[0]
            close_side = "sell" if p.side == PositionSide.LONG else "buy"
            logger.critical(
                "Cerrando posición a mercado: %s qty=%.4f side=%s",
                symbol, p.qty, close_side,
            )
            try:
                market  = self._exchange.market(symbol)
                payload = {"instId": market["id"], "mgnMode": "cross", "posSide": "net"}
                try:
                    self._exchange.private_post_trade_close_position(payload)
                    logger.info("Posición cerrada (net): %s", symbol)
                    return
                except Exception as e_net:
                    if "51006" in str(e_net) or "posSide" in str(e_net):
                        payload["posSide"] = "long" if p.side == PositionSide.LONG else "short"
                        self._exchange.private_post_trade_close_position(payload)
                        logger.info("Posición cerrada (long/short): %s", symbol)
                        return
                    raise e_net
            except Exception as e:
                logger.error("Fallo close-position nativo, usando fallback: %s", e)
            self._exchange.create_order(
                symbol=symbol, type="market", side=close_side, amount=p.qty,
                params={"reduceOnly": True, "tdMode": "cross"},
            )
        except Exception as exc:
            logger.error("close_position_market error: %s", exc)

    # ── Leverage ──────────────────────────────────────────────────────────────

    def set_leverage(self, leverage: int, symbol: str) -> None:
        try:
            logger.info("Ajustando apalancamiento a %dx para %s", leverage, symbol)
            self._exchange.set_leverage(leverage, symbol, params={"mgnMode": "cross"})
        except Exception as exc:
            logger.warning("No se pudo ajustar leverage para %s: %s", symbol, exc)

    # ── Datos de mercado (públicos) ───────────────────────────────────────────

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "5m",
        limit: int = 200,
    ) -> pd.DataFrame:
        """
        Descarga velas OHLCV.

        Returns:
            DataFrame con columnas [ts, open, high, low, close, volume].
            DataFrame vacío si falla la descarga o hay pocos datos.
        """
        try:
            velas = self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not velas or len(velas) < max(15, limit // 4):
                logger.debug("fetch_ohlcv: pocos datos para %s (%d velas)", symbol, len(velas) if velas else 0)
                return pd.DataFrame()
            df = pd.DataFrame(velas, columns=["ts", "open", "high", "low", "close", "volume"])
            df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
            return df
        except Exception as exc:
            logger.error("fetch_ohlcv error %s: %s", symbol, exc)
            return pd.DataFrame()

    def fetch_tickers(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Descarga tickers. Si symbols=None retorna todos.

        Returns:
            Dict {symbol: ticker_data}
        """
        try:
            if symbols:
                return self._exchange.fetch_tickers(symbols)
            return self._exchange.fetch_tickers()
        except Exception as exc:
            logger.error("fetch_tickers error: %s", exc)
            return {}

    def fetch_markets(self) -> Dict[str, Any]:
        """Retorna el mapa completo de mercados."""
        try:
            if not self._exchange.markets:
                self._exchange.load_markets()
            return self._exchange.markets
        except Exception as exc:
            logger.error("fetch_markets error: %s", exc)
            return {}

    def get_recent_fills(self, symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Retorna los últimos N fills del símbolo."""
        try:
            return self._exchange.fetch_my_trades(symbol, limit=limit)
        except Exception as exc:
            logger.error("get_recent_fills error: %s", exc)
            return []


# ─────────────────────────────────────────────────────────────────────────────
# Filtros de mercado
# ─────────────────────────────────────────────────────────────────────────────

def obtener_futuros_usdt(markets: Dict[str, Any]) -> List[str]:
    """
    Filtra futuros perpetuos USDT activos del mapa de mercados.

    Returns:
        Lista de símbolos CCXT (ej. ['BTC/USDT:USDT', 'ETH/USDT:USDT'])
    """
    return [
        sym for sym, m in markets.items()
        if m.get("active")
        and m.get("settle") == "USDT"
        and m.get("type") == "swap"
    ]


def filtrar_por_volumen(
    tickers: Dict[str, Any],
    simbolos: List[str],
    min_volume_usdt: float = 1_000_000.0,
) -> List[str]:
    """
    Filtra y ordena símbolos por volumen de 24h descendente.

    Args:
        tickers: Resultado de fetch_tickers()
        simbolos: Lista inicial de símbolos a filtrar
        min_volume_usdt: Volumen mínimo en USDT

    Returns:
        Lista de símbolos ordenados por volumen descendente.
    """
    filtrados = []
    for sym in simbolos:
        t = tickers.get(sym, {})
        vol = float(t.get("quoteVolume") or 0.0)
        if vol >= min_volume_usdt:
            filtrados.append((sym, vol))

    filtrados.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in filtrados]
