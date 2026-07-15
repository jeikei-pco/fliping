import asyncio
import logging
import pandas as pd
from datetime import datetime
import time
from collections import deque

from .math_core import calculate_keltner_channels, calculate_cv

logger = logging.getLogger("GridWorker.OKX_WS")

def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

def _normalize_amount(value):
    try:
        f_val = float(value)
        if f_val.is_integer():
            return int(f_val)
        return f_val
    except:
        return value

def _extract_symbol(exchange, record):
    symbol = record.get('symbol')
    if symbol:
        return symbol

    info = record.get('info', {}) or {}
    inst_id = info.get('instId')
    if inst_id:
        try:
            return exchange.safe_symbol(inst_id)
        except Exception:
            return inst_id

    return None

def _is_reduce_only_order(order):
    info = order.get('info', {}) or {}

    reduce_only = order.get('reduceOnly', False)
    if isinstance(reduce_only, str):
        reduce_only = reduce_only.lower() == 'true'

    if reduce_only:
        return True

    if str(info.get('reduceOnly', '')).lower() == 'true':
        return True

    pos_side = str(info.get('posSide', '')).lower()
    if pos_side in ['close_long', 'close_short']:
        return True

    return False

def _is_active_position(position):
    info = position.get('info', {}) or {}
    size = position.get('contracts')

    if size is None:
        size = info.get('pos')

    if size is None:
        size = info.get('availPos')

    return abs(_safe_float(size)) > 0


# ---------------------------------------------------------
# DETECCIÓN DE GRIDS
# ---------------------------------------------------------
async def detect_active_exchange_grid(controller):
    exchange = controller.get_instance()

    try:
        await exchange.load_markets()

        open_orders = []
        try:
            open_orders = await exchange.fetch_open_orders(None, None, None, {'instType': 'SWAP'})
        except TypeError:
            open_orders = await exchange.fetch_open_orders()
        except Exception as exc:
            logger.warning(f"No se pudieron consultar órdenes abiertas: {exc}")

        entry_orders = []
        for order in open_orders:
            if _is_reduce_only_order(order):
                continue
            symbol = _extract_symbol(exchange, order)
            if not symbol:
                continue
            entry_orders.append({**order, 'symbol': symbol})

        positions = []
        try:
            positions = await exchange.fetch_positions(None, {'instType': 'SWAP'})
        except TypeError:
            positions = await exchange.fetch_positions()
        except Exception as exc:
            logger.warning(f"No se pudieron consultar posiciones abiertas: {exc}")

        active_positions = []
        for position in positions:
            if not _is_active_position(position):
                continue
            symbol = _extract_symbol(exchange, position)
            if not symbol:
                continue
            active_positions.append({**position, 'symbol': symbol})

        symbol_scores = {}
        for position in active_positions:
            symbol_scores[position['symbol']] = symbol_scores.get(position['symbol'], 0) + 10
        for order in entry_orders:
            symbol_scores[order['symbol']] = symbol_scores.get(order['symbol'], 0) + 1

        symbol = max(symbol_scores, key=symbol_scores.get) if symbol_scores else None

        if active_positions and entry_orders:
            source = "positions_and_orders"
        elif active_positions:
            source = "positions"
        elif entry_orders:
            source = "orders"
        else:
            source = "none"

        return {
            'has_active_grid': bool(symbol),
            'symbol': symbol,
            'position_count': len(active_positions),
            'entry_order_count': len(entry_orders),
            'source': source,
        }
    except Exception as e:
        logger.error(f"Error detectando grid: {e}")
        return {'has_active_grid': False}


# ---------------------------------------------------------
# CLIENTE WEBSOCKETS CORE
# ---------------------------------------------------------
class OkxWsClient:
    def __init__(
        self,
        controller,
        symbol="BTC/USDT",
        timeframe="15m",
        base_capital=50.0,
        ai_recommendation=None,
        resume_existing_grid=False,
    ):
        self.exchange = controller.get_instance()
        self._exchange_id = self.exchange.id
        self._sandbox = self.exchange.options.get('sandboxMode', False)

        self.symbol = symbol
        logger.info(f"Symbol -> {symbol}")
        self.timeframe = timeframe
        self.running = False
        self.base_capital = base_capital
        self.ai_recommendation = ai_recommendation or {}
        self.resume_existing_grid = resume_existing_grid

        self.raw_candles = deque(maxlen=100) 
        self.df = pd.DataFrame() 
            
        self.metrics = {
            "status": "Initialized",
            "keltner": None,
            "cv": None,
            "last_price": None,
            "symbol": symbol,
            "mode": "resume" if resume_existing_grid else "create",
            "exchange_mode": "DEMO/SANDBOX" if self._sandbox else "REAL"
        }
        
        self.grid_spacing_usd = 0.0
        self.processed_orders = set() 
        
        logger.info(f"🚀 Motor de Trading Inicializado en modo: {self.metrics['exchange_mode']}")
        
    async def fetch_historical_candles(self, limit=50):
        try:
            logger.info(f"Cargando {limit} velas históricas para {self.symbol} ({self.timeframe})...")
            ohlcvs = await self.exchange.fetch_ohlcv(self.symbol, self.timeframe, limit=limit)
            
            for ohlcv in ohlcvs:
                self.raw_candles.append({
                    'timestamp': pd.to_datetime(ohlcv[0], unit='ms'),
                    'open': ohlcv[1],
                    'high': ohlcv[2],
                    'low': ohlcv[3],
                    'close': ohlcv[4],
                    'volume': ohlcv[5]
                })
            self.df = pd.DataFrame(list(self.raw_candles))
            logger.info("Velas históricas cargadas exitosamente.")
            self.update_metrics()
        except Exception as e:
            logger.error(f"Error cargando velas históricas: {e}")

    def update_metrics(self):
        if len(self.df) < 20:
            return
            
        upper, middle, lower = calculate_keltner_channels(self.df)
        cv = calculate_cv(self.df)
        
        self.metrics["keltner"] = {
            "upper": float(upper) if upper is not None else None,
            "middle": float(middle) if middle is not None else None,
            "lower": float(lower) if lower is not None else None
        }
        self.metrics["cv"] = float(cv) if cv is not None else None
        self.metrics["last_price"] = float(self.df.iloc[-1]['close'])
        self.metrics["updated_at"] = datetime.now().isoformat()

    async def setup_grid_orders(self):
        try:
            logger.info(f"Configurando órdenes para {self.symbol}...")

            try:
                balance = await self.exchange.fetch_balance()
                usdt_balance = float(balance.get('USDT', {}).get('free', 0.0))
                logger.info(f"💰 Balance libre en cuenta: {usdt_balance:.2f} USDT")
                
                if self.base_capital > usdt_balance:
                    logger.error(f"⚠️ BALANCE INSUFICIENTE")
                    raise Exception(f"Balance insuficiente.")
            except Exception as e:
                if "Balance insuficiente" in str(e): raise
                logger.warning(f"No se pudo verificar el balance: {e}")

            leverage = float(self.ai_recommendation.get("leverage", 10))
            try:
                await self.exchange.set_leverage(leverage, self.symbol)
            except Exception:
                logger.exception("No fue posible configurar el leverage")

            grid_lines = int(self.ai_recommendation.get("grid_lines", 10))
            direction = self.ai_recommendation.get("direction", "neutral").lower()

            if direction == "long":
                buy_lines = grid_lines
                sell_lines = 0
            elif direction == "short":
                buy_lines = 0
                sell_lines = grid_lines
            else:
                if grid_lines % 2 != 0: grid_lines += 1
                buy_lines = grid_lines // 2
                sell_lines = grid_lines // 2

            grid_spacing_factor = float(self.ai_recommendation.get("grid_spacing_factor", 0.5)) / 100
            inversion_base = self.base_capital / grid_lines
            valor_apalancado = inversion_base * leverage

            try:
                account_config = await self.exchange.private_get_account_config()
                self.pos_mode = account_config['data'][0]['posMode']
                logger.info(f"Modo de posición de la cuenta: {self.pos_mode}")
            except Exception as e:
                logger.warning(f"No se pudo obtener posMode, asumiendo long_short_mode: {e}")
                self.pos_mode = 'long_short_mode'

            await self.exchange.load_markets()
            current_price = self.metrics["last_price"]
            self.grid_spacing_usd = current_price * grid_spacing_factor

            market = self.exchange.market(self.symbol)
            contract_size = market.get("contractSize", 1)
            raw_amount = valor_apalancado / current_price / contract_size
            amount = _normalize_amount(self.exchange.amount_to_precision(self.symbol, raw_amount))

            base_params = {"tdMode": "cross"}
                
            orders = []

            # Órdenes de COMPRA (Entry Longs)
            for i in range(1, buy_lines + 1):
                price = float(self.exchange.price_to_precision(self.symbol, current_price - i * self.grid_spacing_usd))
                buy_params = base_params.copy()
                buy_params["posSide"] = "net" if getattr(self, 'pos_mode', 'long_short_mode') == 'net_mode' else "long"
                orders.append({
                    "symbol": self.symbol, "type": "limit", "side": "buy",
                    "amount": amount, "price": price, "params": buy_params
                })

            # Órdenes de VENTA (Entry Shorts)
            for i in range(1, sell_lines + 1):
                price = float(self.exchange.price_to_precision(self.symbol, current_price + i * self.grid_spacing_usd))
                sell_params = base_params.copy()
                sell_params["posSide"] = "net" if getattr(self, 'pos_mode', 'long_short_mode') == 'net_mode' else "short"
                orders.append({
                    "symbol": self.symbol, "type": "limit", "side": "sell",
                    "amount": amount, "price": price, "params": sell_params
                })

            logger.info(f"Enviando {len(orders)} órdenes en bloque...")
            if orders:
                logger.info(f"Ejemplo de orden a enviar (campos y datos): {orders[0]}")

            # FIX: OKX DEMO/SANDBOX suele fallar con create_orders_ws o requiere estructura específica
            # Usamos create_orders (HTTP) o iteración controlada para mayor estabilidad
            try:
                if self.exchange.has.get("createOrders"):
                    result = await self.exchange.create_orders(orders)
                else:
                    # Fallback individual si el bloque falla
                    result = await asyncio.gather(*[self.exchange.create_order(**o) for o in orders], return_exceptions=True)
                logger.info(f"Resultado Creación Bloque: {result}")
            except Exception as block_err:
                logger.error(f"Fallo creación en bloque, reintentando individualmente: {block_err}")
                for o in orders:
                    try:
                        await self.exchange.create_order(symbol=o["symbol"], type=o["type"], side=o["side"], amount=o["amount"], price=o["price"], params=o["params"])
                    except Exception as e:
                        logger.error(f"Error en orden individual: {e}")

            self.ultima_ejecucion_ts = time.time()
        except Exception as e:
            logger.exception("ERROR EN setup_grid_orders")
            raise

    def evaluar_inactividad_velas(self, minutos: int = 20) -> bool:
        if not self.running: return False
        segundos_inactivos = time.time() - getattr(self, 'ultima_ejecucion_ts', time.time())
        if segundos_inactivos > (minutos * 60):
            logger.info(f"⏳ [INACTIVIDAD] Re-centrando Grid.")
            return True
        return False

    async def _watch_orders_loop(self):
        logger.info("Iniciando escucha de Fills (Bidireccional)...")
        while self.running:
            try:
                orders = await self.exchange.watch_orders(self.symbol)
                for order in orders:
                    order_id = order.get('id')
                    status = order.get('status')
                    
                    if status == 'closed' and order_id not in self.processed_orders:
                        self.processed_orders.add(order_id)
                        
                        filled_price = float(order.get('average') or order.get('price'))
                        side = order.get('side')
                        amount = _normalize_amount(order.get('filled') or order.get('amount'))
                        
                        # Lógica PnL Positivo
                        maker_fee_pct = 0.0002 
                        min_net_profit_pct = 0.0015 
                        required_movement_pct = (maker_fee_pct * 2) + min_net_profit_pct
                        
                        min_required_spacing_usd = filled_price * required_movement_pct
                        actual_spacing_usd = max(getattr(self, 'grid_spacing_usd', 0.0), min_required_spacing_usd)
                        
                        # Extraer posSide original (si no existe, deducir asumiendo Entry)
                        info = order.get('info', {}) or {}
                        pos_side = info.get('posSide')
                        if not pos_side:
                            pos_side = 'long' if side == 'buy' else 'short'
                        
                        # Si compramos, la inversa es Vender
                        # Si vendimos, la inversa es Comprar
                        if side == 'buy':
                            new_price = filled_price + actual_spacing_usd
                            new_side = 'sell'
                        else:
                            new_price = filled_price - actual_spacing_usd
                            new_side = 'buy'
                            
                        new_price = float(self.exchange.price_to_precision(self.symbol, new_price))
                        
                        logger.info(f"✅ [FILL {side.upper()} {pos_side.upper()}] Ejecutando Inversa {new_side.upper()} a {new_price} (PnL Garantizado)")
                        
                        if getattr(self, 'pos_mode', 'long_short_mode') == 'net_mode':
                            params = {'postOnly': True, 'tdMode': 'cross', 'posSide': 'net'}
                        else:
                            params = {'postOnly': True, 'tdMode': 'cross', 'posSide': pos_side}
                            
                        try:
                            # Ejecución de la orden inversa inmediata
                            await self.exchange.create_order(
                                symbol=self.symbol, type='limit', side=new_side, amount=amount, price=new_price, params=params
                            )
                        except Exception as e:
                            logger.error(f"Error en orden inversa: {e}")
                            
            except Exception as e:
                logger.error(f"Error en watch_orders: {e}")
                await asyncio.sleep(5)

    async def _watch_ohlcv_loop(self):
        while self.running:
            try:
                candles = await self.exchange.watch_ohlcv(self.symbol, self.timeframe)
                is_new_candle = False
                for ohlcv in candles:
                    ts = pd.to_datetime(ohlcv[0], unit='ms')
                    new_row = {'timestamp': ts, 'open': ohlcv[1], 'high': ohlcv[2], 'low': ohlcv[3], 'close': ohlcv[4], 'volume': ohlcv[5]}
                    if len(self.raw_candles) > 0 and self.raw_candles[-1]['timestamp'] == ts:
                        self.raw_candles[-1] = new_row 
                    else:
                        self.raw_candles.append(new_row)
                        is_new_candle = True
                
                self.metrics["last_price"] = float(candles[-1][4])
                if is_new_candle:
                    self.df = pd.DataFrame(list(self.raw_candles))
                    self.update_metrics()
                    if self.evaluar_inactividad_velas(minutos=20):
                        await self.setup_grid_orders()
            except Exception as e:
                logger.error(f"Error en watch_ohlcv: {e}")
                await asyncio.sleep(5)

    async def _test_ws_order(self):
        logger.info("⚠️ [TEST WS] Iniciando prueba de conexión WS privado y 1 orden LIMIT...")
        try:
            # 1. Configurar apalancamiento
            await self.exchange.set_leverage(15, self.symbol)
            
            # 2. Calcular precio y cantidad
            ticker = await self.exchange.fetch_ticker(self.symbol)
            current_price = ticker['last']
            target_price = float(self.exchange.price_to_precision(self.symbol, current_price * 0.5))
            
            market = self.exchange.market(self.symbol)
            contract_size = market.get("contractSize", 1)
            raw_amount = 75.0 / target_price / contract_size # 5 USDT * 15x
            amount = _normalize_amount(self.exchange.amount_to_precision(self.symbol, raw_amount))
            if amount <= 0: amount = 1
            
            # 3. Configurar estructura exacta solicitada por el usuario
            account_config = await self.exchange.private_get_account_config()
            pos_mode = account_config['data'][0]['posMode']
            pos_side = 'net' if pos_mode == 'net_mode' else 'long'
            
            # Construimos la estructura interna (CCXT la mapeará 1:1 al JSON de OKX)
            params = {
                'tdMode': 'cross',
                'posSide': pos_side
            }
            
            # Log exacto de la estructura nativa que recibirá OKX en args:
            okx_raw_payload = {
                "instId": self.symbol.replace("/", "-").replace(":USDT", "-SWAP"),
                "tdMode": params['tdMode'],
                "side": "buy",
                "posSide": params['posSide'],
                "ordType": "limit",
                "sz": str(amount),
                "px": str(target_price)
            }
            logger.info(f"⚠️ [TEST WS] Estructura exacta que se enviará al socket de OKX: {okx_raw_payload}")
            
            # En CCXT Pro, create_order_ws abre/usa el socket privado autenticado
            if hasattr(self.exchange, 'create_order_ws'):
                order = await self.exchange.create_order_ws(
                    self.symbol, 'limit', 'buy', amount, target_price, params
                )
            else:
                logger.warning("create_order_ws no soportado, usando REST.")
                order = await self.exchange.create_order(
                    self.symbol, 'limit', 'buy', amount, target_price, params
                )
                
            logger.info(f"✅ [TEST WS] Orden creada exitosamente: {order['id']}")
            
            # Cancelar de inmediato para dejar limpio
            await asyncio.sleep(2)
            if hasattr(self.exchange, 'cancel_order_ws'):
                await self.exchange.cancel_order_ws(order['id'], self.symbol)
            else:
                await self.exchange.cancel_order(order['id'], self.symbol)
            logger.info("✅ [TEST WS] Orden cancelada. Test finalizado.")
            
        except Exception as e:
            # Imprimir el error crudo del exchange para ver qué dice exactamente OKX
            logger.error(f"❌ [TEST WS] FALLÓ: Error crudo devuelto por OKX: {repr(e)}")

    async def start(self):
        try:
            self.running = True
            self.metrics["status"] = "Running"
            await self.exchange.load_markets()
            
            if "/" not in self.symbol and "USDT" in self.symbol:
                base = self.symbol.replace("USDT", "").split(":")[0] 
                self.symbol = f"{base}/USDT:USDT"

            await self.fetch_historical_candles()
            if self.resume_existing_grid:
                grid_spacing_factor = float(self.ai_recommendation.get('grid_spacing_factor', 0.5)) / 100.0
                self.grid_spacing_usd = self.metrics['last_price'] * grid_spacing_factor
            else:
                # Comentamos la creación completa de la malla temporalmente para el TEST
                # await self.setup_grid_orders()
                await self._test_ws_order()
            
            self._ohlcv_task = asyncio.create_task(self._watch_ohlcv_loop())
            self._orders_task = asyncio.create_task(self._watch_orders_loop())
        except Exception as e:
            self.running = False
            logger.error(f"Error fatal: {e}")
