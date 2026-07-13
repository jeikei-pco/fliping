import type { ScrapedPage } from "../../domain/model.js";
import type { ScraperPort } from "../../domain/ports.js";

interface FirecrawlScrapeResponse {
  success: boolean;
  data?: {
    markdown?: string;
    html?: string;
    metadata?: { sourceURL?: string };
  };
  error?: string;
}

interface FirecrawlCrawlResponse {
  success: boolean;
  data?: Array<{
    markdown?: string;
    html?: string;
    metadata?: { sourceURL?: string };
  }>;
  error?: string;
}

export class FirecrawlScraperService implements ScraperPort {
  private readonly baseUrl = "https://api.firecrawl.dev";

  /**
   * Scraped a single URL or crawls a site (limit pages).
   * Uses /v1/scrape for single pages and /v1/crawl for multi-page.
   */
  async crawl(params: { url: string; apiKey: string; limit?: number }): Promise<ScrapedPage[]> {
    const limit = params.limit ?? 1;

    if (limit === 1) {
      return this.scrapeSingle(params.url, params.apiKey);
    }

    return this.crawlSite(params.url, params.apiKey, limit);
  }

  private async scrapeSingle(url: string, apiKey: string): Promise<ScrapedPage[]> {
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

    const json = (await response.json()) as FirecrawlScrapeResponse;

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

  private async crawlSite(url: string, apiKey: string, limit: number): Promise<ScrapedPage[]> {
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

    const startJson = (await startResponse.json()) as { success: boolean; id?: string; error?: string };

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

      if (!statusResponse.ok) continue;

      const statusJson = (await statusResponse.json()) as FirecrawlCrawlResponse & {
        status?: string;
      };

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
