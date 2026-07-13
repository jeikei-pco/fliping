import { randomUUID } from "node:crypto";
export class PrismaEngineStatusRepository {
    prisma;
    constructor(prisma) {
        this.prisma = prisma;
    }
    async findByUserAndMotor(userId, motor) {
        const record = await this.prisma.engineStatus.findUnique({
            where: { userId_motor: { userId, motor } },
        });
        return record ? this.mapRecord(record) : null;
    }
    async save(input) {
        await this.prisma.appUser.upsert({
            where: { id: input.userId },
            update: {},
            create: {
                id: input.userId,
                displayName: input.userId === "demo-user" ? "JK Operator" : input.userId,
                email: `${input.userId}@jk-flipping.local`,
            },
        });
        const record = await this.prisma.engineStatus.upsert({
            where: { userId_motor: { userId: input.userId, motor: input.motor } },
            update: {
                enabled: input.enabled,
                startedAt: input.startedAt ? new Date(input.startedAt) : undefined,
                lastRunAt: input.lastRunAt ? new Date(input.lastRunAt) : undefined,
                lastResult: input.lastResult ?? null,
                lastError: input.lastError ?? null,
                config: input.config ?? null,
            },
            create: {
                id: randomUUID(),
                userId: input.userId,
                motor: input.motor,
                enabled: input.enabled,
                startedAt: input.startedAt ? new Date(input.startedAt) : null,
                lastRunAt: input.lastRunAt ? new Date(input.lastRunAt) : null,
                lastResult: input.lastResult ?? null,
                lastError: input.lastError ?? null,
                config: input.config ?? null,
            },
        });
        return this.mapRecord(record);
    }
    mapRecord(record) {
        return {
            id: record.id,
            userId: record.userId,
            motor: record.motor,
            enabled: record.enabled,
            startedAt: record.startedAt?.toISOString() ?? null,
            lastRunAt: record.lastRunAt?.toISOString() ?? null,
            lastResult: record.lastResult,
            lastError: record.lastError,
            config: record.config,
            createdAt: record.createdAt.toISOString(),
            updatedAt: record.updatedAt.toISOString(),
        };
    }
}
