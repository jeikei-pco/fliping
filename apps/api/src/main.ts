import dotenv from "dotenv";

import {
  AlertService,
  EngineManagerService,
  OpportunityService,
  RealEstateEngineService,
  SaasEngineService,
  TechEngineService,
} from "./application/flipping-engines.js";
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

const techEngine = new TechEngineService(
  vault,
  scraper,
  llm,
  alertRepository,
  engineStatusRepository,
);

const realEstateEngine = new RealEstateEngineService(
  vault,
  scraper,
  llm,
  opportunityRepository,
  engineStatusRepository,
);

const saasEngine = new SaasEngineService(
  vault,
  scraper,
  llm,
  opportunityRepository,
  engineStatusRepository,
);

// ── Bootstrap ─────────────────────────────────────────────────────────────
const bootstrap = async () => {
  await prisma.$connect();

  // Buscar el usuario más reciente logueado/creado en el sistema
  const latestUser = await prisma.appUser.findFirst({
    orderBy: { updatedAt: "desc" }
  });
  
  // Usar el usuario logueado en la app móvil, o caer al fallback de .env
  const dynamicDefaultUserId = latestUser ? latestUser.id : (process.env.DEFAULT_USER_ID ?? "demo-user");
  console.log(`[Bootstrap] Usuario principal del sistema establecido a: ${dynamicDefaultUserId}`);

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
    defaultUserId: dynamicDefaultUserId,
    appOrigin,
  });

  // Restaurar motores que estaban activos al momento del reinicio del servidor
  console.log(`[Bootstrap] Buscando motores activos en la base de datos...`);
  
  const activeTechEngines = await prisma.engineStatus.findMany({ where: { motor: "tech", enabled: true } });
  for (const status of activeTechEngines) {
    console.log(`[Bootstrap] Restaurando Motor tech para usuario ${status.userId}...`);
    void techEngine.toggle({ userId: status.userId, enabled: true });
  }

  const activeSaasEngines = await prisma.engineStatus.findMany({ where: { motor: "saas", enabled: true } });
  for (const status of activeSaasEngines) {
    console.log(`[Bootstrap] Restaurando Motor saas para usuario ${status.userId}...`);
    void saasEngine.toggle({ userId: status.userId, enabled: true });
  }

  const activeRealEstateEngines = await prisma.engineStatus.findMany({ where: { motor: "real-estate", enabled: true } });
  for (const status of activeRealEstateEngines) {
    console.log(`[Bootstrap] Restaurando Motor inmobiliario para usuario ${status.userId}...`);
    void realEstateEngine.toggle({ userId: status.userId, enabled: true });
  }

  const { spawnPythonWorker, stopPythonWorker } = await import("./infrastructure/workers/python-spawner.js");
  spawnPythonWorker();

  // Si el motor grid está activo para algún usuario, enviar credenciales
  let activeGridEngines = await prisma.engineStatus.findMany({ where: { motor: "grid", enabled: true } });
  
  // FALLBACK: Si no hay usuarios en DB con el grid activo, usar el dynamicDefaultUserId
  if (activeGridEngines.length === 0) {
    const defaultGridStatus = await prisma.engineStatus.findUnique({
      where: { userId_motor: { userId: dynamicDefaultUserId, motor: "grid" } }
    });
    // Si no existe registro o si existe y está enabled, lo forzamos a iniciar (fallback)
    if (!defaultGridStatus || defaultGridStatus.enabled) {
      activeGridEngines = [{ userId: dynamicDefaultUserId, motor: "grid", enabled: true } as any];
    }
  }

  if (activeGridEngines.length > 0) {
    setTimeout(async () => {
      for (const status of activeGridEngines) {
        try {
          const provider = await vault.getDecryptedProvider(status.userId, "okx");
          if (provider) {
            console.log(`[Bootstrap] Motor Grid Activo para ${status.userId} (Sandbox: ${provider.sandbox}): Enviando auto_start...`);
            const { dispatchGridEngine } = await import("./infrastructure/workers/grid-queue.js");
            await dispatchGridEngine("auto_start", {
              apiKey: provider.payload.apiKey,
              secret: provider.payload.secret,
              passphrase: provider.payload.passphrase,
              sandbox: provider.sandbox,
            });
          } else {
            console.log(`[Bootstrap] Motor Grid Activo para ${status.userId} pero NO hay credenciales OKX en la Bóveda.`);
          }
        } catch (err) {
          console.error(`[Bootstrap] Error enviando auto_start para ${status.userId}:`, err);
        }
      }
    }, 3000); // Dar 3 segundos para que Python levante BullMQ
  }

  const server = app.listen(port, () => {
    console.log(`JK-Flipping API escuchando en http://localhost:${port}`);
    console.log(`  Sprints 3 & 4 activados: Motor Tech, Inmobiliario, Micro-SaaS`);
    console.log(`  Worker de Python: Gestionado por Node`);
  });

  process.on("SIGTERM", () => {
    stopPythonWorker();
    server.close();
  });
  
  process.on("SIGINT", () => {
    stopPythonWorker();
    server.close();
  });
};

void bootstrap();
