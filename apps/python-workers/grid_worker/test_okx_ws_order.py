import asyncio
import ccxt.pro as ccxt

async def main():
    api_key = "6d0eclab-a2e0-4f69-abdf-52f7aff98873"
    secret = "6EE35AF035FD807885ED801D490A667E"
    passphrase = "Demo1234."

    print("Iniciando conexión a OKX en modo Sandbox (Demo)...")
    exchange = ccxt.okx({
        'apiKey': api_key,
        'secret': secret,
        'password': passphrase,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'swap',
            'sandboxMode': True
        }
    })
    
    # Activar sandbox explícitamente (inyecta x-simulated-trading: 1 de forma global)
    exchange.set_sandbox_mode(True)

    symbol = 'BTC/USDT:USDT'
    
    try:
        print(f"Cargando mercados...")
        await exchange.load_markets()
        
        # 1. Configurar apalancamiento a 15x
        print("Configurando apalancamiento a 15x en Long...")
        try:
            await exchange.set_leverage(15, symbol)
            print("Apalancamiento configurado con exito.")
        except Exception as e:
            print(f"Nota apalancamiento: {e}")

        # 2. Obtener precio actual para colocar la orden lejos
        ticker = await exchange.fetch_ticker(symbol)
        current_price = ticker['last']
        
        # Calcular precio 20% por debajo del actual (lejos)
        target_price = current_price * 0.8
        target_price = float(exchange.price_to_precision(symbol, target_price))
        
        # 3. Calcular tamaño en base a 5 USDT y 15x apalancamiento (75 USD nocional)
        inversion = 5.0
        apalancamiento = 15.0
        valor_nocional = inversion * apalancamiento
        
        market = exchange.market(symbol)
        contract_size = market.get("contractSize", 1)
        raw_amount = valor_nocional / target_price / contract_size
        amount = float(exchange.amount_to_precision(symbol, raw_amount))
        
        if amount <= 0:
            amount = 1.0 # Mínimo 1 contrato
            
        print(f"Precio actual: {current_price} | Precio de la orden Limit (lejos): {target_price}")
        print(f"Monto nocional: {valor_nocional} USDT | Tamano contratos (amount): {amount}")

        # 4. Verificar modo de cuenta para asignar posSide correctamente
        pos_side = 'long'
        try:
            account_config = await exchange.private_get_account_config()
            pos_mode = account_config['data'][0]['posMode']
            if pos_mode == 'net_mode':
                pos_side = 'net'
                print("Modo de cuenta detectado: Unidireccional (net_mode)")
            else:
                print("Modo de cuenta detectado: Cobertura (long_short_mode)")
        except Exception as e:
            print(f"No se pudo determinar el posMode: {e}")

        # Definir los parámetros de la orden
        params = {
            'tdMode': 'cross',
            'lever': '15',
            'posSide': pos_side
        }
        
        print(f"Enviando orden Limit via WebSocket con params: {params}...")
        
        # En CCXT Pro, el soporte para enviar órdenes por WebSocket nativo varía.
        # Algunos exchanges lo soportan con create_order_ws
        if hasattr(exchange, 'create_order_ws'):
            print("Utilizando WebSocket Nativo (create_order_ws)...")
            order = await exchange.create_order_ws(
                symbol=symbol,
                type='limit',
                side='buy',
                amount=amount,
                price=target_price,
                params=params
            )
        else:
            print("create_order_ws no disponible nativamente, usando el motor estándar de CCXT Pro...")
            order = await exchange.create_order(
                symbol=symbol,
                type='limit',
                side='buy',
                amount=amount,
                price=target_price,
                params=params
            )
            
        print(f"¡Orden creada exitosamente! ID de la orden: {order['id']}")
        
        print("Esperando 3 segundos antes de eliminarla...")
        await asyncio.sleep(3)
        
        print("Cancelando la orden para limpiar el entorno...")
        if hasattr(exchange, 'cancel_order_ws'):
            cancel_result = await exchange.cancel_order_ws(order['id'], symbol)
        else:
            cancel_result = await exchange.cancel_order(order['id'], symbol)
            
        print(f"Orden cancelada. Status: {cancel_result.get('status', 'canceled')}")

    except Exception as e:
        print(f"Error durante el test: {type(e).__name__} - {e}")
    finally:
        await exchange.close()
        print("Conexión cerrada.")

if __name__ == "__main__":
    asyncio.run(main())
