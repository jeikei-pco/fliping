import asyncio
import re
import ccxt.pro as ccxt
import logging
import pandas as pd
from datetime import datetime
import time

from .math_core import calculate_keltner_channels, calculate_cv
from .net_utils import patch_ccxt_resolver

logger = logging.getLogger("GridWorker.OKX_WS")


def _create_exchange(exchange_id, api_key, secret, passphrase, sandbox=True):
    exchange_class = getattr(ccxt, exchange_id, ccxt.okx)
    config = {
        'apiKey': api_key,
        'secret': secret,
        'enableRateLimit': True,
        'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        },
        'options': {
            'defaultType': 'swap',
            'sandboxMode': sandbox,
        }
    }
    
    if passphrase:
        config['password'] = passphrase
        
    exchange = exchange_class(config)

    if sandbox:
        exchange.set_sandbox_mode(True)

    patch_ccxt_resolver(exchange)
    return exchange


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


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


async def detect_active_exchange_grid(exchange_id, api_key, secret, passphrase, sandbox=True):
    exchange = _create_exchange(exchange_id, api_key, secret, passphrase, sandbox)

    try:
        await exchange.load_markets()

        open_orders = []
        try:
            open_orders = await exchange.fetch_open_orders(None, None, None, {'instType': 'SWAP'})
        except TypeError:
            open_orders = await exchange.fetch_open_orders()
        except Exception as exc:
            logger.warning(f"No se pudieron consultar órdenes abiertas en OKX: {exc}")

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
            logger.warning(f"No se pudieron consultar posiciones abiertas en OKX: {exc}")

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
    finally:
        await exchange.close()


class OkxWsClient:
    def __init__(
        self,
        exchange_id,
        api_key,
        secret,
        passphrase,
        sandbox=True,
        symbol="BTC/USDT",
        timeframe="5m",
        base_capital=50.0,
        ai_recommendation=None,
        resume_existing_grid=False,
    ):
        self._exchange_id = exchange_id
        self._api_key = api_key
        self._secret = secret
        self._passphrase = passphrase
        self._sandbox = sandbox

        self.symbol = symbol
        self.timeframe = timeframe
        self.running = False
        self.base_capital = base_capital
        self.ai_recommendation = ai_recommendation or {}
        self.resume_existing_grid = resume_existing_grid

        self.df = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        self.exchange = _create_exchange(exchange_id, api_key, secret, passphrase, sandbox)
            
        self.metrics = {
            "status": "Initialized",
            "keltner": None,
            "cv": None,
            "last_price": None,
            "symbol": symbol,
            "mode": "resume" if resume_existing_grid else "create",
            "exchange_mode": "DEMO/SANDBOX" if sandbox else "REAL"
        }
        
        self.grid_spacing_usd = 0.0
        self.grid_lines = int(self.ai_recommendation.get('grid_lines', 10))
        self.processed_orders = set()
        self.ultima_ejecucion_ts = time.time()
        
        # OKX VIP0 Fees: Maker 0.02%, Taker 0.05%. Buscamos cubrir ida y vuelta + profit.
        self.min_profit_margin = 0.0015 # 0.15% mínimo de movimiento para ser rentable
        
        logger.info(f"🚀 Motor HFT Grid Inicializado: {self.metrics['exchange_mode']} | Symbol: {self.symbol}")

    async def fetch_historical_candles(self, limit=50):
        try:
            ohlcvs = await self.exchange.fetch_ohlcv(self.symbol, self.timeframe, limit=limit)
            data = [{'timestamp': pd.to_datetime(c[0], unit='ms'), 'open': c[1], 'high': c[2], 'low': c[3], 'close': c[4], 'volume': c[5]} for c in ohlcvs]
            self.df = pd.DataFrame(data)
            self.update_metrics()
        except Exception as e:
            logger.error(f"Error cargando velas: {e}")

    def update_metrics(self):
        if len(self.df) < 20: return
        upper, middle, lower = calculate_keltner_channels(self.df)
        cv = calculate_cv(self.df)
        
        self.metrics["keltner"] = {"upper": float(upper) if upper else None, "middle": float(middle) if middle else None, "lower": float(lower) if lower else None}
        self.metrics["cv"] = float(cv) if cv else None
        self.metrics["last_price"] = float(self.df.iloc[-1]['close'])
        self.metrics["updated_at"] = datetime.now().isoformat()

        # 🧠 CÁLCULO DINÁMICO DE ESPACIADO (Expansión/Contracción)
        if upper and lower:
            keltner_width = float(upper) - float(lower)
            dynamic_spacing = keltner_width / self.grid_lines
            min_spacing_usd = self.metrics["last_price"] * self.min_profit_margin
            
            # El espaciado se adapta a la volatilidad, pero NUNCA baja del umbral de comisiones
            self.grid_spacing_usd = max(dynamic_spacing, min_spacing_usd)

    async def setup_grid_orders(self):
        try:
            logger.info(f"Centrando malla dinámica para {self.symbol}...")
            open_orders = await self.exchange.fetch_open_orders(self.symbol)
            orders_to_cancel = [o['id'] for o in open_orders if not _is_reduce_only_order(o)]
            if orders_to_cancel: await self.exchange.cancel_orders(orders_to_cancel, self.symbol)

            leverage = float(self.ai_recommendation.get('leverage', 10.0))
            try: await self.exchange.set_leverage(leverage, self.symbol)
            except: pass
            try:
                if self.exchange.has.get('setPositionMode'): await self.exchange.set_position_mode(False, self.symbol)
            except: pass

            direction = self.ai_recommendation.get('direction', 'neutral')
            buy_lines = max(1, int(self.grid_lines * (0.7 if direction == 'long' else 0.3 if direction == 'short' else 0.5)))
            sell_lines = max(1, self.grid_lines - buy_lines)

            self.update_metrics() # Asegurar espaciado actualizado
            current_price = self.metrics['last_price']
            
            inversion_base_por_linea = self.base_capital / self.grid_lines
            valor_orden_apalancada = inversion_base_por_linea * leverage

            await self.exchange.load_markets()
            market = self.exchange.market(self.symbol)
            contract_size = market.get('contractSize', 1)

            raw_amount = (valor_orden_apalancada / current_price) / contract_size
            amount_str = self.exchange.amount_to_precision(self.symbol, raw_amount)
            amount = max(float(amount_str), float(market['limits']['amount']['min'] or 1.0))
            
            # 🛡️ OKX requiere enteros para contratos de SWAP
            if market.get('swap') or market.get('contract'):
                amount = int(amount)

            base_params = {'tdMode': 'cross', 'posSide': 'net', 'postOnly': True} # postOnly garantiza Maker Fee
            orders = []

            for i in range(1, buy_lines + 1):
                price = float(self.exchange.price_to_precision(self.symbol, current_price - (i * self.grid_spacing_usd)))
                orders.append({'symbol': self.symbol, 'type': 'limit', 'side': 'buy', 'amount': amount, 'price': price, 'params': base_params.copy()})
            
            for i in range(1, sell_lines + 1):
                price = float(self.exchange.price_to_precision(self.symbol, current_price + (i * self.grid_spacing_usd)))
                orders.append({'symbol': self.symbol, 'type': 'limit', 'side': 'sell', 'amount': amount, 'price': price, 'params': base_params.copy()})

            if self.exchange.has.get('createOrdersWs'): await self.exchange.create_orders_ws(orders)
            else: await asyncio.gather(*[self.exchange.create_order(o['symbol'], o['type'], o['side'], o['amount'], o['price'], o['params']) for o in orders], return_exceptions=True)

            self.ultima_ejecucion_ts = time.time()
            logger.info(f"✅ Malla lista. Spacing dinámico: {self.grid_spacing_usd:.4f} USDT")

        except Exception as e:
            logger.error(f"Error crítico enviando órdenes: {e}")

    async def _watch_orders_loop(self):
        logger.info("⚡ Iniciando HFT Fills Listener...")
        while self.running:
            try:
                orders = await self.exchange.watch_orders(self.symbol)
                for order in orders:
                    order_id = order.get('id')
                    if order.get('status') == 'closed' and order_id not in self.processed_orders:
                        self.processed_orders.add(order_id)
                        
                        filled_price = float(order.get('average') or order.get('price'))
                        side = order.get('side')
                        amount = float(order.get('filled') or order.get('amount')) 
                        
                        logger.info(f"✅ [FILL] {side.upper()} a {filled_price}. Lanzando Replenishment...")
                        self.ultima_ejecucion_ts = time.time()
                        
                        # 1. REPLENISHMENT INMEDIATO (Take Profit)
                        new_side = 'sell' if side == 'buy' else 'buy'
                        tp_price = filled_price + self.grid_spacing_usd if side == 'buy' else filled_price - self.grid_spacing_usd
                        tp_price = float(self.exchange.price_to_precision(self.symbol, tp_price))
                        
                        params = {'tdMode': 'cross', 'posSide': 'net', 'postOnly': True}
                        
                        # Ejecución asíncrona sin await para no bloquear el loop de lectura
                        asyncio.create_task(self._place_replenishment_order(new_side, amount, tp_price, params))
                            
            except Exception as e:
                logger.error(f"Error en WS watch_orders: {e}")
                await asyncio.sleep(2)

    async def _place_replenishment_order(self, side, amount, price, params):
        try:
            await asyncio.sleep(0.2) # Micro-pausa para liberación de margen
            await self.exchange.create_order(symbol=self.symbol, type='limit', side=side, amount=amount, price=price, params=params)
            logger.info(f"🎯 [TP/REPLENISH] {side.upper()} colocada a {price}")
        except Exception as e:
            logger.warning(f"⚠️ Fallo PostOnly TP para {side} a {price}, reintentando como Limit normal: {e}")
            try:
                # Quitamos postOnly para garantizar que la orden entre
                fallback_params = {k: v for k, v in params.items() if k != 'postOnly'}
                await self.exchange.create_order(symbol=self.symbol, type='limit', side=side, amount=amount, price=price, params=fallback_params)
                logger.info(f"🎯 [TP FALLBACK] {side.upper()} colocada a {price} (Normal Limit)")
            except Exception as inner_e:
                logger.error(f"❌ Error fatal colocando TP de fallback: {inner_e}")

    async def _watch_ohlcv_loop(self):
        logger.info(f"📊 Iniciando monitor de Velas y Anti-Escape para {self.symbol}...")
        while self.running:
            try:
                candles = await self.exchange.watch_ohlcv(self.symbol, self.timeframe)
                is_new_candle = False
                
                for ohlcv in candles:
                    ts = pd.to_datetime(ohlcv[0], unit='ms')
                    new_row = {'timestamp': ts, 'open': ohlcv[1], 'high': ohlcv[2], 'low': ohlcv[3], 'close': ohlcv[4], 'volume': ohlcv[5]}
                    
                    if len(self.df) > 0 and self.df.iloc[-1]['timestamp'] == ts:
                        for key in new_row: self.df.loc[self.df.index[-1], key] = new_row[key]
                    else:
                        self.df = pd.concat([self.df, pd.DataFrame([new_row])], ignore_index=True)
                        if len(self.df) > 100: self.df = self.df.iloc[1:]
                        is_new_candle = True
                
                self.metrics["last_price"] = float(candles[-1][4])
                
                if is_new_candle:
                    self.update_metrics() # Actualiza Keltner y recalcula grid_spacing_usd
                    await self._check_grid_boundaries() # Lógica Anti-Escape
                    
                    # Purga de seguridad por volumen de operaciones
                    if len(self.processed_orders) > 2000:
                        logger.info("🧹 Purgando caché de órdenes procesadas (Límite 2000 alcanzado).")
                        self.processed_orders.clear()

                    # Inactividad Normal
                    if time.time() - self.ultima_ejecucion_ts > (15 * 60):
                        logger.info(f"⏳ [INACTIVIDAD] 15 min sin operaciones. Purgando memoria y re-evaluando malla...")
                        self.processed_orders.clear() # Limpieza por inactividad
                        await self._check_grid_boundaries(force_recenter=True)
                        self.ultima_ejecucion_ts = time.time()
                    
            except Exception as e:
                logger.error(f"Error en WS watch_ohlcv: {e}")
                await asyncio.sleep(2)

    async def _check_grid_boundaries(self, force_recenter=False):
        """ 🛡️ CONTINUOUS SHIFTING: Mantiene el precio siempre dentro de la malla """
        try:
            open_orders = await self.exchange.fetch_open_orders(self.symbol)
            if not open_orders: return

            buys = [o for o in open_orders if o['side'] == 'buy']
            sells = [o for o in open_orders if o['side'] == 'sell']
            current_price = self.metrics["last_price"]

            # Si no hay órdenes de un lado, asumimos que el precio ya rompió la malla
            highest_sell = max([o['price'] for o in sells]) if sells else (current_price - self.grid_spacing_usd)
            lowest_buy = min([o['price'] for o in buys]) if buys else (current_price + self.grid_spacing_usd)

            # 📈 Lógica Treadmill UP
            if (current_price >= (highest_sell - self.grid_spacing_usd)) or force_recenter:
                if buys: # Asegurarnos de que hay compras para reciclar
                    furthest_buy = min(buys, key=lambda x: x['price'])
                    logger.info(f"🔄 [SHIFT UP] Moviendo compra lejana de {furthest_buy['price']} hacia arriba.")
                    await self.exchange.cancel_order(furthest_buy['id'], self.symbol)
                    
                    highest_current_buy = max([o['price'] for o in buys])
                    new_price = float(self.exchange.price_to_precision(self.symbol, highest_current_buy + self.grid_spacing_usd))
                    
                    params = {'tdMode': 'cross', 'posSide': 'net', 'postOnly': True}
                    await asyncio.sleep(0.1) # Breve pausa por rate limits
                    await self.exchange.create_order(self.symbol, 'limit', 'sell', furthest_buy['amount'], new_price, params)

            # 📉 Lógica Treadmill DOWN
            elif (current_price <= (lowest_buy + self.grid_spacing_usd)) or force_recenter:
                if sells: # Asegurarnos de que hay ventas para reciclar
                    furthest_sell = max(sells, key=lambda x: x['price'])
                    logger.info(f"🔄 [SHIFT DOWN] Moviendo venta lejana de {furthest_sell['price']} hacia abajo.")
                    await self.exchange.cancel_order(furthest_sell['id'], self.symbol)
                    
                    lowest_current_sell = min([o['price'] for o in sells])
                    new_price = float(self.exchange.price_to_precision(self.symbol, lowest_current_sell - self.grid_spacing_usd))
                    
                    params = {'tdMode': 'cross', 'posSide': 'net', 'postOnly': True}
                    await asyncio.sleep(0.1) # Breve pausa por rate limits
                    await self.exchange.create_order(self.symbol, 'limit', 'buy', furthest_sell['amount'], new_price, params)

        except Exception as e:
            logger.error(f"Error en Continuous Shifting: {e}")

    async def start(self):
        self.running = True
        self.metrics["status"] = "Running"
        await self.exchange.load_markets()
        
        if "/" not in self.symbol and "USDT" in self.symbol:
            base = self.symbol.replace("USDT", "").split(":")[0] 
            self.symbol = f"{base}/USDT:USDT"

        if self.symbol not in self.exchange.markets:
            logger.error(f"⛔ ERROR: El mercado {self.symbol} NO EXISTE en los SWAPS.")
            self.running = False
            self.metrics["status"] = "Error - Invalid Symbol"
            await self.exchange.close()
            return

        try:
            balance = await self.exchange.fetch_balance()
            usdt_balance = float(balance.get('USDT', {}).get('free', 0.0))
            if self.base_capital > usdt_balance:
                logger.error(f"Inversión requerida ({self.base_capital}) > balance libre ({usdt_balance}).")
                self.running = False
                await self.exchange.close()
                return
        except Exception as e:
            logger.warning(f"No se pudo obtener el balance: {e}")

        await self.fetch_historical_candles()

        if self.resume_existing_grid:
            logger.info(f"Reanudando monitoreo para {self.symbol}")
            self.ultima_ejecucion_ts = time.time()
            self.update_metrics()
        else:
            await self.setup_grid_orders()
        
        self._ohlcv_task = asyncio.create_task(self._watch_ohlcv_loop())
        self._orders_task = asyncio.create_task(self._watch_orders_loop())
        await asyncio.gather(self._ohlcv_task, self._orders_task, return_exceptions=True)
