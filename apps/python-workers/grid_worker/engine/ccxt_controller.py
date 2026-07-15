import ccxt.pro as ccxt 
import logging
import socket
from .exchange_ports import CredentialPort

# Forzar resolución IPv4 a nivel global para CCXT y aiohttp
_getaddrinfo = socket.getaddrinfo
def force_ipv4_getaddrinfo(*args, **kwargs):
    if len(args) >= 3:
        if args[2] in (0, socket.AF_UNSPEC):
            args = list(args)
            args[2] = socket.AF_INET
            args = tuple(args)
    else:
        if kwargs.get('family', 0) in (0, socket.AF_UNSPEC):
            kwargs['family'] = socket.AF_INET
    return _getaddrinfo(*args, **kwargs)

socket.getaddrinfo = force_ipv4_getaddrinfo

logger = logging.getLogger("GridWorker.CCXTController")

class CCXTController:
    """
    Controlador centralizado de CCXT.
    Recibe un Puerto de Credenciales (Adaptador) por inyección de dependencias.
    """
    def __init__(self, exchange_id: str, credential_adapter: CredentialPort):
        self.exchange_id = exchange_id.lower()
        self.config = credential_adapter.get_ccxt_config()
        self.exchange = None
        self._initialize_exchange()

    def _initialize_exchange(self):
        try:
            exchange_class = getattr(ccxt, self.exchange_id)
            self.exchange = exchange_class(self.config)
            
            # Activación explícita del sandbox si el adaptador lo dictaminó
            if self.config.get('options', {}).get('sandboxMode', False):
                self.exchange.set_sandbox_mode(True)
                
        except AttributeError:
            logger.error(f"El exchange {self.exchange_id} no está soportado por CCXT.")
            raise

    def get_instance(self):
        """Devuelve la instancia asíncrona de CCXT lista para usar"""
        return self.exchange