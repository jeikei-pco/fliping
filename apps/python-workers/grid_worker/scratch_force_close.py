import asyncio
import ccxt.async_support as ccxt
import os

API_KEY = "6d0eclab-a2e0-4f69-abdf-52f7aff98873"
SECRET = "6EE35AF035FD807885ED801D490A667E"
PASSPHRASE = "Demo1234."

async def force_close_all():
    exchange = ccxt.okx({
        'apiKey': API_KEY,
        'secret': SECRET,
        'password': PASSPHRASE,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'swap',
        }
    })
    
    exchange.set_sandbox_mode(True)
    
    try:
        await exchange.load_markets()
        print("Cancelando todas las órdenes abiertas...")
        
        try:
            open_orders = await exchange.fetch_open_orders()
            if open_orders:
                for order in open_orders:
                    try:
                        await exchange.cancel_order(order['id'], order['symbol'])
                        print(f"Orden cancelada: {order['id']} - {order['symbol']}")
                    except Exception as e:
                        print(f"Error cancelando orden {order['id']}: {e}")
            else:
                print("No hay órdenes abiertas.")
        except Exception as e:
            print(f"Error buscando órdenes abiertas: {e}")
            
        print("\nCerrando todas las posiciones abiertas...")
        try:
            positions = await exchange.fetch_positions()
            active_positions = [p for p in positions if p.get('contracts') and float(p['contracts']) > 0]
            
            if active_positions:
                for pos in active_positions:
                    symbol = pos['symbol']
                    side = pos['side']
                    amount = float(pos['contracts'])
                    
                    # Para cerrar una posición, tomamos la posición opuesta
                    order_side = 'sell' if side == 'long' else 'buy'
                    
                    print(f"Intentando cerrar posición: {symbol} | Lado original: {side} | Cantidad: {amount}")
                    
                    try:
                        # Crear orden de mercado (reduce only para cerrar la posición)
                        res = await exchange.create_order(
                            symbol,
                            'market',
                            order_side,
                            amount,
                            params={'reduceOnly': True}
                        )
                        print(f"Posición cerrada para {symbol}. Resultado: {res['id']}")
                    except Exception as e:
                        print(f"Error cerrando posición {symbol}: {e}")
            else:
                print("No hay posiciones activas.")
        except Exception as e:
            print(f"Error buscando posiciones: {e}")
            
    finally:
        await exchange.close()

if __name__ == "__main__":
    asyncio.run(force_close_all())
