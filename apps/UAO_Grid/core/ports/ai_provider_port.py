from abc import ABC, abstractmethod
from typing import Optional

class AIProviderPort(ABC):
    """
    Puerto (Interface) para los proveedores de IA.
    Define el contrato para generar contenido basado en un prompt.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Nombre del proveedor (ej. Gemini, OpenAI, Anthropic)"""
        pass

    @abstractmethod
    def generate_json(self, prompt: str) -> Optional[str]:
        """
        Genera una respuesta en formato JSON a partir del prompt dado.
        
        Args:
            prompt (str): El texto/contexto a enviar a la IA.
            
        Returns:
            Optional[str]: La respuesta cruda en JSON si tiene éxito, None en caso de error.
        """
        pass
