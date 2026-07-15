import { randomUUID } from "node:crypto";

import type { PrismaClient } from "@prisma/client";

import type { OpportunityRecord } from "../../domain/model.js";
import type { OpportunityRepository } from "../../domain/ports.js";

export class PrismaOpportunityRepository implements OpportunityRepository {
  constructor(private readonly prisma: PrismaClient) {}

  async create(input: {
    userId: string;
    motor: "real-estate" | "saas";
    title: string;
    description: string;
    sourceUrl?: string;
    aiAnalysis: string;
    estimatedValue?: string;
    estimatedRepair?: string;
    dealScore?: number;
    tags: string[];
  }): Promise<OpportunityRecord> {
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

  async listByUser(userId: string, limit = 50): Promise<OpportunityRecord[]> {
    const opportunities = await this.prisma.opportunity.findMany({
      where: { userId },
      orderBy: { createdAt: "desc" },
      take: limit,
    });

    return opportunities.map((opportunity) => this.mapOpportunity(opportunity));
  }

  async listByMotor(
    userId: string,
    motor: "real-estate" | "saas",
    limit = 50,
  ): Promise<OpportunityRecord[]> {
    const opportunities = await this.prisma.opportunity.findMany({
      where: { userId, motor },
      orderBy: { createdAt: "desc" },
      take: limit,
    });

    return opportunities.map((opportunity) => this.mapOpportunity(opportunity));
  }

  async findById(id: string): Promise<OpportunityRecord | null> {
    const opportunity = await this.prisma.opportunity.findUnique({
      where: { id },
    });

    return opportunity ? this.mapOpportunity(opportunity) : null;
  }

  async updateStatus(id: string, status: "reviewed" | "archived"): Promise<void> {
    await this.prisma.opportunity.update({
      where: { id },
      data: { status },
    });
  }

  private mapOpportunity(opportunity: {
    id: string;
    userId: string;
    motor: string;
    title: string;
    description: string;
    sourceUrl: string | null;
    aiAnalysis: string;
    estimatedValue: string | null;
    estimatedRepair: string | null;
    dealScore: number | null;
    tags: string;
    status: string;
    createdAt: Date;
    updatedAt: Date;
  }): OpportunityRecord {
    let tags: string[] = [];
    try {
      tags = JSON.parse(opportunity.tags) as string[];
    } catch {
      tags = [];
    }

    return {
      id: opportunity.id,
      userId: opportunity.userId,
      motor: opportunity.motor as "real-estate" | "saas",
      title: opportunity.title,
      description: opportunity.description,
      sourceUrl: opportunity.sourceUrl,
      aiAnalysis: opportunity.aiAnalysis,
      estimatedValue: opportunity.estimatedValue,
      estimatedRepair: opportunity.estimatedRepair,
      dealScore: opportunity.dealScore,
      tags,
      status: opportunity.status as OpportunityRecord["status"],
      createdAt: opportunity.createdAt.toISOString(),
      updatedAt: opportunity.updatedAt.toISOString(),
    };
  }
}
