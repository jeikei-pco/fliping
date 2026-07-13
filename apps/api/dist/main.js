import dotenv from "dotenv";
import { AlertService, EngineManagerService, OpportunityService, RealEstateEngineService, SaasEngineService, TechEngineService, } from "./application/flipping-engines.js";
import { BalanceService, CredentialVaultService, CryptoEngineService } from "./application/use-cases.js";
import { PrismaAlertRepository } from "./infrastructure/repositories/prisma-alert-repository.js";
import { PrismaCredentialRepository } from "./infrastructure/repositories/prisma-credential-repository.js";
import { PrismaEngineStatusRepository } from "./infrastructure/repositories/prisma-engine-status-repository.js";
import { PrismaOpportunityRepository } from "./infrastructure/repositories/prisma-opportunity-repository.js";
import { AesEncryptionService } from "./infrastructure/services/aes-encryption-service.js";
import { CcxtExchangeService } from "./infrastructure/services/ccxt-exchange-service.js";
import { DemoAwareCryptoEngineService } from "./infrastructure/services/crypto-engine-service.js";
import { OpenRouterLLMService } from "./infrastructure/services/llm-service.js";
import { getPrismaClient } from "./infrastructure/services/prisma-client-service.js";
import { FirecrawlScraperService } from "./infrastructure/services/scraper-service.js";
import { createHttpApp } from "./presentation/http-app.js";
dotenv.config();
const port = Number(process.env.PORT ?? 4000);
const appOrigin = process.env.APP_ORIGIN ?? "*";
const masterKey = process.env.MASTER_KEY ?? "jk-flipping-local-master-key";
const defaultUserId = process.env.DEFAULT_USER_ID ?? "demo-user";
const prisma = getPrismaClient();
// ── Repositorios ────────────────────────────────────────────────────────
const credentialRepository = new PrismaCredentialRepository(prisma);
const alertRepository = new PrismaAlertRepository(prisma);
const opportunityRepository = new PrismaOpportunityRepository(prisma);
const engineStatusRepository = new PrismaEngineStatusRepository(prisma);
// ── Servicios de infraestructura ────────────────────────────────────────
const encryption = new AesEncryptionService(masterKey);
const exchange = new CcxtExchangeService();
const scraper = new FirecrawlScraperService();
const llm = new OpenRouterLLMService();
// ── Casos de uso (Sprint 1 & 2) ─────────────────────────────────────────
const vault = new CredentialVaultService(credentialRepository, encryption);
const balance = new BalanceService(vault, exchange);
const cryptoEngineAdapter = new DemoAwareCryptoEngineService(exchange);
const cryptoEngine = new CryptoEngineService(vault, cryptoEngineAdapter);
// ── Casos de uso (Sprint 3 & 4) ─────────────────────────────────────────
const alertService = new AlertService(alertRepository);
const opportunityService = new OpportunityService(opportunityRepository);
const engineManager = new EngineManagerService(engineStatusRepository);
const techEngine = new TechEngineService(vault, scraper, llm, alertRepository, engineStatusRepository);
const realEstateEngine = new RealEstateEngineService(vault, scraper, llm, opportunityRepository, engineStatusRepository);
const saasEngine = new SaasEngineService(vault, scraper, llm, opportunityRepository, engineStatusRepository);
// ── HTTP App ─────────────────────────────────────────────────────────────
const app = createHttpApp({
    vault,
    balance,
    cryptoEngine,
    alertService,
    opportunityService,
    engineManager,
    techEngine,
    realEstateEngine,
    saasEngine,
    defaultUserId,
    appOrigin,
});
// ── Bootstrap ─────────────────────────────────────────────────────────────
const bootstrap = async () => {
    await prisma.$connect();
    // Restaurar motores que estaban activos al momento del reinicio del servidor
    const motors = ["tech", "real-estate", "saas"];
    for (const motor of motors) {
        const storedStatus = await engineStatusRepository.findByUserAndMotor(defaultUserId, motor);
        if (storedStatus?.enabled) {
            console.log(`[Bootstrap] Restaurando Motor ${motor} para usuario ${defaultUserId}...`);
            if (motor === "tech") {
                void techEngine.toggle({ userId: defaultUserId, enabled: true });
            }
            else if (motor === "real-estate") {
                void realEstateEngine.toggle({ userId: defaultUserId, enabled: true });
            }
            else {
                void saasEngine.toggle({ userId: defaultUserId, enabled: true });
            }
        }
    }
    app.listen(port, () => {
        console.log(`JK-Flipping API escuchando en http://localhost:${port}`);
        console.log(`  Sprints 3 & 4 activados: Motor Tech, Inmobiliario, Micro-SaaS`);
    });
};
void bootstrap();
