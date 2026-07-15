import { randomUUID } from "node:crypto";
export class PrismaCredentialRepository {
    prisma;
    constructor(prisma) {
        this.prisma = prisma;
    }
    async save(input, encryptedPayload) {
        const credential = await this.prisma.credential.upsert({
            where: {
                userId_provider_sandbox: {
                    userId: input.userId,
                    provider: input.provider,
                    sandbox: input.sandbox ?? false,
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
    async findByProvider(userId, provider, sandbox) {
        if (sandbox !== undefined) {
            const credential = await this.prisma.credential.findUnique({
                where: {
                    userId_provider_sandbox: {
                        userId,
                        provider,
                        sandbox,
                    },
                },
            });
            return credential ? this.mapCredential(credential) : null;
        }
        const credential = await this.prisma.credential.findFirst({
            where: {
                userId,
                provider,
            },
            orderBy: { updatedAt: "desc" },
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
