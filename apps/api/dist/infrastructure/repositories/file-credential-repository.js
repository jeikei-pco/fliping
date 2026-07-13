import { randomUUID } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";
export class FileCredentialRepository {
    filePath;
    constructor(filePath) {
        this.filePath = filePath;
    }
    async save(input, encryptedPayload) {
        const store = await this.readStore();
        const now = new Date().toISOString();
        const existingIndex = store.credentials.findIndex((credential) => credential.userId === input.userId && credential.provider === input.provider);
        const nextRecord = {
            id: existingIndex >= 0 ? store.credentials[existingIndex].id : randomUUID(),
            userId: input.userId,
            providerKind: input.providerKind,
            provider: input.provider,
            label: input.label,
            encryptedPayload,
            sandbox: input.sandbox ?? false,
            createdAt: existingIndex >= 0 ? store.credentials[existingIndex].createdAt : now,
            updatedAt: now,
        };
        if (existingIndex >= 0) {
            store.credentials[existingIndex] = nextRecord;
        }
        else {
            store.credentials.push(nextRecord);
        }
        await this.writeStore(store);
        return nextRecord;
    }
    async listByUser(userId) {
        const store = await this.readStore();
        return store.credentials.filter((credential) => credential.userId === userId);
    }
    async findByProvider(userId, provider) {
        const store = await this.readStore();
        return store.credentials.find((credential) => credential.userId === userId && credential.provider === provider) ?? null;
    }
    async readStore() {
        try {
            const raw = await fs.readFile(this.filePath, "utf8");
            return JSON.parse(raw);
        }
        catch (error) {
            await this.ensureDir();
            return { credentials: [] };
        }
    }
    async writeStore(store) {
        await this.ensureDir();
        await fs.writeFile(this.filePath, JSON.stringify(store, null, 2), "utf8");
    }
    async ensureDir() {
        await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    }
}
