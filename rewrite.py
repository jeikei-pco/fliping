import sys
import re

with open('apps/api/src/presentation/http-app.ts', 'r', encoding='utf-8') as f:
    content = f.read()

import_str = '''import express from "express";
import cors from "cors";
import { z } from "zod";
import jwt from "jsonwebtoken";
import bcrypt from "bcryptjs";
import type { PrismaClient } from "@prisma/client";'''

content = content.replace('import express from "express";\nimport cors from "cors";\nimport { z } from "zod";', import_str)

services_old = '''export const createHttpApp = (services: {
  vault: CredentialVaultService;
  balance: BalanceService;
  cryptoEngine: CryptoEngineService;
  alertService: AlertService;
  opportunityService: OpportunityService;
  engineManager: EngineManagerService;
  techEngine: TechEngineService;
  realEstateEngine: RealEstateEngineService;
  saasEngine: SaasEngineService;
  defaultUserId: string;
  appOrigin: string;
}) => {'''

services_new = '''const JWT_SECRET = process.env.JWT_SECRET || "super-secret-key-change-me-in-prod";

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
}) => {'''

content = content.replace(services_old, services_new)

auth_mock_old = '''  // ──────────────────────────────────────────────
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
  });'''

auth_new = '''  // ──────────────────────────────────────────────
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
  });'''

content = content.replace(auth_mock_old, auth_new)

content = content.replace('services.defaultUserId', '(request as any).user.id')
content = content.replace('request.query.userId ?? ', '')
content = content.replace('request.body.userId ?? ', '')

with open('apps/api/src/presentation/http-app.ts', 'w', encoding='utf-8') as f:
    f.write(content)
