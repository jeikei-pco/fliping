import express from "express";
import cors from "cors";
import { z } from "zod";
import jwt from "jsonwebtoken";
import bcrypt from "bcryptjs";
import type { PrismaClient } from "@prisma/client";

import {
  AlertService,
  EngineManagerService,
  OpportunityService,
  RealEstateEngineService,
  SaasEngineService,
  TechEngineService,
} from "../application/flipping-engines.js";
import { BalanceService, CredentialVaultService, CryptoEngineService } from "../application/use-cases.js";

/** Muestra los primeros 4 caracteres y enmascara el resto */
const maskValue = (value: string): string => {
  if (value.length <= 4) return "••••";
  return value.slice(0, 4) + "••••••••";
};

const JWT_SECRET = process.env.JWT_SECRET || "super-secret-key-change-me-in-prod";

export const createHttpApp = (services: {
  vault: CredentialVaultService;
  balance: BalanceService;
  cryptoEngine: CryptoEngineService;
  alertService: AlertService;
  opportunityService: OpportunityService;
  engineManager: EngineManagerService;
  techEngine: TechEngineService;
  realEstateEngine: RealEstateEngineService;
  saasEngine: SaasEngineService;
  prisma: PrismaClient;
  appOrigin: string;
}) => {
  const app = express();
  app.use(cors({ origin: services.appOrigin === "*" ? true : services.appOrigin }));
  app.use(express.json());

  // ──────────────────────────────────────────────
  // Health
  // ──────────────────────────────────────────────
  app.get("/api/health", (_request, response) => {
    response.json({
      status: "ok",
      service: "jk-flipping-api",
      timestamp: new Date().toISOString(),
    });
  });

  // ──────────────────────────────────────────────
  // Auth
  // ──────────────────────────────────────────────
  app.post("/api/auth/register", async (request, response) => {
    try {
      const schema = z.object({
        email: z.string().email(),
        password: z.string().min(6),
        displayName: z.string().min(2),
      });

      const { email, password, displayName } = schema.parse(request.body);
      
      const existingUser = await services.prisma.appUser.findUnique({ where: { email } });
      if (existingUser) {
        response.status(400).json({ error: "El correo ya está en uso" });
        return;
      }

      const passwordHash = await bcrypt.hash(password, 10);
      const user = await services.prisma.appUser.create({
        data: {
          id: Math.random().toString(36).substring(2, 15),
          email,
          displayName,
          passwordHash,
        }
      });

      const token = jwt.sign({ id: user.id }, JWT_SECRET, { expiresIn: "7d" });
      response.json({ token, user: { id: user.id, email: user.email, displayName: user.displayName } });
    } catch (err: any) {
      response.status(400).json({ error: err.message });
    }
  });

  app.post("/api/auth/login", async (request, response) => {
    try {
      const schema = z.object({
        email: z.string().email(),
        password: z.string(),
      });

      const { email, password } = schema.parse(request.body);
      const user = await services.prisma.appUser.findUnique({ where: { email } });
      if (!user) {
        response.status(401).json({ error: "Credenciales inválidas" });
        return;
      }

      const valid = await bcrypt.compare(password, user.passwordHash as string);
      if (!valid) {
        response.status(401).json({ error: "Credenciales inválidas" });
        return;
      }

      const token = jwt.sign({ id: user.id }, JWT_SECRET, { expiresIn: "7d" });
      response.json({ token, user: { id: user.id, email: user.email, displayName: user.displayName } });
    } catch (err: any) {
      response.status(400).json({ error: err.message });
    }
  });

  app.use((request, response, next) => {
    if (request.path.startsWith("/api/auth/") || request.path === "/api/health") {
      return next();
    }
    const authHeader = request.headers.authorization;
    if (!authHeader || !authHeader.startsWith("Bearer ")) {
      response.status(401).json({ error: "No autorizado" });
      return;
    }
    const token = authHeader.split(" ")[1];
    try {
      const decoded = jwt.verify(token, JWT_SECRET) as { id: string };
      (request as any).user = { id: decoded.id };
      next();
    } catch (err) {
      response.status(401).json({ error: "Token inválido o expirado" });
    }
  });

  // ──────────────────────────────────────────────
  // Credential Vault
  // ──────────────────────────────────────────────
  app.get("/api/keys", async (request, response) => {
    const userId = String((request as any).user.id);
    const credentials = await services.vault.list(userId);
    response.json({ credentials });
  });

  app.post("/api/keys", async (request, response) => {
    const saved = await services.vault.save({
      ...request.body,
      userId: (request as any).user.id,
    });

    response.status(201).json({
      id: saved.id,
      provider: saved.provider,
      label: saved.label,
      sandbox: saved.sandbox,
      updatedAt: saved.updatedAt,
    });
  });

  // Devuelve qué credenciales están configuradas (con valores enmascarados)
  app.get("/api/keys/status", async (request, response) => {
    const userId = String((request as any).user.id);
    const providers = ["okx", "openrouter", "firecrawl"] as const;

    const statuses = await Promise.all(
      providers.map(async (provider) => {
        const record = await services.vault.getDecryptedProvider(userId, provider);
        if (!record) {
          return { provider, configured: false };
        }
        const payload = record.payload;
        return {
          provider,
          configured: true,
          label: record.label,
          sandbox: record.sandbox,
          maskedApiKey: maskValue(payload.apiKey),
          maskedSecret: payload.secret ? maskValue(payload.secret) : undefined,
          hasPassphrase: !!payload.passphrase,
        };
      }),
    );

    response.json({ providers: statuses });
  });

  // ──────────────────────────────────────────────
  // Balances
  // ──────────────────────────────────────────────
  app.get("/api/balances", async (request, response) => {
    const userId = String((request as any).user.id);
    const exchange = String(request.query.exchange ?? "okx");
    const sandbox = String(request.query.sandbox ?? "true") === "true";
    const balances = await services.balance.getBalances(userId, exchange, sandbox);
    response.json(balances);
  });

  // ──────────────────────────────────────────────
  // Motor Cripto (Sprint 2) / Grid Worker (Sprint 1)
  // 
  app.get("/api/engine/status", (_request, response) => {
    response.json(services.cryptoEngine.getStatus());
  });

  app.post("/api/engine/toggle", async (request, response) => {
    const status = await services.cryptoEngine.toggle({
      ...request.body,
      userId: (request as any).user.id,
    });

    response.json(status);
  });

  app.post("/api/grid/ping", async (_request, response) => {
    try {
      const { pingGridWorker } = await import("../infrastructure/workers/grid-queue.js");
      const result = await pingGridWorker();
      response.json({ success: true, result });
    } catch (error: any) {
      response.status(503).json({ success: false, error: error.message });
    }
  });

  app.post("/api/grid/start", async (request, response) => {
    try {
      const userId = (request as any).user.id;
      const okxCreds = await services.vault.getDecryptedProvider(userId, "okx");
      if (!okxCreds) {
        response.status(400).json({ success: false, error: "Faltan credenciales de OKX en la bóveda." });
        return;
      }
      
      const { dispatchGridEngine } = await import("../infrastructure/workers/grid-queue.js");
      const result = await dispatchGridEngine("start", { 
        apiKey: okxCreds.payload.apiKey,
        secret: okxCreds.payload.secret,
        passphrase: okxCreds.payload.passphrase,
        sandbox: okxCreds.sandbox
      });
      response.json({ success: true, result });
    } catch (error: any) {
      response.status(500).json({ success: false, error: error.message });
    }
  });

  app.post("/api/grid/stop", async (_request, response) => {
    try {
      const { dispatchGridEngine } = await import("../infrastructure/workers/grid-queue.js");
      const result = await dispatchGridEngine("stop");
      response.json({ success: true, result });
    } catch (error: any) {
      response.status(500).json({ success: false, error: error.message });
    }
  });

  app.post("/api/grid/scan", async (request, response) => {
    try {
      const { sandbox } = request.body as { sandbox?: boolean };
      
      const userId = (request as any).user.id;
      const provider = await services.vault.getDecryptedProvider(userId, "okx");
      if (!provider) {
        response.status(400).json({ success: false, error: "Credenciales de OKX no encontradas" });
        return;
      }

      const { dispatchGridScan } = await import("../infrastructure/workers/grid-queue.js");
      const result = await dispatchGridScan({
        apiKey: provider.payload.apiKey,
        secret: provider.payload.secret,
        passphrase: provider.payload.passphrase,
        sandbox: sandbox ?? true,
      });

      response.json({ success: true, result });
    } catch (error: any) {
      response.status(500).json({ success: false, error: error.message });
    }
  });

  app.get("/api/grid/metrics", async (_request, response) => {
    try {
      const { gridRedisConnection } = await import("../infrastructure/workers/grid-queue.js");
      const data = await gridRedisConnection.get("grid:metrics");
      const logs = await gridRedisConnection.lrange("grid:logs", 0, -1);
      const top10Raw = await gridRedisConnection.get("grid:backtest_top10");
      const top3Raw = await gridRedisConnection.get("grid:backtest_top3");
      const top20Raw = await gridRedisConnection.get("grid:top20");
      
      if (!data) {
        response.json({ status: "Offline", task: "Offline", logs: logs || [], backtest_top10: [], top20: [] });
        return;
      }
      
      const parsedData = JSON.parse(data);
      parsedData.logs = logs || [];
      parsedData.backtest_top10 = top10Raw ? JSON.parse(top10Raw) : (top3Raw ? JSON.parse(top3Raw) : []);
      parsedData.top20 = top20Raw ? JSON.parse(top20Raw) : [];
      response.json(parsedData);
    } catch (error: any) {
      response.status(500).json({ success: false, error: error.message });
    }
  });

  app.post("/api/grid/config", async (request, response) => {
    try {
      const { gridBaseCapital, gridMaxLeverage } = request.body;
      const { gridRedisConnection } = await import("../infrastructure/workers/grid-queue.js");
      await gridRedisConnection.set("grid:config", JSON.stringify({
        baseCapital: gridBaseCapital,
        maxLeverage: gridMaxLeverage
      }));
      response.json({ success: true });
    } catch (error: any) {
      response.status(500).json({ success: false, error: error.message });
    }
  });

  // ──────────────────────────────────────────────
  // Alertas (Sprint 3)
  // ──────────────────────────────────────────────
  app.get("/api/alerts", async (request, response) => {
    const userId = String((request as any).user.id);
    const motor = request.query.motor as string | undefined;
    const alerts = await services.alertService.listAlerts(
      userId,
      motor as Parameters<typeof services.alertService.listAlerts>[1],
    );
    response.json({ alerts });
  });

  app.post("/api/alerts/:id/read", async (request, response) => {
    await services.alertService.markAsRead(request.params.id);
    response.json({ success: true });
  });

  // ──────────────────────────────────────────────
  // Oportunidades (Sprint 4)
  // ──────────────────────────────────────────────
  app.get("/api/opportunities", async (request, response) => {
    const userId = String((request as any).user.id);
    const motor = request.query.motor as "real-estate" | "saas" | undefined;
    const opportunities = await services.opportunityService.listOpportunities(userId, motor);
    response.json({ opportunities });
  });

  app.get("/api/opportunities/:id", async (request, response) => {
    const opportunity = await services.opportunityService.getOpportunity(request.params.id);
    if (!opportunity) {
      response.status(404).json({ error: "Oportunidad no encontrada." });
      return;
    }
    response.json(opportunity);
  });

  app.post("/api/opportunities/:id/status", async (request, response) => {
    const schema = z.object({
      status: z.enum(["reviewed", "archived"]),
    });
    const { status } = schema.parse(request.body);
    await services.opportunityService.updateStatus(request.params.id, status);
    response.json({ success: true });
  });

  // ──────────────────────────────────────────────
  // Motores Sprints 3 & 4 — toggle y estado
  // ──────────────────────────────────────────────
    app.get("/api/engines", async (request, response) => {
      const userId = String((request as any).user.id);
      const motors = ["tech", "real-estate", "saas", "grid"] as const;
  
      const statuses = await Promise.all(
        motors.map(async (motor) => {
          const record = await services.engineManager.getStatus(userId, motor);
          return {
            motor,
            // Grid is enabled by default if no record exists
            enabled: record ? record.enabled : (motor === "grid"),
            startedAt: record?.startedAt ?? null,
            lastRunAt: record?.lastRunAt ?? null,
            lastResult: record?.lastResult ? (JSON.parse(record.lastResult) as unknown) : null,
            lastError: record?.lastError ?? null,
          };
        }),
      );
  
      response.json({ engines: statuses });
    });
  
    app.post("/api/engines/toggle", async (request, response) => {
      const schema = z.object({
        motor: z.enum(["tech", "real-estate", "saas", "grid"]),
        enabled: z.boolean(),
        userId: z.string().optional(),
      });
  
      const input = schema.parse(request.body);
      const userId = input.userId ?? (request as any).user.id;
  
      let status;
  
      if (input.motor === "tech") {
        status = await services.techEngine.toggle({ userId, enabled: input.enabled });
      } else if (input.motor === "real-estate") {
        status = await services.realEstateEngine.toggle({ userId, enabled: input.enabled });
      } else if (input.motor === "saas") {
        status = await services.saasEngine.toggle({ userId, enabled: input.enabled });
      } else if (input.motor === "grid") {
        status = await services.engineManager.setEnabled(userId, "grid", input.enabled);

        const { dispatchGridEngine } = await import("../infrastructure/workers/grid-queue.js");
        if (input.enabled) {
          const provider = await services.vault.getDecryptedProvider(userId, "okx");
          if (provider) {
            await dispatchGridEngine("auto_start", {
              apiKey: provider.payload.apiKey,
              secret: provider.payload.secret,
              passphrase: provider.payload.passphrase,
              sandbox: provider.sandbox,
            });
          }
        } else {
          await dispatchGridEngine("stop", {});
        }
      }
  
      response.json(status);
    });

  // ──────────────────────────────────────────────
  // Error handler global
  // ──────────────────────────────────────────────
  app.use((error: unknown, _request: express.Request, response: express.Response, _next: express.NextFunction) => {
    const message = error instanceof Error ? error.message : "Error inesperado";
    response.status(400).json({ error: message });
  });

  return app;
};
