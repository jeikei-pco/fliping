import { randomUUID } from "node:crypto";

import { z } from "zod";

import type { CryptoEnginePort, CredentialRepository, EncryptionPort, ExchangePort } from "../domain/ports.js";
import type { CredentialPayload, EngineState, ExchangeBalances, ProviderKind, SaveCredentialInput } from "../domain/model.js";

const saveCredentialSchema = z.object({
  userId: z.string().default("demo-user"),
  providerKind: z.enum(["exchange", "ai", "scraper"]),
  provider: z.string().min(2),
  label: z.string().min(2),
  sandbox: z.boolean().optional(),
  payload: z.object({
    apiKey: z.string().min(1),
    secret: z.string().optional(),
    passphrase: z.string().optional(),
    baseUrl: z.string().url().optional(),
  }),
});

const toggleEngineSchema = z.object({
  userId: z.string().default("demo-user"),
  enabled: z.boolean(),
  exchange: z.string().default("okx"),
  symbol: z.string().default("BTC/USDT"),
  sandbox: z.boolean().default(true),
});

export class CredentialVaultService {
  constructor(
    private readonly repository: CredentialRepository,
    private readonly encryption: EncryptionPort,
  ) {}

  async save(rawInput: unknown) {
    const input = saveCredentialSchema.parse(rawInput);
    const encryptedPayload = this.encryption.encrypt(JSON.stringify(input.payload));
    return this.repository.save(input, encryptedPayload);
  }

  async list(userId: string) {
    const records = await this.repository.listByUser(userId);
    return records.map((record) => ({
      id: record.id,
      providerKind: record.providerKind,
      provider: record.provider,
      label: record.label,
      sandbox: record.sandbox,
      hasSecret: true,
      updatedAt: record.updatedAt,
    }));
  }

  async getDecryptedProvider(userId: string, provider: string, sandbox?: boolean) {
    const record = await this.repository.findByProvider(userId, provider, sandbox);
    if (!record) {
      return null;
    }

    const decrypted = this.encryption.decrypt(record.encryptedPayload);
    return {
      ...record,
      payload: JSON.parse(decrypted) as CredentialPayload,
    };
  }
}

export class BalanceService {
  constructor(
    private readonly vault: CredentialVaultService,
    private readonly exchange: ExchangePort,
  ) {}

  async getBalances(userId: string, provider: string, sandbox = true): Promise<ExchangeBalances> {
    const stored = await this.vault.getDecryptedProvider(userId, provider);

    // Si tenemos credenciales guardadas, usamos su configuración de sandbox por defecto
    const effectiveSandbox = stored ? (stored.sandbox ?? sandbox) : sandbox;

    if (!stored) {
      return {
        exchange: provider,
        sandbox: effectiveSandbox,
        fetchedAt: new Date().toISOString(),
        balances: [],
        note: "Configura tus credenciales para consultar saldos reales del exchange.",
      };
    }

    return this.exchange.fetchBalances({
      provider,
      sandbox: effectiveSandbox,
      credentials: stored.payload,
    });
  }
}

export class CryptoEngineService {
  constructor(
    private readonly vault: CredentialVaultService,
    private readonly engine: CryptoEnginePort,
  ) {}

  async toggle(rawInput: unknown): Promise<EngineState> {
    const input = toggleEngineSchema.parse(rawInput);

    if (!input.enabled) {
      return this.engine.stop();
    }

    const stored = await this.vault.getDecryptedProvider(input.userId, input.exchange);
    return this.engine.start({
      exchange: input.exchange,
      symbol: input.symbol,
      sandbox: input.sandbox,
      credentials: stored?.payload,
    });
  }

  getStatus() {
    return this.engine.getState();
  }
}

export const createCredentialInput = (
  providerKind: ProviderKind,
  provider: string,
  label: string,
  payload: CredentialPayload,
  sandbox = false,
): SaveCredentialInput => ({
  userId: "demo-user",
  providerKind,
  provider,
  label,
  sandbox,
  payload,
});

export const createId = () => randomUUID();
