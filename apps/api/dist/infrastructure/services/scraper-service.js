export class FirecrawlScraperService {
    baseUrl = "https://api.firecrawl.dev";
    /**
     * Scraped a single URL or crawls a site (limit pages).
     * Uses /v1/scrape for single pages and /v1/crawl for multi-page.
     */
    async crawl(params) {
        const limit = params.limit ?? 1;
        if (limit === 1) {
            return this.scrapeSingle(params.url, params.apiKey);
        }
        return this.crawlSite(params.url, params.apiKey, limit);
    }
    async scrapeSingle(url, apiKey) {
        const response = await fetch(`${this.baseUrl}/v1/scrape`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${apiKey}`,
            },
            body: JSON.stringify({
                url,
                formats: ["markdown"],
            }),
        });
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Firecrawl scrape error ${response.status}: ${errorText}`);
        }
        const json = (await response.json());
        if (!json.success || !json.data) {
            throw new Error(`Firecrawl falló: ${json.error ?? "respuesta vacía"}`);
        }
        return [
            {
                url,
                markdown: json.data.markdown ?? "",
                rawHtml: json.data.html,
            },
        ];
    }
    async crawlSite(url, apiKey, limit) {
        // Iniciar crawl
        const startResponse = await fetch(`${this.baseUrl}/v1/crawl`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${apiKey}`,
            },
            body: JSON.stringify({
                url,
                limit,
                scrapeOptions: { formats: ["markdown"] },
            }),
        });
        if (!startResponse.ok) {
            const errorText = await startResponse.text();
            throw new Error(`Firecrawl crawl error ${startResponse.status}: ${errorText}`);
        }
        const startJson = (await startResponse.json());
        if (!startJson.success || !startJson.id) {
            throw new Error(`Firecrawl crawl start falló: ${startJson.error ?? "sin ID"}`);
        }
        // Polling hasta que el crawl termine (máx 60s)
        const crawlId = startJson.id;
        const maxAttempts = 12;
        let attempt = 0;
        while (attempt < maxAttempts) {
            await new Promise((resolve) => setTimeout(resolve, 5000));
            attempt++;
            const statusResponse = await fetch(`${this.baseUrl}/v1/crawl/${crawlId}`, {
                headers: { Authorization: `Bearer ${apiKey}` },
            });
            if (!statusResponse.ok)
                continue;
            const statusJson = (await statusResponse.json());
            if (statusJson.success && statusJson.data && statusJson.data.length > 0) {
                return statusJson.data.map((page) => ({
                    url: page.metadata?.sourceURL ?? url,
                    markdown: page.markdown ?? "",
                    rawHtml: page.html,
                }));
            }
        }
        throw new Error("Firecrawl crawl timeout: no completó en 60 segundos.");
    }
}
