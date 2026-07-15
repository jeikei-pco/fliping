import type { AlertRecord, EngineStatusRecord, MotorKind, OpportunityRecord } from "../domain/model.js";
import type {
  AlertRepository,
  EngineStatusRepository,
  LLMPort,
  OpportunityRepository,
  ScraperPort,
} from "../domain/ports.js";
import { CredentialVaultService } from "./use-cases.js";

// ======================================================================
//  AlertService — Sprint 3: leer y marcar alertas
// ======================================================================

export class AlertService {
  constructor(private readonly repository: AlertRepository) {}

  async listAlerts(userId: string, motor?: MotorKind): Promise<AlertRecord[]> {
    if (motor) {
      return this.repository.listByMotor(userId, motor);
    }
    return this.repository.listByUser(userId);
  }

  async markAsRead(id: string): Promise<void> {
    return this.repository.markAsRead(id);
  }
}

// ======================================================================
//  OpportunityService — Sprint 4: listar, detalle, cambiar estado
// ======================================================================

export class OpportunityService {
  constructor(private readonly repository: OpportunityRepository) {}

  async listOpportunities(
    userId: string,
    motor?: "real-estate" | "saas",
  ): Promise<OpportunityRecord[]> {
    if (motor) {
      return this.repository.listByMotor(userId, motor);
    }
    return this.repository.listByUser(userId);
  }

  async getOpportunity(id: string): Promise<OpportunityRecord | null> {
    return this.repository.findById(id);
  }

  async updateStatus(id: string, status: "reviewed" | "archived"): Promise<void> {
    return this.repository.updateStatus(id, status);
  }
}

// ======================================================================
//  EngineManagerService — controlar estado de los motores en DB
// ======================================================================

export class EngineManagerService {
  constructor(private readonly repository: EngineStatusRepository) {}

  async getStatus(userId: string, motor: MotorKind): Promise<EngineStatusRecord | null> {
    return this.repository.findByUserAndMotor(userId, motor);
  }

  async setEnabled(userId: string, motor: MotorKind, enabled: boolean): Promise<EngineStatusRecord> {
    return this.repository.save({ userId, motor, enabled });
  }

  async setConfig(userId: string, motor: MotorKind, config: string): Promise<EngineStatusRecord> {
    const current = await this.getStatus(userId, motor);
    return this.repository.save({ 
      userId, 
      motor, 
      enabled: current ? current.enabled : false, 
      config 
    });
  }
}

// ======================================================================
//  Tipos de análisis IA — definidos a nivel de módulo para evitar `never`
// ======================================================================

interface TechAnalysis {
  productFound: boolean;
  lowestPriceUsd: number | null;
  productTitle: string | null;
  isGoodDeal: boolean;
  reason: string;
}

interface RealEstateAnalysis {
  opportunityFound: boolean;
  title: string;
  description: string;
  estimatedARV: string;
  estimatedRepair: string;
  estimatedMAO: string;
  listedPrice: string;
  dealScore: number;
  tags: string[];
  analysis: string;
  sourceUrl: string | null;
}

interface SaasAnalysis {
  opportunityFound: boolean;
  title: string;
  description: string;
  askingPrice: string;
  monthlyRevenue: string;
  annualRevenue: string;
  revenueMultiple: string;
  techStack: string[];
  dealScore: number;
  tags: string[];
  analysis: string;
  growthPotential: string;
  sourceUrl: string | null;
}

function parseJsonFromLLM<T>(content: string): T | null {
  try {
    const jsonMatch = content.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      return JSON.parse(jsonMatch[0]) as T;
    }
  } catch {
    // Ignorar error de parsing
  }
  return null;
}

// ======================================================================
//  TechEngineService — Sprint 3: Motor Retail Tech (Firecrawl + Groq)
// ======================================================================

export type TechEngineConfig = {
  targetUrl: string;
  productName: string;
  maxPriceUsd: number;
};

export class TechEngineService {
  private timer: NodeJS.Timeout | null = null;
  private readonly intervalMs = 5 * 60 * 1000; // 5 minutos

  constructor(
    private readonly vault: CredentialVaultService,
    private readonly scraper: ScraperPort,
    private readonly llm: LLMPort,
    private readonly alertRepo: AlertRepository,
    private readonly engineStatusRepo: EngineStatusRepository,
  ) {}

  async toggle(params: {
    userId: string;
    enabled: boolean;
    config?: TechEngineConfig;
  }): Promise<EngineStatusRecord> {
    if (!params.enabled) {
      this.stop();
      return this.engineStatusRepo.save({
        userId: params.userId,
        motor: "tech",
        enabled: false,
      });
    }

    const cfg: TechEngineConfig = params.config ?? {
      targetUrl: "https://www.newegg.com/p/pl?d=RTX+5090",
      productName: "NVIDIA RTX 5090",
      maxPriceUsd: 2000,
    };

    const status = await this.engineStatusRepo.save({
      userId: params.userId,
      motor: "tech",
      enabled: true,
      startedAt: new Date().toISOString(),
      config: JSON.stringify(cfg),
    });

    void this.runScan(params.userId, cfg);
    this.clearTimer();
    this.timer = setInterval(() => {
      void this.runScan(params.userId, cfg);
    }, this.intervalMs);

    return status;
  }

  stop() {
    this.clearTimer();
  }

  private async runScan(userId: string, cfg: TechEngineConfig): Promise<void> {
    try {
      const firecrawlCred = await this.vault.getDecryptedProvider(userId, "firecrawl");
      const openrouterCred = await this.vault.getDecryptedProvider(userId, "openrouter");

      if (!firecrawlCred?.payload?.apiKey || !openrouterCred?.payload?.apiKey) {
        await this.engineStatusRepo.save({
          userId,
          motor: "tech",
          enabled: true,
          lastRunAt: new Date().toISOString(),
          lastError: "Faltan credenciales de Firecrawl o OpenRouter en la bóveda.",
        });
        return;
      }

      const pages = await this.scraper.crawl({
        url: cfg.targetUrl,
        apiKey: firecrawlCred.payload.apiKey,
        limit: 1,
      });

      const pageContent = pages[0]?.markdown ?? "";
      if (!pageContent) {
        await this.engineStatusRepo.save({
          userId,
          motor: "tech",
          enabled: true,
          lastRunAt: new Date().toISOString(),
          lastError: "Firecrawl no retornó contenido.",
        });
        return;
      }

      const llmResponse = await this.llm.chat({
        apiKey: openrouterCred.payload.apiKey,
        model: "groq/llama-3.3-70b-versatile",
        messages: [
          {
            role: "system",
            content:
              "Eres un asistente experto en mercado de hardware. Analiza listados de productos y extrae información de precios. Responde siempre en JSON válido.",
          },
          {
            role: "user",
            content: `Analiza el siguiente contenido de una tienda de hardware y busca el precio más bajo disponible para "${cfg.productName}".

Responde con este JSON exacto:
{
  "productFound": boolean,
  "lowestPriceUsd": number | null,
  "productTitle": string | null,
  "isGoodDeal": boolean,
  "reason": string
}

Si el precio más bajo es menor o igual a $${cfg.maxPriceUsd}, isGoodDeal debe ser true.

Contenido de la página:
---
${pageContent.slice(0, 4000)}
---`,
          },
        ],
        temperature: 0.1,
      });

      const analysis = parseJsonFromLLM<TechAnalysis>(llmResponse.content);
      const now = new Date().toISOString();

      if (analysis?.isGoodDeal && analysis.productFound) {
        await this.alertRepo.create({
          userId,
          motor: "tech",
          title: `🖥️ ¡Oportunidad! ${analysis.productTitle ?? cfg.productName}`,
          description: `Precio detectado: $${analysis.lowestPriceUsd}. Límite: $${cfg.maxPriceUsd}. ${analysis.reason}`,
          sourceUrl: cfg.targetUrl,
          severity: "high",
        });
      }

      await this.engineStatusRepo.save({
        userId,
        motor: "tech",
        enabled: true,
        lastRunAt: now,
        lastResult: JSON.stringify(analysis),
      });
    } catch (error) {
      await this.engineStatusRepo.save({
        userId,
        motor: "tech",
        enabled: true,
        lastRunAt: new Date().toISOString(),
        lastError: error instanceof Error ? error.message : "Error desconocido en Motor Tech.",
      });
    }
  }

  private clearTimer() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }
}

// ======================================================================
//  RealEstateEngineService — Sprint 4: Motor Inmobiliario
// ======================================================================

export type RealEstateEngineConfig = {
  targetUrl: string;
};

export class RealEstateEngineService {
  private timer: NodeJS.Timeout | null = null;
  private readonly intervalMs = 30 * 60 * 1000; // 30 minutos

  constructor(
    private readonly vault: CredentialVaultService,
    private readonly scraper: ScraperPort,
    private readonly llm: LLMPort,
    private readonly opportunityRepo: OpportunityRepository,
    private readonly engineStatusRepo: EngineStatusRepository,
  ) {}

  async toggle(params: {
    userId: string;
    enabled: boolean;
    config?: RealEstateEngineConfig;
  }): Promise<EngineStatusRecord> {
    if (!params.enabled) {
      this.stop();
      return this.engineStatusRepo.save({
        userId: params.userId,
        motor: "real-estate",
        enabled: false,
      });
    }

    const cfg: RealEstateEngineConfig = params.config ?? {
      targetUrl: "https://www.realtor.com/realestateandhomes-search/Detroit_MI/price-na-50000",
    };

    const status = await this.engineStatusRepo.save({
      userId: params.userId,
      motor: "real-estate",
      enabled: true,
      startedAt: new Date().toISOString(),
      config: JSON.stringify(cfg),
    });

    void this.runScan(params.userId, cfg);
    this.clearTimer();
    this.timer = setInterval(() => {
      void this.runScan(params.userId, cfg);
    }, this.intervalMs);

    return status;
  }

  stop() {
    this.clearTimer();
  }

  private async runScan(userId: string, cfg: RealEstateEngineConfig): Promise<void> {
    try {
      const firecrawlCred = await this.vault.getDecryptedProvider(userId, "firecrawl");
      const openrouterCred = await this.vault.getDecryptedProvider(userId, "openrouter");

      if (!firecrawlCred?.payload?.apiKey || !openrouterCred?.payload?.apiKey) {
        await this.engineStatusRepo.save({
          userId,
          motor: "real-estate",
          enabled: true,
          lastRunAt: new Date().toISOString(),
          lastError: "Faltan credenciales de Firecrawl o OpenRouter en la bóveda.",
        });
        return;
      }

      const pages = await this.scraper.crawl({
        url: cfg.targetUrl,
        apiKey: firecrawlCred.payload.apiKey,
        limit: 1,
      });

      const pageContent = pages[0]?.markdown ?? "";
      if (!pageContent) return;

      const llmResponse = await this.llm.chat({
        apiKey: openrouterCred.payload.apiKey,
        model: "openai/gpt-4o-mini",
        messages: [
          {
            role: "system",
            content:
              "Eres un experto en wholesaling inmobiliario. Aplicas la regla del 70% para evaluar propiedades. Responde siempre en JSON válido.",
          },
          {
            role: "user",
            content: `Analiza este listado de propiedades inmobiliarias y encuentra la mejor oportunidad de wholesaling.

Para cada propiedad interesante:
1. Aplica la regla del 70%: MAO = (ARV × 0.70) - Reparaciones Estimadas
2. Busca palabras clave de vendedores motivados: "urge vender", "motivated seller", "as-is", "fixer-upper", "cash only", "price reduced"
3. Asigna un dealScore del 1 al 10 (10 = oportunidad excepcional)

CRÍTICO: NO inventes ni simules propiedades. Si el contenido no muestra propiedades reales en venta, debes devolver "opportunityFound": false y dejar los demás campos vacíos o nulos. Es inaceptable devolver datos falsos, moks o simulados.

Responde con este JSON exacto para la MEJOR oportunidad encontrada:
{
  "opportunityFound": boolean,
  "title": string,
  "description": string,
  "estimatedARV": string,
  "estimatedRepair": string,
  "estimatedMAO": string,
  "listedPrice": string,
  "dealScore": number,
  "tags": string[],
  "analysis": string,
  "sourceUrl": string | null
}

Contenido del portal:
---
${pageContent.slice(0, 5000)}
---`,
          },
        ],
        temperature: 0.2,
      });

      const analysis = parseJsonFromLLM<RealEstateAnalysis>(llmResponse.content);

      if (analysis?.opportunityFound && (analysis.dealScore ?? 0) >= 5) {
        await this.opportunityRepo.create({
          userId,
          motor: "real-estate",
          title: analysis.title,
          description: analysis.description,
          sourceUrl: analysis.sourceUrl ?? cfg.targetUrl,
          aiAnalysis: analysis.analysis,
          estimatedValue: analysis.estimatedMAO,
          estimatedRepair: analysis.estimatedRepair,
          dealScore: analysis.dealScore,
          tags: analysis.tags ?? [],
        });
      }

      await this.engineStatusRepo.save({
        userId,
        motor: "real-estate",
        enabled: true,
        lastRunAt: new Date().toISOString(),
        lastResult: JSON.stringify({ dealScore: analysis?.dealScore, opportunityFound: analysis?.opportunityFound }),
      });
    } catch (error) {
      await this.engineStatusRepo.save({
        userId,
        motor: "real-estate",
        enabled: true,
        lastRunAt: new Date().toISOString(),
        lastError: error instanceof Error ? error.message : "Error en Motor Inmobiliario.",
      });
    }
  }

  private clearTimer() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }
}

// ======================================================================
//  SaasEngineService — Sprint 4: Motor Micro-SaaS
// ======================================================================

export type SaasEngineConfig = {
  targetUrl: string;
};

export class SaasEngineService {
  private timer: NodeJS.Timeout | null = null;
  private readonly intervalMs = 60 * 60 * 1000; // 1 hora

  constructor(
    private readonly vault: CredentialVaultService,
    private readonly scraper: ScraperPort,
    private readonly llm: LLMPort,
    private readonly opportunityRepo: OpportunityRepository,
    private readonly engineStatusRepo: EngineStatusRepository,
  ) {}

  async toggle(params: {
    userId: string;
    enabled: boolean;
    config?: SaasEngineConfig;
  }): Promise<EngineStatusRecord> {
    if (!params.enabled) {
      this.stop();
      return this.engineStatusRepo.save({
        userId: params.userId,
        motor: "saas",
        enabled: false,
      });
    }

    const cfg: SaasEngineConfig = params.config ?? {
      targetUrl: "https://acquire.com/marketplace/saas",
    };

    const status = await this.engineStatusRepo.save({
      userId: params.userId,
      motor: "saas",
      enabled: true,
      startedAt: new Date().toISOString(),
      config: JSON.stringify(cfg),
    });

    void this.runScan(params.userId, cfg);
    this.clearTimer();
    this.timer = setInterval(() => {
      void this.runScan(params.userId, cfg);
    }, this.intervalMs);

    return status;
  }

  stop() {
    this.clearTimer();
  }

  private async runScan(userId: string, cfg: SaasEngineConfig): Promise<void> {
    try {
      const firecrawlCred = await this.vault.getDecryptedProvider(userId, "firecrawl");
      const openrouterCred = await this.vault.getDecryptedProvider(userId, "openrouter");

      if (!firecrawlCred?.payload?.apiKey || !openrouterCred?.payload?.apiKey) {
        await this.engineStatusRepo.save({
          userId,
          motor: "saas",
          enabled: true,
          lastRunAt: new Date().toISOString(),
          lastError: "Faltan credenciales de Firecrawl o OpenRouter en la bóveda.",
        });
        return;
      }

      const pages = await this.scraper.crawl({
        url: cfg.targetUrl,
        apiKey: firecrawlCred.payload.apiKey,
        limit: 1,
      });

      const pageContent = pages[0]?.markdown ?? "";
      if (!pageContent) return;

      const llmResponse = await this.llm.chat({
        apiKey: openrouterCred.payload.apiKey,
        model: "openai/gpt-4o-mini",
        messages: [
          {
            role: "system",
            content:
              "Eres un experto en adquisición de negocios digitales y Micro-SaaS. Evalúas oportunidades de compra basándote en múltiplos de ingresos, potencial técnico y subvaluación. Responde siempre en JSON válido.",
          },
          {
            role: "user",
            content: `Analiza estos listados de negocios digitales/SaaS en venta y encuentra la mejor oportunidad de adquisición.

Criterios de evaluación:
1. Múltiplo de ingresos razonable (< 3x ARR para micro-SaaS es bueno)
2. Stack tecnológico escalable o mejorable
3. Revenue verificable o indicios claros
4. Potencial de crecimiento técnico (sin marketing agresivo)
5. Señales de subvaluación (fundador cansado, negocio descuidado, etc.)

CRÍTICO: NO inventes ni simules negocios. Si el contenido no muestra listados reales en venta, debes devolver "opportunityFound": false y dejar los demás campos vacíos o nulos. Es inaceptable devolver datos falsos, moks o simulados.

Responde con este JSON exacto para la MEJOR oportunidad encontrada:
{
  "opportunityFound": boolean,
  "title": string,
  "description": string,
  "askingPrice": string,
  "monthlyRevenue": string,
  "annualRevenue": string,
  "revenueMultiple": string,
  "techStack": string[],
  "dealScore": number,
  "tags": string[],
  "analysis": string,
  "growthPotential": string,
  "sourceUrl": string | null
}

dealScore del 1-10 donde 10 = oportunidad excepcional.

Contenido del marketplace:
---
${pageContent.slice(0, 5000)}
---`,
          },
        ],
        temperature: 0.2,
      });

      const analysis = parseJsonFromLLM<SaasAnalysis>(llmResponse.content);

      if (analysis?.opportunityFound && (analysis.dealScore ?? 0) >= 6) {
        await this.opportunityRepo.create({
          userId,
          motor: "saas",
          title: analysis.title,
          description: `${analysis.description}\n\nIngresos: ${analysis.monthlyRevenue} / mes (${analysis.annualRevenue} / año)\nMúltiplo: ${analysis.revenueMultiple}\n\nStack: ${(analysis.techStack ?? []).join(", ")}\nPotencial: ${analysis.growthPotential}`,
          sourceUrl: analysis.sourceUrl ?? cfg.targetUrl,
          aiAnalysis: analysis.analysis,
          estimatedValue: analysis.askingPrice,
          dealScore: analysis.dealScore,
          tags: [...(analysis.tags ?? []), ...(analysis.techStack ?? []).slice(0, 3)],
        });
      }

      await this.engineStatusRepo.save({
        userId,
        motor: "saas",
        enabled: true,
        lastRunAt: new Date().toISOString(),
        lastResult: JSON.stringify({ dealScore: analysis?.dealScore, opportunityFound: analysis?.opportunityFound }),
      });
    } catch (error) {
      await this.engineStatusRepo.save({
        userId,
        motor: "saas",
        enabled: true,
        lastRunAt: new Date().toISOString(),
        lastError: error instanceof Error ? error.message : "Error en Motor SaaS.",
      });
    }
  }

  private clearTimer() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }
}
