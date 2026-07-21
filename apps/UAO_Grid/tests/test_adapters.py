import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Añadir el path para que los tests encuentren 'core'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.adapters.gemini_adapter import GeminiAdapter
from core.adapters.openai_adapter import OpenAIAdapter
from core.adapters.anthropic_adapter import AnthropicAdapter
from core.adapters.openrouter_adapter import OpenRouterAdapter
from core.adapters.groq_adapter import GroqAdapter
from core.adapters.ai_provider_factory import get_api_providers

class TestAIAdapters(unittest.TestCase):

    @patch('google.genai.Client')
    def test_gemini_adapter(self, MockClient):
        # Configurar el mock
        mock_client_instance = MockClient.return_value
        mock_interaction = MagicMock()
        mock_interaction.text = '{"success": true, "provider": "gemini"}'
        mock_client_instance.interactions.create.return_value = mock_interaction

        adapter = GeminiAdapter(api_key="fake-key")
        result = adapter.generate_json("Test prompt")
        
        self.assertIsNotNone(result)
        self.assertIn("success", result)
        mock_client_instance.interactions.create.assert_called_once()

    @patch('openai.OpenAI')
    def test_openai_adapter(self, MockOpenAI):
        mock_client_instance = MockOpenAI.return_value
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"success": true, "provider": "openai"}'))]
        mock_client_instance.chat.completions.create.return_value = mock_response

        adapter = OpenAIAdapter(api_key="fake-key", url="https://api.openai.com/v1/chat/completions", model="gpt-4o-mini")
        result = adapter.generate_json("Test prompt")
        
        self.assertIsNotNone(result)
        self.assertIn("success", result)
        mock_client_instance.chat.completions.create.assert_called_once()

    @patch('openrouter.OpenRouter')
    def test_openrouter_adapter(self, MockOpenRouter):
        mock_client_instance = MockOpenRouter.return_value
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"success": true, "provider": "openrouter"}'))]
        mock_client_instance.chat.completions.create.return_value = mock_response

        adapter = OpenRouterAdapter(api_key="fake-key")
        result = adapter.generate_json("Test prompt")
        
        self.assertIsNotNone(result)
        self.assertIn("success", result)
        mock_client_instance.chat.completions.create.assert_called_once()

    @patch('groq.Groq')
    def test_groq_adapter(self, MockGroq):
        mock_client_instance = MockGroq.return_value
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"success": true, "provider": "groq"}'))]
        mock_client_instance.chat.completions.create.return_value = mock_response

        adapter = GroqAdapter(api_key="fake-key")
        result = adapter.generate_json("Test prompt")
        
        self.assertIsNotNone(result)
        self.assertIn("success", result)
        mock_client_instance.chat.completions.create.assert_called_once()

    @patch('urllib.request.urlopen')
    def test_anthropic_adapter(self, mock_urlopen):
        # Mock the response for urllib
        mock_response = MagicMock()
        # Simulated raw SSE or JSON from Anthropic Proxy
        mock_response.read.return_value = b'{"content": [{"type": "text", "text": "{\\"success\\": true, \\"provider\\": \\"anthropic\\"}"}]}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        adapter = AnthropicAdapter(api_key="fake-key", url="http://fake-url", model="fake-model")
        result = adapter.generate_json("Test prompt")
        
        self.assertIsNotNone(result)
        self.assertIn("success", result)
        mock_urlopen.assert_called_once()

    @patch.dict(os.environ, {
        "GEMINI_API_KEY": "fake-gemini",
        "OPENAI_API_KEY": "fake-openai",
        "OPENROUTER_API_KEY": "fake-openrouter",
        "GROQ_API_KEY": "fake-groq",
        "CLAUDE_CODE_PROXY_API_KEY": "fake-claude"
    })
    def test_ai_provider_factory(self):
        providers = get_api_providers()
        self.assertGreater(len(providers), 0)
        
        # Check that we have one of each registered
        provider_types = [type(p) for p in providers]
        self.assertIn(GeminiAdapter, provider_types)
        self.assertIn(OpenAIAdapter, provider_types)
        self.assertIn(AnthropicAdapter, provider_types)
        self.assertIn(OpenRouterAdapter, provider_types)
        self.assertIn(GroqAdapter, provider_types)

if __name__ == '__main__':
    unittest.main()
