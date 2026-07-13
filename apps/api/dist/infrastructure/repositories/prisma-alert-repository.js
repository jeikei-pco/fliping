import { randomUUID } from "node:crypto";
export class PrismaAlertRepository {
    prisma;
    constructor(prisma) {
        this.prisma = prisma;
    }
    async create(input) {
        await this.prisma.appUser.upsert({
            where: { id: input.userId },
            update: {},
            create: {
                id: input.userId,
                displayName: input.userId === "demo-user" ? "JK Operator" : input.userId,
                email: `${input.userId}@jk-flipping.local`,
            },
        });
        const alert = await this.prisma.alert.create({
            data: {
                id: randomUUID(),
                userId: input.userId,
                motor: input.motor,
                title: input.title,
                description: input.description,
                sourceUrl: input.sourceUrl ?? null,
                severity: input.severity,
            },
        });
        return this.mapAlert(alert);
    }
    async listByUser(userId, limit = 50) {
        const alerts = await this.prisma.alert.findMany({
            where: { userId },
            orderBy: { createdAt: "desc" },
            take: limit,
        });
        return alerts.map((alert) => this.mapAlert(alert));
    }
    async listByMotor(userId, motor, limit = 50) {
        const alerts = await this.prisma.alert.findMany({
            where: { userId, motor },
            orderBy: { createdAt: "desc" },
            take: limit,
        });
        return alerts.map((alert) => this.mapAlert(alert));
    }
    async markAsRead(id) {
        await this.prisma.alert.update({
            where: { id },
            data: { read: true },
        });
    }
    mapAlert(alert) {
        return {
            id: alert.id,
            userId: alert.userId,
            motor: alert.motor,
            title: alert.title,
            description: alert.description,
            sourceUrl: alert.sourceUrl,
            severity: alert.severity,
            read: alert.read,
            createdAt: alert.createdAt.toISOString(),
            updatedAt: alert.updatedAt.toISOString(),
        };
    }
}
