Plan de Optimización para OKX (Grid Worker)

Objetivos de la Optimización

Parámetros Dinámicos (IA): Utilizar la cantidad de líneas y el apalancamiento recomendados por la optimización (ai_recommendation).

Distribución Simétrica y Fraccionada: Dividir el número total de líneas a la mitad (50% long, 50% short) y fraccionar la inversión total (apalancada) de manera uniforme entre todas las órdenes.

Mitigar Error 51155: Capturar la restricción local/KYC de OKX para evitar que el bot se quede en un bucle infinito de reinicios.

Precisión de API: Usar correctamente los métodos de ccxt para respetar los tamaños de tick (precio) y lot (cantidad) requeridos por OKX.

Modificaciones en el Código (okx_ws.py)

Debes reemplazar el método setup_grid_orders y ajustar el método start en tu archivo apps/python-workers/grid_worker/engine/okx_ws.py.

1. Actualizar setup_grid_orders

Este método ahora extrae dinámicamente los parámetros recomendados y calcula el capital por línea con precisión matemática.

    async def setup_grid_orders(self):
        try:
            # 1. Limpieza inicial (Cancelación de órdenes que no sean Take Profits)
            logger.info(f"Cancelando órdenes abiertas para {self.symbol}...")
            try:
                open_orders = await self.exchange.fetch_open_orders(self.symbol)
                orders_to_cancel = [o['id'] for o in open_orders if not _is_reduce_only_order(o)]
                if orders_to_cancel:
                    await self.exchange.cancel_orders(orders_to_cancel, self.symbol)
            except Exception as e:
                logger.warning(f"Error gestionando órdenes previas: {e}")

            # 2. Configuración Dinámica de la Malla (Basado en IA)
            leverage = float(self.ai_recommendation.get('leverage', 10.0))
            await self.exchange.set_leverage(leverage, self.symbol)

            grid_lines = int(self.ai_recommendation.get('grid_lines', 10))
            
            # Asegurar que el número de líneas sea par para mantener simetría perfecta
            if grid_lines % 2 != 0:
                grid_lines += 1
                
            buy_lines = grid_lines // 2
            sell_lines = grid_lines // 2
            
            grid_spacing_factor = float(self.ai_recommendation.get('grid_spacing_factor', 0.5)) / 100.0
            
            # Inversión dividida por el número de líneas total y apalancada
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

            # Cálculo de cantidad (asegurando precisión del exchange y lotes mínimos)
            raw_amount = (usd_per_line / current_price) / contract_size
            amount = float(self.exchange.amount_to_precision(self.symbol, raw_amount))
            amount = max(amount, float(market['limits']['amount']['min'] or 1.0))

            # Matriz de órdenes (Symmetric Buy/Sell)
            for i in range(1, buy_lines + 1):
                price = float(self.exchange.price_to_precision(self.symbol, current_price - (i * spacing)))
                orders.append({
                    'symbol': self.symbol, 'type': 'limit', 'side': 'buy',
                    'amount': amount, 'price': price
                })
                
            for i in range(1, sell_lines + 1):
                price = float(self.exchange.price_to_precision(self.symbol, current_price + (i * spacing)))
                orders.append({
                    'symbol': self.symbol, 'type': 'limit', 'side': 'sell',
                    'amount': amount, 'price': price
                })

            # 4. Envío Atómico (Batch)
            if self.exchange.has['createOrders']:
                logger.info(f"Transmitiendo bloque masivo de {len(orders)} órdenes a OKX...")
                try:
                    await self.exchange.create_orders(orders)
                except Exception as order_exc:
                    _check_okx_51155(order_exc)  # Propaga OkxAccountRestrictedError si es 51155
                    raise  # Re-lanza cualquier otro error
                logger.info("✅ Bloque ejecutado exitosamente.")
            else:
                raise Exception("El exchange no soporta Batch Orders")

            self.ultima_ejecucion_ts = time.time()
            self.malla_modificada = True

        except OkxAccountRestrictedError:
            raise  # Subir al caller para detener el worker
        except Exception as e:
            logger.error(f"Error al configurar órdenes del grid: {e}")


2. Actualizar start para manejar bloqueos (Resiliencia)

Asegúrate de que tu método start capture el error propagado y detenga la ejecución limpiamente.

    async def start(self):
        self.running = True
        self.metrics["status"] = "Running"
        
        await self.fetch_historical_candles()

        try:
            if self.resume_existing_grid:
                logger.info(f"Reanudando monitoreo de grid existente en OKX para {self.symbol} sin recrear órdenes.")
                self.ultima_ejecucion_ts = time.time()
            else:
                await self.setup_grid_orders()
            
            self._ohlcv_task = asyncio.create_task(self._watch_ohlcv_loop())
            self._orders_task = asyncio.create_task(self._watch_orders_loop())
            
            # Recolectar resultados de las tareas asíncronas
            results = await asyncio.gather(self._ohlcv_task, self._orders_task, return_exceptions=True)
            
            # Revisar si alguna tarea falló por restricción de OKX
            for res in results:
                if isinstance(res, OkxAccountRestrictedError):
                    logger.critical("⛔ DETENCIÓN DE EMERGENCIA: Restricción de cuenta (Error 51155) detectada en OKX.")
                    await self.stop()
                    
        except OkxAccountRestrictedError as e:
            logger.critical(f"⛔ FALLO AL INICIAR: {e}")
            await self.stop()