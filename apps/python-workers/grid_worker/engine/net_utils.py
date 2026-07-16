import ssl
import socket
import aiohttp

def patch_ccxt_resolver(exchange):
    """
    Fuerza el uso de IPv4 y ThreadedResolver para evitar problemas 
    de aiodns en Windows. Compatible con aiohttp 3.9+.
    """
    if getattr(exchange, 'tcp_connector', None) is None:
        verify = getattr(exchange, 'verify', True)
        cafile = getattr(exchange, 'cafile', None)
        
        if verify:
            ssl_context = ssl.create_default_context(cafile=cafile)
        else:
            ssl_context = False

        resolver = aiohttp.resolver.ThreadedResolver()
        
        # ELIMINADO 'loop=loop' que causa errores en Python 3.10+
        exchange.tcp_connector = aiohttp.TCPConnector(
            ssl=ssl_context,
            enable_cleanup_closed=True,
            resolver=resolver,
            family=socket.AF_INET,
        )