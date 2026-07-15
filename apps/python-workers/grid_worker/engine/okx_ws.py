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


def _create_okx_exchange(api_key, secret, passphrase, sandbox=True):
    exchange = ccxt.okx({
        'apiKey': api_key,
        'secret': secret,
        'password': passphrase,
        'enableRateLimit': True,
        'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        },
        'options': {
            'defaultType': 'swap',
            'fetchMarkets': ['swap'],
            'sandboxMode': sandbox,
        }
    })

    if sandbox:
        exchange.set_sandbox_mode(True)
        # Forzar endpoints de websocket para evitar ambigüedades
        exchange.urls['api']['ws'] = {
            'public': 'wss://wspap.okx.com:8443/ws/v5/public',
            'private': 'wss://wspap.okx.com:8443/ws/v5/private'
        }

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


async def detect_active_exchange_grid(api_key, secret, passphrase, sandbox=True):
    exchange = _create_okx_exchange(api_key, secret, passphrase, sandbox)

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
        # Guardar credenciales para posible fallback a sandbox
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

        # DataFrame para mantener las velas
        self.df = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        self.exchange = _create_okx_exchange(api_key, secret, passphrase, sandbox)
            
        self.metrics = {
            "status": "Initialized",
            "keltner": None,
            "cv": None,
            "last_price": None,
            "symbol": symbol,
            "mode": "resume" if resume_existing_grid else "create",
            "exchange_mode": "DEMO/SANDBOX" if sandbox else "REAL"
        }
        
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
            # 1. Limpieza inicial de órdenes que no sean Take Profits
            logger.info(f"Cancelando órdenes abiertas para {self.symbol}...")
            try:
                open_orders = await self.exchange.fetch_open_orders(self.symbol)
                orders_to_cancel = [o['id'] for o in open_orders if not _is_reduce_only_order(o)]
                if orders_to_cancel:
                    await self.exchange.cancel_orders(orders_to_cancel, self.symbol)
            except Exception as e:
                logger.warning(f"Error gestionando órdenes previas: {e}")

            # 2. Configuración Dinámica de la Malla
            leverage = float(self.ai_recommendation.get('leverage', 10.0))
            await self.exchange.set_leverage(leverage, self.symbol)

            # Cálculo de líneas y simetría
            grid_lines = int(self.ai_recommendation.get('grid_lines', 10))
            if grid_lines % 2 != 0: grid_lines += 1 # Asegurar paridad
            
            buy_lines = grid_lines // 2
            sell_lines = grid_lines // 2
            
            grid_spacing_factor = float(self.ai_recommendation.get('grid_spacing_factor', 0.5)) / 100.0
            
            # Inversión
            effective_investment = self.base_capital * leverage
            usd_per_line = effective_investment / grid_lines

            await self.exchange.load_markets()
            current_price = self.metrics['last_price']
            spacing = current_price * grid_spacing_factor
            
            market = self.exchange.market(self.symbol)
            contract_size = market.get('contractSize', 1)

            # 3. Preparación del bloque de órdenes
            orders = []
            logger.info(f"Grid Dinámico: {grid_lines} líneas -> {buy_lines} Buy | {sell_lines} Sell | Leverage: x{leverage}")

            raw_amount = (usd_per_line / current_price) / contract_size
            amount = float(self.exchange.amount_to_precision(self.symbol, raw_amount))
            amount = max(amount, float(market['limits']['amount']['min'] or 1.0))

            base_params = {'tdMode': 'cross'}

            # Órdenes de Compra
            for i in range(1, buy_lines + 1):
                price = float(self.exchange.price_to_precision(self.symbol, current_price - (i * spacing)))
                params_buy = base_params.copy()
                params_buy['posSide'] = 'long'
                orders.append({
                    'symbol': self.symbol, 'type': 'limit', 'side': 'buy',
                    'amount': amount, 'price': price, 'params': params_buy
                })
            
            # Órdenes de Venta
            for i in range(1, sell_lines + 1):
                price = float(self.exchange.price_to_precision(self.symbol, current_price + (i * spacing)))
                params_sell = base_params.copy()
                params_sell['posSide'] = 'short'
                orders.append({
                    'symbol': self.symbol, 'type': 'limit', 'side': 'sell',
                    'amount': amount, 'price': price, 'params': params_sell
                })

            # 4. Envío optimizado vía WebSocket (Canal Privado)
            if self.exchange.has.get('createOrdersWs'):
                logger.info(f"Transmitiendo {len(orders)} órdenes a OKX vía WS Batch...")
                response = await self.exchange.create_orders_ws(orders)
                logger.info(f"✅ WS Batch ejecutado: {response}")
            else:
                # Si el exchange no soporta WS, lanzamos error explícito para no degradar a REST
                raise Exception("WebSocket no disponible para creación masiva")

            self.ultima_ejecucion_ts = time.time()
            self.malla_modificada = True

        except Exception as e:
            logger.error(f"Error crítico enviando órdenes: {e}")
            raise

    def evaluar_inactividad_velas(self, minutos: int = 20) -> bool:
        """Regla de las 4 Velas (5m * 4 = 20 min). Si no hay operaciones, desliza el grid."""
        if not self.running:
            return False
            
        segundos_inactivos = time.time() - getattr(self, 'ultima_ejecucion_ts', time.time())
        
        if segundos_inactivos > (minutos * 60):
            logger.info(f"⏳ [INACTIVIDAD] {minutos} min sin ejecuciones. Solicitando re-centrado dinámico del Grid.")
            # Reiniciar timer para no hacer spam si tarda en reajustar
            self.ultima_ejecucion_ts = time.time() 
            return True
            
        return False

    async def _watch_orders_loop(self):
        logger.info("Iniciando escucha de Fills de Órdenes (Canal Privado)...")
        while self.running:
            try:
                orders = await self.exchange.watch_orders(self.symbol)
                for order in orders:
                    status = order.get('status')
                    if status == 'closed':
                        logger.info(f"✅ [FILL DETECTADO] Orden ejecutada. Reseteando temporizador de inactividad.")
                        self.ultima_ejecucion_ts = time.time()
            except Exception as e:
                logger.error(f"Error en websocket (watch_orders): {e}")
                await asyncio.sleep(5)

    async def _watch_ohlcv_loop(self):
        logger.info(f"Iniciando escucha de Velas para {self.symbol}...")
        while self.running:
            try:
                # CCXT.pro subscribeToOHLCV returns a list of candles
                candles = await self.exchange.watch_ohlcv(self.symbol, self.timeframe)
                
                is_new_candle = False
                
                # Actualizar el DataFrame con la vela más reciente
                for ohlcv in candles:
                    ts = pd.to_datetime(ohlcv[0], unit='ms')
                    
                    new_row = {
                        'timestamp': ts,
                        'open': ohlcv[1],
                        'high': ohlcv[2],
                        'low': ohlcv[3],
                        'close': ohlcv[4],
                        'volume': ohlcv[5]
                    }
                    
                    # Si el timestamp ya existe (vela actualizándose), la reemplazamos
                    if len(self.df) > 0 and self.df.iloc[-1]['timestamp'] == ts:
                        for key in new_row:
                            self.df.loc[self.df.index[-1], key] = new_row[key]
                    else:
                        # NUEVA VELA CREADA (Pasaron los 5m)
                        new_df = pd.DataFrame([new_row])
                        self.df = pd.concat([self.df, new_df], ignore_index=True)
                        if len(self.df) > 100:
                            self.df = self.df.iloc[1:]
                        
                        is_new_candle = True
                
                # 1. Actualización ligera y silenciosa: Mantener el precio vivo para Redis
                self.metrics["last_price"] = float(candles[-1][4])
                
                # 2. Actualización pesada: Recalcular matemáticas y log SOLO al cerrar la vela
                if is_new_candle:
                    self.update_metrics()
                    
                    # 3. Chequear inactividad
                    if self.evaluar_inactividad_velas(minutos=20):
                        logger.info(f"🔄 [RESPIRACIÓN VIVO] Malla re-centrada para {self.symbol}")
                        await self.setup_grid_orders()
                    
            except Exception as e:
                logger.error(f"Error en websocket (watch_ohlcv): {e}")
                await asyncio.sleep(5)

    
    async def start(self):
        self.running = True
        self.metrics["status"] = "Running"
        
        # 1. Cargar mercados y validar si el símbolo existe en OKX
        await self.exchange.load_markets()
        
        # REEMPLAZA LA LÓGICA ANTERIOR POR ESTO:
        # Asegurarnos de que el formato sea estrictamente el de CCXT para SWAPS (con barra)
        if "/" not in self.symbol and "USDT" in self.symbol:
            base = self.symbol.replace("USDT", "")
            # Limpiar por si trae los dos puntos pegados
            base = base.split(":")[0] 
            self.symbol = f"{base}/USDT:USDT"
            logger.info(f"Símbolo auto-corregido a formato CCXT: {self.symbol}")

        if self.symbol not in self.exchange.markets:
            available_swaps = [k for k, v in self.exchange.markets.items() if v.get('swap')]
            logger.error(f"⛔ ERROR: El mercado {self.symbol} NO EXISTE en los SWAPS de OKX.")
            logger.error(f"Futuros disponibles en OKX (Mostrando 20): {', '.join(available_swaps[:20])}...")
            self.running = False
            self.metrics["status"] = "Error - Invalid Symbol"
            await self.exchange.close()
            return  # Salimos de la función sin iniciar los bucles

        # 2. Obtener el balance de la cuenta y validarlo
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

        # 3. Si el mercado existe y hay balance, continuamos normalmente
        await self.fetch_historical_candles()

        if self.resume_existing_grid:
            logger.info(f"Reanudando monitoreo de grid existente en OKX para {self.symbol}")
            self.ultima_ejecucion_ts = time.time()
        else:
            await self.setup_grid_orders()
        
        self._ohlcv_task = asyncio.create_task(self._watch_ohlcv_loop())
        self._orders_task = asyncio.create_task(self._watch_orders_loop())
        
        await asyncio.gather(self._ohlcv_task, self._orders_task, return_exceptions=True)
        self.running = False
        self.metrics["status"] = "Stopped"
        logger.info("Deteniendo OKX Websocket...")
        if hasattr(self, '_ohlcv_task') and not self._ohlcv_task.done():
            self._ohlcv_task.cancel()
        if hasattr(self, '_orders_task') and not self._orders_task.done():
            self._orders_task.cancel()
