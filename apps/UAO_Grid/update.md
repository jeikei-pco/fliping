 SoluciónVamos a matar dos pájaros de un tiro. Primero, obligaremos al bot a darle tiempo a OKX para que actualice sus bases de datos. Segundo (y más importante para la gestión de riesgo), si una moneda activa el Kill-Switch, debe ir a la Lista Negra. Operar inmediatamente un activo que acaba de barrer tu Stop-Loss de emergencia es "Revenge Trading" y causa exactamente estos bucles.1. Modificar el Kill-Switch en el Orquestador (core/orquestador.py)Abre core/orquestador.py, localiza la función _loop_operativo y baja hasta el Paso 1: Kill-Switch (aproximadamente en la línea 440). Reemplaza ese bloque por este:  Python                    # 1. Kill-Switch (Prioridad Máxima)
                    if self.engine.evaluar_kill_switch(precio_actual):
                        self.provider.cancel_all_orders(symbol)
                        self.provider.close_position_market(symbol)
                        self.engine.reset()
                        self.engine.current_symbol = None
                        logger.warning(f"🚨 Posición cerrada por Kill-Switch. Forzando re-escaneo inmediato.")
                        
                        # [NUEVO 1] Bloquear el símbolo: Si nos sacó por Stop-Loss global, evitamos operarlo de nuevo en esta sesión.
                        self.db.agregar_a_lista_negra(symbol, self.mode)
                        
                        # [NUEVO 2] Pausa de seguridad: Damos 3 segundos a OKX para asentar la orden y actualizar su API REST.
                        time.sleep(3.0)
                        
                        self.wakeup_event.set()
                        break  # Sale del loop de este symbol
2. Proteger el "Botón de Pánico" del Drenaje (core/orquestador.py)Debemos aplicar la misma protección de tiempo cuando el drenaje se rinde por timeout (ya que también hace un cierre a mercado). En el mismo archivo, localiza la función _ciclo_reescaneo y actualiza el supervisor de timeout (aproximadamente en la línea 230):  Python        # --- NUEVO: SUPERVISOR DE TIMEOUT DE DRENAJE ---
        if self.engine.modo_drenaje:
            timeout_horas = float(os.getenv("GRID_ROTATION_TIMEOUT_HOURS", 2.0))
            if self.engine.es_timeout_drenaje(timeout_horas):
                logger.warning(f"⏱️ [BOTÓN DE PÁNICO] Drenaje en {self.engine.current_symbol} superó {timeout_horas}h. Cerrando a mercado para forzar rotación.")
                
                # 1. Limpiar las órdenes "pacientes" que no se llenaron
                self.provider.cancel_all_orders(self.engine.current_symbol)
                
                # 2. Cerrar agresivamente a mercado asumiendo pérdida/breakeven
                self.provider.close_position_market(self.engine.current_symbol)
                self.engine.modo_drenaje = False
                
                # [NUEVO] Pausa de seguridad para que OKX elimine la posición de su API
                time.sleep(3.0)
                
                # 3. Disparar la rotación hacia el símbolo pendiente
                proximo_symbol = getattr(self.engine, 'simbolo_destino_pendiente', None)
                if proximo_symbol:
                    self.engine.simbolo_destino_pendiente = None
                    self._iniciar_operacion(proximo_symbol)
                return  # Salir del ciclo para no interferir con la rotación
            else:
                logger.info(f"⏳ Drenaje paciente activo en {self.engine.current_symbol}. Esperando rentabilidad...")
                return 
        # -----------------------------------------------
Al agregar time.sleep(3.0) y bloquear la moneda con la lista negra, eliminas matemáticamente la posibilidad de un bucle infinito por consistencia eventual de la API. Tu memoria RAM se mantendrá estable y el bot rotará pacíficamente hacia el siguiente activo del escáner.