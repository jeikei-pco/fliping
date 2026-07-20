"""
providers.py - Provider unificado para OKX DEMO/REAL.

El motor trabaja contra un unico ExchangeProvider. La persistencia local queda fuera
porque el exchange es la fuente de verdad para posiciones y ordenes abiertas.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional

import ccxt

logger = logging.getLogger("UAO_Sclaping.Providers")


class Order:
    """Orden de dominio independiente del exchange."""

    def __init__(self, order_id: str, symbol: str, side: str, price: float, qty: float,
                 status: str = "OPEN", reduce_only: bool = False, grid_level: int = 0):
        self.order_id = order_id
        self.symbol = symbol
        self.side = side.upper()
        self.price = float(price)
        self.qty = float(qty)
        self.status = status
        self.reduce_only = bool(reduce_only)
        self.grid_level = int(grid_level or 0)

    @property
    def inst_id(self) -> str:
        return self.symbol.replace("/", "-").replace(":USDT", "-SWAP")

    def __eq__(self, other):
        if not isinstance(other, Order):
            return False
        # Tolerancia de +/- 1 tick aproximado (0.05% del precio)
        price_match = abs(self.price - other.price) <= (self.price * 0.0005)
        
        # ✅ FIX: Eliminamos la validación de qty_match para soportar partial fills
        # Las órdenes de grid se identifican por precio y lado.
        return (
            self.symbol == other.symbol
            and self.side == other.side
            and price_match
            and self.reduce_only == other.reduce_only
        )


class Position:
    """Posicion de dominio."""

    def __init__(self, symbol: str, side: str, qty: float, entry_price: float):
        self.symbol = symbol
        self.side = side.upper()
        self.qty = abs(float(qty))
        self.entry_price = float(entry_price or 0.0)


class ExecutionProvider(ABC):
    def __init__(self, exchange: ccxt.Exchange, mode: str):
        self.exchange = exchange
        self.mode = mode.lower()

    @abstractmethod
    def get_balance(self) -> Dict[str, float]: pass

    @abstractmethod
    def get_open_positions(self, symbol: Optional[str] = None) -> List[Position]: pass

    @abstractmethod
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]: pass

    @abstractmethod
    def reconciliar_ordenes(self, deseadas: List[Order], actuales: List[Order]): pass

    @abstractmethod
    def cancel_all_orders(self, symbol: str) -> None: pass

    @abstractmethod
    def close_position_market(self, symbol: str) -> None: pass

    @abstractmethod
    def set_leverage(self, leverage: float, symbol: str) -> None: pass


def _chunked(items: Iterable[Any], n: int = 20):
    items = list(items)
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _load_okx_credentials(prefix: str) -> Dict[str, str]:
    """Carga credenciales estrictamente desde el .env evitando sobrescrituras."""
    prefix = prefix.upper()
    
    # Prioridad absoluta: Variables con sufijo _DEMO o _REAL
    creds = {
        "api_key": os.getenv(f"OKX_API_KEY_{prefix}"),
        "api_secret": os.getenv(f"OKX_API_SECRET_{prefix}"),
        "passphrase": os.getenv(f"OKX_PASSPHRASE_{prefix}"),
    }
    
    # Validar que no estén vacías
    if creds["api_key"] and creds["api_secret"] and creds["passphrase"]:
        logger.info(f"✅ Credenciales {prefix} cargadas correctamente.")
        return creds

    # Si llegamos aquí y es DEMO, el bot no debería intentar llamar a la API interna 
    # de producción, así que lanzamos error para que no conecte con credenciales erróneas.
    raise ValueError(f"❌ Error: No se encontraron las credenciales {prefix} en el .env")


class ExchangeProvider(ExecutionProvider):
    """Provider unico para OKX DEMO/REAL usando CCXT y Batch API."""

    def __init__(self, mode: str = "DEMO", exchange: Optional[ccxt.Exchange] = None):
        self.raw_mode = (mode or "DEMO").upper()
        self.is_demo = self.raw_mode in {"DEMO", "SANDBOX", "PAPER", "SIMULATED"}
        self.credentials = _load_okx_credentials("DEMO" if self.is_demo else "REAL")
        exchange = exchange or self._init_exchange()
        super().__init__(exchange, "demo" if self.is_demo else "real")
        self.exchange.load_markets()
        logger.info("ExchangeProvider OKX iniciado en modo %s", self.raw_mode)

    @property
    def has_private_credentials(self) -> bool:
        return bool(self.credentials.get("api_key") and self.credentials.get("api_secret") and self.credentials.get("passphrase"))

    def _init_exchange(self) -> ccxt.Exchange:
        config: Dict[str, Any] = {
            "options": {"defaultType": "swap"},
            "timeout": 20_000,
            "enableRateLimit": True,
        }
        if self.credentials.get("api_key"):
            config["apiKey"] = self.credentials["api_key"]
            config["secret"] = self.credentials["api_secret"]
            config["password"] = self.credentials["passphrase"]
        exchange = ccxt.okx(config)
        if self.is_demo:
            exchange.set_sandbox_mode(True)
        return exchange

    def get_balance(self) -> Dict[str, float]:
        try:
            bal = self.exchange.fetch_balance()
            total = float(bal.get("total", {}).get("USDT", 0.0) or 0.0)
            free = float(bal.get("free", {}).get("USDT", 0.0) or 0.0)
            return {"usdt_total": total, "usdt_available": free}
        except Exception as exc:
            logger.error("Error API balance: %s", exc)
            return {"usdt_total": 0.0, "usdt_available": 0.0}

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Position]:
        try:
            raw = [self.exchange.fetch_position(symbol)] if symbol else self.exchange.fetch_positions()
            result: List[Position] = []
            for p in raw:
                if not p:
                    continue
                contracts = float(p.get("contracts") or 0.0)
                raw_pos = float(p.get("info", {}).get("pos") or contracts)
                if abs(contracts) <= 0:
                    continue
                sym = p.get("symbol") or symbol
                side_raw = str(p.get("side") or "").lower()
                side = "SHORT" if side_raw == "short" or raw_pos < 0 else "LONG"
                entry = float(p.get("entryPrice") or p.get("markPrice") or 0.0)
                result.append(Position(sym, side, abs(contracts), entry))
            return result
        except Exception as exc:
            logger.error("Error API fetch_positions: %s", exc)
            return []

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        try:
            raw_orders = self.exchange.fetch_open_orders(symbol)
            result = []
            for o in raw_orders:
                cl_id = o.get("clientOrderId") or o.get("info", {}).get("clOrdId", "")
                grid_level = 0
                if cl_id.startswith("glvl"):
                    try:
                        level_str = cl_id.split("x", 1)[0].replace("glvl", "")
                        grid_level = int(level_str.replace("m", "-"))
                    except (ValueError, IndexError):
                        grid_level = 0
                result.append(Order(
                    order_id=str(o.get("id") or cl_id),
                    symbol=o.get("symbol") or symbol,
                    side=str(o.get("side", "")).upper(),
                    price=float(o.get("price") or 0.0),
                    qty=float(o.get("amount") or 0.0),
                    status=str(o.get("status") or "OPEN").upper(),
                    reduce_only=str(o.get("reduceOnly") or o.get("info", {}).get("reduceOnly", "")).lower() == "true",
                    grid_level=grid_level,
                ))
            return result
        except Exception as exc:
            logger.error("Error API fetch_open_orders: %s", exc)
            return []

    def reconciliar_ordenes(self, deseadas: List[Order], actuales: List[Order]):
        a_cancelar = []
        a_crear = []
        a_modificar = []

        deseadas_pendientes = list(deseadas)
        actuales_pendientes = list(actuales)

        for act in actuales:
            match = next((d for d in deseadas_pendientes if d.grid_level == act.grid_level and d.side == act.side and act.grid_level != 0 and d.reduce_only == act.reduce_only), None)
            if match:
                deseadas_pendientes.remove(match)
                actuales_pendientes.remove(act)
                if act != match:
                    a_modificar.append((act, match))

        for act in actuales_pendientes:
            perfect_match = next((d for d in deseadas_pendientes if d == act), None)
            if perfect_match:
                deseadas_pendientes.remove(perfect_match)
            else:
                a_cancelar.append(act)

        a_crear.extend(deseadas_pendientes)

        if not a_cancelar and not a_crear and not a_modificar:
            return

        try:
            for chunk in _chunked(a_cancelar, 20):
                payload = []
                for o in chunk:
                    if not o.order_id or str(o.order_id).lower() == "none":
                        logger.warning(f"⚠️ Intentando cancelar orden sin ID (reconciliacion): {o}")
                        continue
                    item = {"instId": o.inst_id}
                    if str(o.order_id).startswith("glvl"):
                        item["clOrdId"] = str(o.order_id)
                    else:
                        item["ordId"] = str(o.order_id)
                    payload.append(item)
                    
                if payload:
                    logger.info("Cancelando %d ordenes batch", len(payload))
                    self.exchange.private_post_trade_cancel_batch_orders(payload)
                    # ✅ FIX: Anti-Baneo Rate Limit
                    time.sleep(0.5)
                    
            for chunk in _chunked(a_modificar, 20):
                payload = []
                for act, des in chunk:
                    try:
                        new_px_str = self.exchange.price_to_precision(des.inst_id, des.price)
                    except Exception:
                        new_px_str = str(des.price)
                    try:
                        new_sz_str = self.exchange.amount_to_precision(des.inst_id, des.qty)
                    except Exception:
                        new_sz_str = str(des.qty)
                        
                    item = {
                        "instId": act.inst_id,
                        "newPx": new_px_str,
                        "newSz": new_sz_str,
                    }
                    if str(act.order_id).startswith("glvl"):
                        item["clOrdId"] = str(act.order_id)
                    else:
                        item["ordId"] = str(act.order_id)
                    payload.append(item)
                    
                if payload:
                    logger.info("Modificando %d ordenes batch", len(payload))
                    self.exchange.private_post_trade_amend_batch_orders(payload)
                    time.sleep(0.5)

            for chunk in _chunked(a_crear, 20):
                payload = []
                now = int(time.time())
                for i, o in enumerate(chunk):
                    safe_grid_level = str(o.grid_level).replace("-", "m")
                    try:
                        px_str = self.exchange.price_to_precision(o.inst_id, o.price)
                    except Exception:
                        px_str = str(o.price)
                        
                    try:
                        sz_str = self.exchange.amount_to_precision(o.inst_id, o.qty)
                    except Exception:
                        sz_str = str(o.qty)
                        
                    item = {
                        "instId": o.inst_id,
                        "tdMode": "cross",
                        "side": "buy" if o.side == "BUY" else "sell",
                        "ordType": "limit",
                        "px": px_str,
                        "sz": sz_str,
                        "clOrdId": f"glvl{safe_grid_level}x{now}x{i}",
                    }
                    if o.reduce_only:
                        item["reduceOnly"] = "true"
                    payload.append(item)
                if payload:
                    logger.info("Creando %d ordenes batch", len(payload))
                    self.exchange.private_post_trade_batch_orders(payload)
                    # ✅ FIX: Anti-Baneo Rate Limit
                    time.sleep(0.5)
        except Exception as exc:
            if "51155" in str(exc):
                raise exc
            logger.error("Error API reconciliar_ordenes: %s", exc)

    def cancel_all_orders(self, symbol: str) -> None:
        try:
            abiertas = self.get_open_orders(symbol)
            for chunk in _chunked(abiertas, 20):
                payload = []
                for o in chunk:
                    if not o.order_id or str(o.order_id).lower() == "none":
                        logger.warning(f"⚠️ Intentando cancelar orden sin ID: {o}")
                        continue
                        
                    item = {"instId": o.inst_id}
                    if str(o.order_id).startswith("glvl"):
                        item["clOrdId"] = str(o.order_id)
                    else:
                        item["ordId"] = str(o.order_id)
                    payload.append(item)

                if payload:
                    self.exchange.private_post_trade_cancel_batch_orders(payload)
                    # ✅ FIX: Anti-Baneo Rate Limit
                    time.sleep(0.5)
            if abiertas:
                logger.info("Canceladas %d ordenes de %s", len(abiertas), symbol)
        except Exception as exc:
            logger.error("Error cancel_all_orders: %s", exc)

    def close_position_market(self, symbol: str) -> None:
        try:
            pos = self.get_open_positions(symbol)
            if not pos:
                return
            p = pos[0]
            close_side = "sell" if p.side == "LONG" else "buy"
            logger.critical("Cerrando posicion a mercado: %s qty=%s side=%s", symbol, p.qty, close_side)
            
            try:
                market = self.exchange.market(symbol)
                payload = {
                    "instId": market["id"],
                    "mgnMode": "cross",
                    "posSide": "net"
                }
                try:
                    self.exchange.private_post_trade_close_position(payload)
                    logger.info(f"Posición cerrada vía OKX API (net): {payload}")
                    return
                except Exception as e_net:
                    if "51006" in str(e_net) or "posSide" in str(e_net):
                        payload["posSide"] = "long" if p.side == "LONG" else "short"
                        self.exchange.private_post_trade_close_position(payload)
                        logger.info(f"Posición cerrada vía OKX API (long/short): {payload}")
                        return
                    else:
                        raise e_net
            except Exception as e:
                logger.error(f"Fallo close-position nativo, usando fallback: {e}")
                
            # ✅ FIX: Booleano nativo True para CCXT
            self.exchange.create_order(symbol=symbol, type="market", side=close_side, amount=p.qty,
                                       params={"reduceOnly": True, "tdMode": "cross"})
        except Exception as exc:
            logger.error("Error close_position_market: %s", exc)

    def get_recent_fills(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            return self.exchange.fetch_my_trades(symbol, limit=20)
        except Exception as exc:
            logger.error("Error API fetch_my_trades: %s", exc)
            return []

    def set_leverage(self, leverage: float, symbol: str) -> None:
        try:
            logger.info("Ajustando apalancamiento a %sx para %s", int(leverage), symbol)
            self.exchange.set_leverage(int(leverage), symbol, params={"mgnMode": "cross"})
        except Exception as exc:
            logger.warning("No se pudo ajustar apalancamiento para %s (puede que ya este configurado): %s", symbol, exc)


class OKXRealAdapter(ExchangeProvider):
    def __init__(self, exchange: Optional[ccxt.Exchange] = None):
        super().__init__(mode="REAL", exchange=exchange)

class OKXDemoAdapter(ExchangeProvider):
    def __init__(self, exchange: Optional[ccxt.Exchange] = None):
        super().__init__(mode="DEMO", exchange=exchange)
