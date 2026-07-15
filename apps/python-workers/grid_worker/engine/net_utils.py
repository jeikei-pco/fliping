"""
Utilidades de red para ccxt async en Windows.

Soluciona el problema de DNS donde aiodns (c-ares) no puede
contactar los servidores DNS nativos del SO. Fuerza el uso de
ThreadedResolver que usa el DNS nativo de Python/OS.
"""
import asyncio
import ssl
import aiohttp


def patch_ccxt_resolver(exchange):
    """
    Pre-inicializa el tcp_connector de aiohttp con ThreadedResolver.
    Al hacer esto antes de que CCXT llame a open(), CCXT usará este conector
    pero inicializará la sesión con todos los headers y parámetros necesarios,
    evitando romper la autenticación de WebSockets privados.
    """
    import asyncio
    import ssl
    import socket
    import aiohttp

    if getattr(exchange, 'tcp_connector', None) is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
            
        verify = getattr(exchange, 'verify', True)
        cafile = getattr(exchange, 'cafile', None)
        
        if verify:
            ssl_context = ssl.create_default_context(cafile=cafile)
        else:
            ssl_context = False

        resolver = aiohttp.resolver.ThreadedResolver()
        exchange.tcp_connector = aiohttp.TCPConnector(
            ssl=ssl_context,
            loop=loop,
            enable_cleanup_closed=True,
            resolver=resolver,
            family=socket.AF_INET,
        )
