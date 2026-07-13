import type { LLMResponse } from "../../domain/model.js";
import type { LLMPort } from "../../domain/ports.js";

interface OpenRouterMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

interface OpenRouterResponse {
  id: string;
  choices: Array<{
    message: {
      role: string;
      content: string;
    };
    finish_reason: string;
  }>;
  model: string;
  usage?: {
    total_tokens?: number;
  };
  error?: { message: string };
}

export class OpenRouterLLMService implements LLMPort {
  private readonly baseUrl = "https://openrouter.ai/api/v1";

  async chat(params: {
    apiKey: string;
    model: string;
    messages: OpenRouterMessage[];
    temperature?: number;
  }): Promise<LLMResponse> {
    const response = await fetch(`${this.baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${params.apiKey}`,
        "HTTP-Referer": "https://jk-flipping.local",
        "X-Title": "JK-Flipping",
      },
      body: JSON.stringify({
        model: params.model,
        messages: params.messages,
        temperature: params.temperature ?? 0.3,
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`OpenRouter error ${response.status}: ${errorText}`);
    }

    const json = (await response.json()) as OpenRouterResponse;

    if (json.error) {
      throw new Error(`OpenRouter API error: ${json.error.message}`);
    }

    const content = json.choices[0]?.message?.content ?? "";

    return {
      content,
      model: json.model,
      tokensUsed: json.usage?.total_tokens,
    };
  }
}
