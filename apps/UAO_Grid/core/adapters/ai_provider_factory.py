import os
from typing import List
from core.ports.ai_provider_port import AIProviderPort
from core.adapters.gemini_adapter import GeminiAdapter
from core.adapters.openai_adapter import OpenAIAdapter
from core.adapters.anthropic_adapter import AnthropicAdapter
from core.adapters.openrouter_adapter import OpenRouterAdapter
from core.adapters.groq_adapter import GroqAdapter

def get_api_providers() -> List[AIProviderPort]:
    """
    Construye y devuelve una lista de adaptadores (AIProviderPort) 
    disponibles según las variables de entorno.
    """
    providers: List[AIProviderPort] = []
    
    # Gemini (Native SDK)
    for key_name in ["GEMINI_API_KEY", "GEMINI_API_KEY2", "GEMINI_API_KEY3"]:
        val = os.getenv(key_name)
        if val:
            providers.append(GeminiAdapter(api_key=val, model="gemini-2.0-flash"))
            
    # OpenRouter
    for key_name in ["OPENROUTER_API_KEY", "OPENROUTER_API_KEY2"]:
        val = os.getenv(key_name)
        if val:
            providers.append(OpenRouterAdapter(
                api_key=val,
                model="google/gemini-2.0-flash-001:free"
            ))
            
    # OpenAI
    for key_name in ["OPENAI_API_KEY", "OPENAI_API_KEY2"]:
        val = os.getenv(key_name)
        if val:
            providers.append(OpenAIAdapter(
                api_key=val, 
                url="https://api.openai.com/v1/chat/completions", 
                model="gpt-4o-mini"
            ))
            
    # Groq
    for key_name in ["GROQ_API_KEY"]:
        val = os.getenv(key_name)
        if val:
            providers.append(GroqAdapter(
                api_key=val,
                url="https://api.groq.com/openai/v1",
                model="llama-3.3-70b-versatile"
            ))
            
    # Anthropic (Official API)
    for key_name in ["ANTHROPIC_API_KEY"]:
        val = os.getenv(key_name)
        if val:
            providers.append(AnthropicAdapter(
                api_key=val, 
                url="https://api.anthropic.com/v1/messages", 
                model="claude-3-5-haiku-20241022"
            ))
    
    return providers