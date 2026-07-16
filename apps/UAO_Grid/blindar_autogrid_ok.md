quí tienes cómo integrar la protección del precio promedio y margen de ganancia directamente en el motor junto con el desbloqueo del ATR:

Modificaciones en core/engine.py
1. Liberar el ATR y definir el margen mínimo de ganancia en calcular_espaciado_atr
Reemplaza el inicio de calcular_espaciado_atr para permitir la contracción, pero calculando el piso de comisiones:

Python
    def calcular_espaciado_atr(self, df_5m: pd.DataFrame, market_info: dict = {}):
        """Calcula el spread dinámico permitiendo expansión y contracción seguras."""
        if df_5m is None or len(df_5m) < 15:
            return
            
        # 1. Liberamos el candado: permitimos que recalcule incluso con posición abierta
        self._atr_inicializado = True
            
        high = df_5m['high']
        low = df_5m['low']
        close = df_5m['close']
        
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        
        precio_actual = close.iloc[-1]
        if precio_actual > 0 and atr > 0:
            espaciado_calculado = (atr * self.atr_multiplicador) / precio_actual
            
            # ── A. DISTANCIA DINÁMICA CON PISO DE COMISIONES ──
            fee_maker = float(market_info.get("maker", 0.00020))
            fee_taker = float(market_info.get("taker", 0.00050))
            
            # Piso dinámico: Cubrir doble comisión (entrada + salida) + un margen neto de ganancia (ej. 0.15%)
            self.min_spread_rentable = (fee_maker + fee_taker) + 0.0015
            max_spread = 0.0080  # Techo: 0.80%
            
            # El espaciado nunca podrá ser menor a lo que garantiza ganancia real
            nuevo_espaciado = max(self.min_spread_rentable, min(espaciado_calculado, max_spread))
            
            if abs(self.espaciado_actual - nuevo_espaciado) > 1e-5:
                self.espaciado_actual = nuevo_espaciado
                
                cobertura_deseada = 0.04 
                lineas_calculadas = int(cobertura_deseada / self.espaciado_actual)
                self.num_lineas_lado = max(4, lineas_calculadas)
                self._recalcular_inversion_por_nivel()
                self._hubo_cambio_atr = True
            else:
                self._hubo_cambio_atr = False
2. Blindar actualizar_tps_dinamicos con el Precio Promedio
Aquí evitamos que, al contraerse el ATR, un Take Profit (TP) baje a una zona de pérdida respecto a toda tu posición:

Python
    def actualizar_tps_dinamicos(self, df_5m: pd.DataFrame):
        """Ajusta Take Profits activos garantizando que NUNCA generen pérdidas sobre el precio promedio."""
        if not getattr(self, '_hubo_cambio_atr', False) or df_5m is None or len(df_5m) == 0:
            return
        try:
            hubo_cambios = False
            distancia = max(self.espaciado_actual, getattr(self, 'min_spread_rentable', 0.0025))
            
            for n in self.niveles:
                if abs(n.get("level", 0)) < 100:
                    continue
                entrada = float(n.get("precio_original_entrada") or n.get("entry_price") or 0.0)
                if entrada <= 0:
                    continue
                
                if n.get("side") == "SELL":  # Cerrando un LONG
                    precio_tp_calculado = entrada * (1 + distancia)
                    # BLINDAJE ANTI-PÉRDIDAS: Si estamos en LONG, el TP debe estar estrictamente
                    # por encima de nuestro precio promedio global + margen mínimo de ganancia
                    if self.posicion_neta > 1e-9 and self.precio_promedio > 0:
                        precio_minimo_seguro = self.precio_promedio * (1 + getattr(self, 'min_spread_rentable', 0.0025))
                        nuevo_precio = max(precio_tp_calculado, precio_minimo_seguro)
                    else:
                        nuevo_precio = precio_tp_calculado
                        
                else:  # Cerrando un SHORT (n.get("side") == "BUY")
                    precio_tp_calculado = entrada * (1 - distancia)
                    # BLINDAJE ANTI-PÉRDIDAS: Si estamos en SHORT, el TP debe estar estrictamente
                    # por debajo de nuestro precio promedio global - margen mínimo de ganancia
                    if self.posicion_neta < -1e-9 and self.precio_promedio > 0:
                        precio_maximo_seguro = self.precio_promedio * (1 - getattr(self, 'min_spread_rentable', 0.0025))
                        nuevo_precio = min(precio_tp_calculado, precio_maximo_seguro)
                    else:
                        nuevo_precio = precio_tp_calculado

                if abs(float(n.get("price", 0.0)) - nuevo_precio) > 1e-12:
                    n["price"] = nuevo_precio
                    hubo_cambios = True
                    
            if hubo_cambios:
                self.malla_modificada = True
        except Exception as exc:
            logger.warning(f"No se pudieron actualizar TPs dinámicos: {exc}")
3. Reforzamiento en inicializar_grid para nuevas líneas
Al reconstruir o desplazar la malla, aseguramos que las nuevas líneas de cierre respeten ese mismo margen sobre el precio promedio:

Python
        # Dentro de inicializar_grid(self, precio_base: float):
        margen_seguro = getattr(self, 'min_spread_rentable', 0.0025)

        for i in range(1, self.num_lineas_lado + 1):
            if i not in niveles_cubiertos:
                # LÓGICA ANTI-PÉRDIDAS PARA LONG: El precio base para vender (TP) 
                # NUNCA debe estar por debajo del precio promedio + margen seguro.
                if self.posicion_neta > 1e-9 and self.precio_promedio > 0:
                    piso_rentable = self.precio_promedio * (1 + margen_seguro)
                    base_sell = max(self.centro_grid, piso_rentable)
                else:
                    base_sell = self.centro_grid
                    
                precio_sell = base_sell * (1 + (self.espaciado_actual * i))
                qty_sell = self.inversion_por_nivel / precio_sell
                self.niveles.append({"side": "SELL", "price": precio_sell, "qty": qty_sell, "level": i})

            if -i not in niveles_cubiertos:
                # LÓGICA ANTI-PÉRDIDAS PARA SHORT: El precio base para comprar (TP)
                # NUNCA debe estar por encima del precio promedio - margen seguro.
                if self.posicion_neta < -1e-9 and self.precio_promedio > 0:
                    techo_rentable = self.precio_promedio * (1 - margen_seguro)
                    base_buy = min(self.centro_grid, techo_rentable)
                else:
                    base_buy = self.centro_grid
                    
                precio_buy = base_buy * (1 - (self.espaciado_actual * i))
                qty_buy = self.inversion_por_nivel / precio_buy
                self.niveles.append({"side": "BUY", "price": precio_buy, "qty": qty_buy, "level": -i})
¿Qué logramos con este blindaje?
Contracción segura: Si la volatilidad baja considerablemente y el ATR manda a reducir la distancia del grid (ej. de 0.34% a 0.15%), el bot contraerá las órdenes sólo hasta donde sea matemáticamente rentable.

Protección al promediar: Si el mercado va en contra y tu posición acumula varias entradas, el self.precio_promedio se actualiza. Ninguna orden de cierre se reubicará por debajo de ese nuevo promedio más las comisiones del exchange (maker + taker + 0.15% net profit).