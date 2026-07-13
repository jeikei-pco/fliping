import { randomUUID } from "node:crypto";

import type { PrismaClient } from "@prisma/client";

import type { EngineStatusRecord, MotorKind } from "../../domain/model.js";
import type { EngineStatusRepository } from "../../domain/ports.js";

export class PrismaEngineStatusRepository implements EngineStatusRepository {
  constructor(private readonly prisma: PrismaClient) {}

  async findByUserAndMotor(userId: string, motor: MotorKind): Promise<EngineStatusRecord | null> {
    const record = await this.prisma.engineStatus.findUnique({
      where: { userId_motor: { userId, motor } },
    });

    return record ? this.mapRecord(record) : null;
  }

  async save(input: {
    userId: string;
    motor: MotorKind;
    enabled: boolean;
    startedAt?: string;
    lastRunAt?: string;
    lastResult?: string;
    lastError?: string;
    config?: string;
  }): Promise<EngineStatusRecord> {
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

  private mapRecord(record: {
    id: string;
    userId: string;
    motor: string;
    enabled: boolean;
    startedAt: Date | null;
    lastRunAt: Date | null;
    lastResult: string | null;
    lastError: string | null;
    config: string | null;
    createdAt: Date;
    updatedAt: Date;
  }): EngineStatusRecord {
    return {
      id: record.id,
      userId: record.userId,
      motor: record.motor as MotorKind,
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
