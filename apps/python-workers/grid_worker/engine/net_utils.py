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
    Monkey-patch para forzar ThreadedResolver en la sesión aiohttp de ccxt.
    Debe llamarse DESPUÉS de crear la instancia del exchange y ANTES de
    cualquier llamada de red (load_markets, fetch_ohlcv, etc.).
    """
    original_open = exchange.open

    def patched_open(self_ref=exchange):
        if self_ref.session is None:
            if self_ref.asyncio_loop is None:
                self_ref.asyncio_loop = asyncio.get_running_loop()
                self_ref.throttler.loop = self_ref.asyncio_loop
            if self_ref.ssl_context is None:
                self_ref.ssl_context = (
                    ssl.create_default_context(cafile=self_ref.cafile)
                    if self_ref.verify else self_ref.verify
                )
            import socket
            resolver = aiohttp.resolver.ThreadedResolver()
            self_ref.tcp_connector = aiohttp.TCPConnector(
                ssl=self_ref.ssl_context,
                loop=self_ref.asyncio_loop,
                enable_cleanup_closed=True,
                resolver=resolver,
                family=socket.AF_INET,
            )
            self_ref.session = aiohttp.ClientSession(
                loop=self_ref.asyncio_loop,
                connector=self_ref.tcp_connector,
                trust_env=self_ref.aiohttp_trust_env,
            )
        else:
            original_open()

    exchange.open = patched_open
