import { randomUUID } from "node:crypto";
export class PrismaCredentialRepository {
    prisma;
    constructor(prisma) {
        this.prisma = prisma;
    }
    async save(input, encryptedPayload) {
        await this.prisma.appUser.upsert({
            where: {
                id: input.userId,
            },
            update: {
                displayName: this.inferDisplayName(input.userId),
                email: this.inferEmail(input.userId),
            },
            create: {
                id: input.userId,
                displayName: this.inferDisplayName(input.userId),
                email: this.inferEmail(input.userId),
            },
        });
        const credential = await this.prisma.credential.upsert({
            where: {
                userId_provider: {
                    userId: input.userId,
                    provider: input.provider,
                },
            },
            update: {
                providerKind: input.providerKind,
                label: input.label,
                encryptedPayload,
                sandbox: input.sandbox ?? false,
            },
            create: {
                id: randomUUID(),
                userId: input.userId,
                providerKind: input.providerKind,
                provider: input.provider,
                label: input.label,
                encryptedPayload,
                sandbox: input.sandbox ?? false,
            },
        });
        return this.mapCredential(credential);
    }
    async listByUser(userId) {
        const credentials = await this.prisma.credential.findMany({
            where: { userId },
            orderBy: {
                updatedAt: "desc",
            },
        });
        return credentials.map((credential) => this.mapCredential(credential));
    }
    async findByProvider(userId, provider) {
        const credential = await this.prisma.credential.findUnique({
            where: {
                userId_provider: {
                    userId,
                    provider,
                },
            },
        });
        return credential ? this.mapCredential(credential) : null;
    }
    mapCredential(credential) {
        return {
            id: credential.id,
            userId: credential.userId,
            providerKind: credential.providerKind,
            provider: credential.provider,
            label: credential.label,
            encryptedPayload: credential.encryptedPayload,
            sandbox: credential.sandbox,
            createdAt: credential.createdAt.toISOString(),
            updatedAt: credential.updatedAt.toISOString(),
        };
    }
    inferEmail(userId) {
        return `${userId}@jk-flipping.local`;
    }
    inferDisplayName(userId) {
        return userId === "demo-user" ? "JK Operator" : userId;
    }
}
