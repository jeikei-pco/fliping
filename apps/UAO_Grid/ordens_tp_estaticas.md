el objetivo es que las órdenes Take Profit (TP) sean completamente inamovibles una vez que se crean (capturando la distancia exacta del ATR en el instante en que se ejecuta la orden de entrada) y que se marquen estrictamente como reduce_only, debemos remover la lógica que cambiaba sus precios dinámicamente en la respiración de 30 segundos.

El sistema ya cuenta con la infraestructura para esto; solo necesitamos desactivar la actualización de precios en caliente para los niveles superiores o iguales a 100.

Aquí tienes las modificaciones exactas para lograr este comportamiento en tu arquitectura:

1. Modificación en core/engine.py
Desactivar el recalculo de TPs existentes
En tu archivo core/engine.py, la función actualizar_tps_dinamicos era la encargada de buscar los niveles de Take Profit (abs(level) >= 100) y alterar su precio cuando el ATR cambiaba. Al dejar esta función limpia o deshabilitada, garantizamos que ningún ciclo de respiro o variación del auto-grid pueda mover un TP ya establecido.

Reemplaza la función en core/engine.py por lo siguiente:

Python
    def actualizar_tps_dinamicos(self, df_5m: pd.DataFrame):
        """
        [MODIFICADO] Las órdenes Take Profit activas ahora son INAMOVIBLES.
        No se altera el precio de los TPs abiertos durante la respiración en vivo.
        """
        # Mantener como pass para no romper referencias en el orquestador
        pass
Verificación de la creación del TP en la ejecución
Cuando una orden base de la malla se llena, procesar_ejecucion_simulada se ejecuta inmediatamente. Aquí es donde el bot captura el valor de self.espaciado_actual calculado en el último respiro y fija el precio del TP de forma permanente:

Python
            # GENERAR EL TAKE PROFIT DINÁMICO EN CONTRA (Se ejecuta en procesar_ejecucion_simulada)
            tp_side = "SELL" if side == "BUY" else "BUY"
            mult = (1 + self.espaciado_actual) if side == "BUY" else (1 - self.espaciado_actual)
            tp_price = price * mult
            
            nivel_origen = abs(n_ejecutado["level"])
            tp_level = (nivel_origen // 100) if nivel_origen >= 100 else (nivel_origen * 100)
            if tp_level == 0: tp_level = 9999
            
            # Este diccionario guarda el precio fijo e inalterable del TP
            self.niveles.append({
                "side": tp_side,
                "price": tp_price,
                "qty": n_ejecutado["qty"],
                "level": tp_level,
                "precio_original_entrada": price
            })
            self.malla_modificada = True
Verificación del flag reduce_only
Tu función obtener_ordenes_deseadas en core/engine.py ya lee el level de cada orden. Si el nivel pertenece a un TP (abs(level) >= 100), le inyecta automáticamente el parámetro reduce_only=True antes de enviarlo a la capa de reconciliación de OKX:

Python
        deseadas = []
        for n in top_n:
            precio = self._redondear_precio(n["price"], tick_sz)
            qty = self._redondear_qty(n["qty"], lot_sz)
            if qty > 0:
                oid = f"grid_{n['level']}_{precio}"
                is_tp = abs(n['level']) >= 100  # 👈 Detecta si es un nivel de Take Profit
                deseadas.append(Order(
                    order_id=oid, 
                    symbol=self.current_symbol, 
                    side=n["side"], 
                    price=precio, 
                    qty=qty, 
                    grid_level=n['level'],
                    reduce_only=is_tp  # 👈 Aplica reduce_only de forma estricta
                ))
2. Comportamiento del Sistema con este Cambio
Antes de la ejecución (Malla Base): Cada 30 segundos, el orquestador llamará a calcular_espaciado_atr. Si el mercado cambia de volatilidad, self.espaciado_actual se expandirá o contraerá de manera fluida. Las órdenes de compra/venta de la malla base se reajustarán si ocurre un breakout del rango.

Al ejecutarse la orden: En el milisegundo exacto en que entra un fill, el bot toma la última distancia guardada en self.espaciado_actual, calcula el precio objetivo del TP, y lo guarda en la lista de niveles con un identificador >= 100.

Persistencia Estricta: Al desactivar el cuerpo de actualizar_tps_dinamicos, esas órdenes asignadas a la plataforma con reduce_only jamás volverán a ser editadas o canceladas por fluctuaciones del ATR. Permanecerán fijas en el libro de órdenes de OKX hasta que el precio las toque para tomar ganancias o se limpie la malla por completo.