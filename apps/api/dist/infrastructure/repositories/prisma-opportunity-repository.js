import { randomUUID } from "node:crypto";
export class PrismaOpportunityRepository {
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
                passwordHash: "",
            },
        });
        const opportunity = await this.prisma.opportunity.create({
            data: {
                id: randomUUID(),
                userId: input.userId,
                motor: input.motor,
                title: input.title,
                description: input.description,
                sourceUrl: input.sourceUrl ?? null,
                aiAnalysis: input.aiAnalysis,
                estimatedValue: input.estimatedValue ?? null,
                estimatedRepair: input.estimatedRepair ?? null,
                dealScore: input.dealScore ?? null,
                tags: JSON.stringify(input.tags),
                status: "new",
            },
        });
        return this.mapOpportunity(opportunity);
    }
    async listByUser(userId, limit = 50) {
        const opportunities = await this.prisma.opportunity.findMany({
            where: { userId },
            orderBy: { createdAt: "desc" },
            take: limit,
        });
        return opportunities.map((opportunity) => this.mapOpportunity(opportunity));
    }
    async listByMotor(userId, motor, limit = 50) {
        const opportunities = await this.prisma.opportunity.findMany({
            where: { userId, motor },
            orderBy: { createdAt: "desc" },
            take: limit,
        });
        return opportunities.map((opportunity) => this.mapOpportunity(opportunity));
    }
    async findById(id) {
        const opportunity = await this.prisma.opportunity.findUnique({
            where: { id },
        });
        return opportunity ? this.mapOpportunity(opportunity) : null;
    }
    async updateStatus(id, status) {
        await this.prisma.opportunity.update({
            where: { id },
            data: { status },
        });
    }
    mapOpportunity(opportunity) {
        let tags = [];
        try {
            tags = JSON.parse(opportunity.tags);
        }
        catch {
            tags = [];
        }
        return {
            id: opportunity.id,
            userId: opportunity.userId,
            motor: opportunity.motor,
            title: opportunity.title,
            description: opportunity.description,
            sourceUrl: opportunity.sourceUrl,
            aiAnalysis: opportunity.aiAnalysis,
            estimatedValue: opportunity.estimatedValue,
            estimatedRepair: opportunity.estimatedRepair,
            dealScore: opportunity.dealScore,
            tags,
            status: opportunity.status,
            createdAt: opportunity.createdAt.toISOString(),
            updatedAt: opportunity.updatedAt.toISOString(),
        };
    }
}
