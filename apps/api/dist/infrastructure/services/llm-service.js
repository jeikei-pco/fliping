export class OpenRouterLLMService {
    baseUrl = "https://openrouter.ai/api/v1";
    async chat(params) {
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
        const json = (await response.json());
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
