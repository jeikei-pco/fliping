import express from "express";
import cors from "cors";
import { z } from "zod";
export const createHttpApp = (services) => {
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
    // Auth (mock)
    // ──────────────────────────────────────────────
    app.post("/api/auth/login", (request, response) => {
        const schema = z.object({
            email: z.string().email(),
            password: z.string().min(4),
        });
        const parsed = schema.parse(request.body);
        response.json({
            token: `demo-token-${parsed.email}`,
            user: {
                id: services.defaultUserId,
                email: parsed.email,
                displayName: "JK Operator",
            },
        });
    });
    // ──────────────────────────────────────────────
    // Credential Vault
    // ──────────────────────────────────────────────
    app.get("/api/keys", async (request, response) => {
        const userId = String(request.query.userId ?? services.defaultUserId);
        const credentials = await services.vault.list(userId);
        response.json({ credentials });
    });
    app.post("/api/keys", async (request, response) => {
        const saved = await services.vault.save({
            ...request.body,
            userId: request.body.userId ?? services.defaultUserId,
        });
        response.status(201).json({
            id: saved.id,
            provider: saved.provider,
            label: saved.label,
            sandbox: saved.sandbox,
            updatedAt: saved.updatedAt,
        });
    });
    // ──────────────────────────────────────────────
    // Balances
    // ──────────────────────────────────────────────
    app.get("/api/balances", async (request, response) => {
        const userId = String(request.query.userId ?? services.defaultUserId);
        const exchange = String(request.query.exchange ?? "okx");
        const sandbox = String(request.query.sandbox ?? "true") === "true";
        const balances = await services.balance.getBalances(userId, exchange, sandbox);
        response.json(balances);
    });
    // ──────────────────────────────────────────────
    // Motor Cripto (Sprint 2)
    // ──────────────────────────────────────────────
    app.get("/api/engine/status", (_request, response) => {
        response.json(services.cryptoEngine.getStatus());
    });
    app.post("/api/engine/toggle", async (request, response) => {
        const status = await services.cryptoEngine.toggle({
            ...request.body,
            userId: request.body.userId ?? services.defaultUserId,
        });
        response.json(status);
    });
    // ──────────────────────────────────────────────
    // Alertas (Sprint 3)
    // ──────────────────────────────────────────────
    app.get("/api/alerts", async (request, response) => {
        const userId = String(request.query.userId ?? services.defaultUserId);
        const motor = request.query.motor;
        const alerts = await services.alertService.listAlerts(userId, motor);
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
        const userId = String(request.query.userId ?? services.defaultUserId);
        const motor = request.query.motor;
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
        const userId = String(request.query.userId ?? services.defaultUserId);
        const motors = ["tech", "real-estate", "saas"];
        const statuses = await Promise.all(motors.map(async (motor) => {
            const record = await services.engineManager.getStatus(userId, motor);
            return {
                motor,
                enabled: record?.enabled ?? false,
                startedAt: record?.startedAt ?? null,
                lastRunAt: record?.lastRunAt ?? null,
                lastResult: record?.lastResult ? JSON.parse(record.lastResult) : null,
                lastError: record?.lastError ?? null,
            };
        }));
        response.json({ engines: statuses });
    });
    app.post("/api/engines/toggle", async (request, response) => {
        const schema = z.object({
            motor: z.enum(["tech", "real-estate", "saas"]),
            enabled: z.boolean(),
            userId: z.string().optional(),
        });
        const input = schema.parse(request.body);
        const userId = input.userId ?? services.defaultUserId;
        let status;
        if (input.motor === "tech") {
            status = await services.techEngine.toggle({ userId, enabled: input.enabled });
        }
        else if (input.motor === "real-estate") {
            status = await services.realEstateEngine.toggle({ userId, enabled: input.enabled });
        }
        else {
            status = await services.saasEngine.toggle({ userId, enabled: input.enabled });
        }
        response.json(status);
    });
    // ──────────────────────────────────────────────
    // Error handler global
    // ──────────────────────────────────────────────
    app.use((error, _request, response, _next) => {
        const message = error instanceof Error ? error.message : "Error inesperado";
        response.status(400).json({ error: message });
    });
    return app;
};
