import { randomUUID } from "node:crypto";
import { z } from "zod";
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
    repository;
    encryption;
    constructor(repository, encryption) {
        this.repository = repository;
        this.encryption = encryption;
    }
    async save(rawInput) {
        const input = saveCredentialSchema.parse(rawInput);
        const encryptedPayload = this.encryption.encrypt(JSON.stringify(input.payload));
        return this.repository.save(input, encryptedPayload);
    }
    async list(userId) {
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
    async getDecryptedProvider(userId, provider) {
        const record = await this.repository.findByProvider(userId, provider);
        if (!record) {
            return null;
        }
        const decrypted = this.encryption.decrypt(record.encryptedPayload);
        return {
            ...record,
            payload: JSON.parse(decrypted),
        };
    }
}
export class BalanceService {
    vault;
    exchange;
    constructor(vault, exchange) {
        this.vault = vault;
        this.exchange = exchange;
    }
    async getBalances(userId, provider, sandbox = true) {
        const stored = await this.vault.getDecryptedProvider(userId, provider);
        if (!stored) {
            return {
                exchange: provider,
                sandbox,
                fetchedAt: new Date().toISOString(),
                balances: [],
                note: "Configura tus credenciales para consultar saldos reales del exchange.",
            };
        }
        return this.exchange.fetchBalances({
            provider,
            sandbox,
            credentials: stored.payload,
        });
    }
}
export class CryptoEngineService {
    vault;
    engine;
    constructor(vault, engine) {
        this.vault = vault;
        this.engine = engine;
    }
    async toggle(rawInput) {
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
export const createCredentialInput = (providerKind, provider, label, payload, sandbox = false) => ({
    userId: "demo-user",
    providerKind,
    provider,
    label,
    sandbox,
    payload,
});
export const createId = () => randomUUID();
