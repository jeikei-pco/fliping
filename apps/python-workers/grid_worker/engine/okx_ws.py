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

        logger.info(
            "Validación OKX -> source=%s, symbol=%s, positions=%s, entry_orders=%s",
            source,
            symbol,
            len(active_positions),
            len(entry_orders),
        )

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
        logger.info("Symbol -> %s", symbol)
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
        
        # Variables de memoria para el Grid Continuo
        self.grid_spacing_usd = 0.0
        self.processed_orders = set() # Filtro Anti-Spam
        
        logger.info(f"🚀 Motor de Trading (OkxWsClient) Inicializado en modo: {self.metrics['exchange_mode']}")
        
    async def fetch_historical_candles(self, limit=50):
        try:
            logger.info(f"Cargando {limit} velas históricas para {self.symbol} ({self.timeframe})...")
            ohlcvs = await self.exchange.fetch_ohlcv(self.symbol, self.timeframe, limit=limit)
            
            data = []
            for ohlcv in ohlcvs:
                data.append({
                    'timestamp': pd.to_datetime(ohlcv[0], unit='ms'),
                    'open': ohlcv[1],
                    'high': ohlcv[2],
                    'low': ohlcv[3],
                    'close': ohlcv[4],
                    'volume': ohlcv[5]
                })
            self.df = pd.DataFrame(data)
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
        
        logger.info(f"Metrics Updated -> Last: {self.metrics['last_price']}, CV: {cv:.5f}, Mid: {middle:.2f}")

    async def setup_grid_orders(self):
        try:
            # 1. Limpieza inicial
            logger.info(f"Cancelando órdenes abiertas para {self.symbol}...")
            try:
                open_orders = await self.exchange.fetch_open_orders(self.symbol)
                orders_to_cancel = [o['id'] for o in open_orders if not _is_reduce_only_order(o)]
                if orders_to_cancel:
                    await self.exchange.cancel_orders(orders_to_cancel, self.symbol)
            except Exception as e:
                logger.warning(f"Error gestionando órdenes previas: {e}")

            # 2. Configuración Dinámica
            leverage = float(self.ai_recommendation.get('leverage', 10.0))
            try:
                await self.exchange.set_leverage(leverage, self.symbol)
            except Exception as e:
                logger.warning(f"Error o no soportado set_leverage: {e}")
                
            try:
                if self.exchange.has.get('setPositionMode'):
                    await self.exchange.set_position_mode(False, self.symbol)
            except Exception as e:
                logger.warning(f"Error o no soportado set_position_mode (One-Way): {e}")
                
            try:
                if self.exchange.has.get('setMarginMode'):
                    await self.exchange.set_margin_mode('cross', self.symbol)
            except Exception as e:
                logger.warning(f"Error o no soportado set_margin_mode: {e}")

            grid_lines = int(self.ai_recommendation.get('grid_lines', 10))
            if grid_lines % 2 != 0: grid_lines += 1 
            
            buy_lines = grid_lines // 2
            sell_lines = grid_lines // 2
            
            grid_spacing_factor = float(self.ai_recommendation.get('grid_spacing_factor', 0.5)) / 100.0
            
            # ---------------------------------------------------------
            # 🚨 NUEVA LÓGICA DE CÁLCULO DE TAMAÑO DE ORDEN
            # ---------------------------------------------------------
            # 1. Dividimos la inversión total entre las líneas
            inversion_base_por_linea = self.base_capital / grid_lines
            
            # 2. Multiplicamos por el apalancamiento para obtener el valor real de la orden en el mercado
            valor_orden_apalancada = inversion_base_por_linea * leverage
            
            logger.info(f"💰 Capital Base: {self.base_capital} USDT | Líneas: {grid_lines} | Apalancamiento: {leverage}x")
            logger.info(f"📊 Inversión real por línea: {inversion_base_por_linea:.2f} USDT -> Valor apalancado por orden: {valor_orden_apalancada:.2f} USDT")
            # ---------------------------------------------------------

            await self.exchange.load_markets()
            current_price = self.metrics['last_price']
            
            # GUARDAR SPACING EN MEMORIA PARA EL GRID CONTINUO
            self.grid_spacing_usd = current_price * grid_spacing_factor
            
            market = self.exchange.market(self.symbol)
            contract_size = market.get('contractSize', 1)

            orders = []
            logger.info(f"Grid Dinámico: {grid_lines} líneas -> {buy_lines} Buy | {sell_lines} Sell")

            # Calculamos la cantidad de contratos/monedas usando el valor apalancado
            raw_amount = (valor_orden_apalancada / current_price) / contract_size
            amount = float(self.exchange.amount_to_precision(self.symbol, raw_amount))
            amount = max(amount, float(market['limits']['amount']['min'] or 1.0))

            base_params = {}
            if self._exchange_id == 'okx':
                base_params['tdMode'] = 'cross'

            # Órdenes de Compra
            for i in range(1, buy_lines + 1):
                price = float(self.exchange.price_to_precision(self.symbol, current_price - (i * self.grid_spacing_usd)))
                params_buy = base_params.copy()
                if self._exchange_id == 'okx': params_buy['posSide'] = 'net'
                elif self._exchange_id == 'binance': params_buy['positionSide'] = 'BOTH'
                
                orders.append({
                    'symbol': self.symbol, 'type': 'limit', 'side': 'buy',
                    'amount': amount, 'price': price, 'params': params_buy
                })
            
            # Órdenes de Venta
            for i in range(1, sell_lines + 1):
                price = float(self.exchange.price_to_precision(self.symbol, current_price + (i * self.grid_spacing_usd)))
                params_sell = base_params.copy()
                if self._exchange_id == 'okx': params_sell['posSide'] = 'net'
                elif self._exchange_id == 'binance': params_sell['positionSide'] = 'BOTH'
                
                orders.append({
                    'symbol': self.symbol, 'type': 'limit', 'side': 'sell',
                    'amount': amount, 'price': price, 'params': params_sell
                })

            # 4. Envío optimizado
            exchange_name = self._exchange_id.upper()
            if self.exchange.has.get('createOrdersWs'):
                response = await self.exchange.create_orders_ws(orders)
                logger.info(f"✅ WS Batch ejecutado: {len(orders)} órdenes.")
            elif self.exchange.has.get('createOrders'):
                response = await self.exchange.create_orders(orders)
                logger.info(f"✅ REST Batch ejecutado con éxito.")
            else:
                tasks = []
                for o in orders:
                    tasks.append(self.exchange.create_order(
                        o['symbol'], o['type'], o['side'], o['amount'], o['price'], o['params']
                    ))
                response = await asyncio.gather(*tasks, return_exceptions=True)
                logger.info(f"✅ REST Concurrente ejecutado.")

            self.ultima_ejecucion_ts = time.time()
            self.malla_modificada = True

        except Exception as e:
            logger.error(f"Error crítico enviando órdenes: {e}")
            raise

    def evaluar_inactividad_velas(self, minutos: int = 20) -> bool:
        if not self.running:
            return False
            
        segundos_inactivos = time.time() - getattr(self, 'ultima_ejecucion_ts', time.time())
        
        if segundos_inactivos > (minutos * 60):
            logger.info(f"⏳ [INACTIVIDAD] {minutos} min sin ejecuciones. Solicitando re-centrado dinámico del Grid.")
            self.ultima_ejecucion_ts = time.time() 
            return True
            
        return False

    async def _watch_orders_loop(self):
        logger.info("Iniciando escucha de Fills de Órdenes (Canal Privado)...")
        while self.running:
            try:
                orders = await self.exchange.watch_orders(self.symbol)
                for order in orders:
                    order_id = order.get('id')
                    status = order.get('status')
                    
                    # Filtro Anti-Spam: Solo procesar órdenes cerradas que no hayamos visto
                    if status == 'closed' and order_id not in self.processed_orders:
                        self.processed_orders.add(order_id)
                        
                        filled_price = order.get('average') or order.get('price')
                        side = order.get('side')
                        amount = order.get('amount')
                        
                        logger.info(f"✅ [FILL DETECTADO] Orden {side} ejecutada a {filled_price}. Reponiendo malla...")
                        self.ultima_ejecucion_ts = time.time()
                        
                        # LÓGICA DE GRID CONTINUO (Replenishment)
                        if side == 'buy':
                            # Si compramos, colocamos Take Profit (Venta) un nivel arriba
                            new_price = filled_price + self.grid_spacing_usd
                            new_side = 'sell'
                        else:
                            # Si vendimos, colocamos Take Profit (Compra) un nivel abajo
                            new_price = filled_price - self.grid_spacing_usd
                            new_side = 'buy'
                            
                        new_price = float(self.exchange.price_to_precision(self.symbol, new_price))
                        
                        params = {}
                        if self._exchange_id == 'okx':
                            params['tdMode'] = 'cross'
                            params['posSide'] = 'net'
                        elif self._exchange_id == 'binance':
                            params['positionSide'] = 'BOTH'
                            
                        try:
                            await self.exchange.create_order(
                                symbol=self.symbol,
                                type='limit',
                                side=new_side,
                                amount=amount,
                                price=new_price,
                                params=params
                            )
                            logger.info(f"🔄 [GRID REPLENISH] Nueva orden {new_side} colocada a {new_price}")
                        except Exception as e:
                            logger.error(f"Error colocando orden de reposición: {e}")
                            
            except Exception as e:
                logger.error(f"Error en websocket (watch_orders): {e}")
                await asyncio.sleep(5)

    async def _watch_ohlcv_loop(self):
        logger.info(f"Iniciando escucha de Velas para {self.symbol}...")
        while self.running:
            try:
                candles = await self.exchange.watch_ohlcv(self.symbol, self.timeframe)
                is_new_candle = False
                
                for ohlcv in candles:
                    ts = pd.to_datetime(ohlcv[0], unit='ms')
                    new_row = {
                        'timestamp': ts, 'open': ohlcv[1], 'high': ohlcv[2],
                        'low': ohlcv[3], 'close': ohlcv[4], 'volume': ohlcv[5]
                    }
                    
                    if len(self.df) > 0 and self.df.iloc[-1]['timestamp'] == ts:
                        for key in new_row:
                            self.df.loc[self.df.index[-1], key] = new_row[key]
                    else:
                        new_df = pd.DataFrame([new_row])
                        self.df = pd.concat([self.df, new_df], ignore_index=True)
                        if len(self.df) > 100:
                            self.df = self.df.iloc[1:]
                        is_new_candle = True
                
                self.metrics["last_price"] = float(candles[-1][4])
                
                if is_new_candle:
                    self.update_metrics()
                    if self.evaluar_inactividad_velas(minutos=20):
                        logger.info(f"🔄 [RESPIRACIÓN VIVO] Malla re-centrada para {self.symbol}")
                        await self.setup_grid_orders()
                    
            except Exception as e:
                logger.error(f"Error en websocket (watch_ohlcv): {e}")
                await asyncio.sleep(5)

    async def start(self):
        self.running = True
        self.metrics["status"] = "Running"
        
        await self.exchange.load_markets()
        
        if "/" not in self.symbol and "USDT" in self.symbol:
            base = self.symbol.replace("USDT", "")
            base = base.split(":")[0] 
            self.symbol = f"{base}/USDT:USDT"
            logger.info(f"Símbolo auto-corregido a formato CCXT: {self.symbol}")

        if self.symbol not in self.exchange.markets:
            available_swaps = [k for k, v in self.exchange.markets.items() if v.get('swap')]
            logger.error(f"⛔ ERROR: El mercado {self.symbol} NO EXISTE en los SWAPS de OKX.")
            self.running = False
            self.metrics["status"] = "Error - Invalid Symbol"
            await self.exchange.close()
            return

        try:
            balance = await self.exchange.fetch_balance()
            usdt_balance = float(balance.get('USDT', {}).get('free', 0.0))
            logger.info(f"Balance de la cuenta OKX: {usdt_balance:.2f} USDT")
            
            if self.base_capital > usdt_balance:
                logger.error(f"Inversión requerida ({self.base_capital} USDT) es mayor al balance libre ({usdt_balance} USDT). Deteniendo bot.")
                self.running = False
                await self.exchange.close()
                return
        except Exception as e:
            logger.warning(f"No se pudo obtener el balance: {e}")

        await self.fetch_historical_candles()

        if self.resume_existing_grid:
            logger.info(f"Reanudando monitoreo de grid existente en OKX para {self.symbol}")
            self.ultima_ejecucion_ts = time.time()
            # Calcular spacing en caso de reanudación
            grid_spacing_factor = float(self.ai_recommendation.get('grid_spacing_factor', 0.5)) / 100.0
            self.grid_spacing_usd = self.metrics['last_price'] * grid_spacing_factor
        else:
            await self.setup_grid_orders()
        
        self._ohlcv_task = asyncio.create_task(self._watch_ohlcv_loop())
        self._orders_task = asyncio.create_task(self._watch_orders_loop())
