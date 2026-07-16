from abc import ABC, abstractmethod

# ---------------------------------------------------------
# 1. PUERTO (La Interfaz abstracta)
# ---------------------------------------------------------
class CredentialPort(ABC):
    @abstractmethod
    def get_ccxt_config(self) -> dict:
        """Debe retornar un diccionario compatible con la configuración de CCXT"""
        pass

# ---------------------------------------------------------
# 2. ADAPTADOR (Implementación concreta Real vs Demo)
# ---------------------------------------------------------
class EnvironmentAdapter(CredentialPort):
    def __init__(self, api_key: str, secret: str, passphrase: str = None, sandbox: bool = True):
        self.api_key = api_key
        self.secret = secret
        self.passphrase = passphrase
        self.sandbox = sandbox

    def get_ccxt_config(self) -> dict:
        """Construye la configuración inyectando el modo correcto"""
        config = {
            'apiKey': self.api_key,
            'secret': self.secret,
            'enableRateLimit': True,
            'timeout': 30000, # Ideal para evitar cortes en Docker/Ubuntu
            'options': {
                'defaultType': 'swap',
                'sandboxMode': self.sandbox
            }
        }
        
        if self.passphrase:
            config['password'] = self.passphrase
            
        return config