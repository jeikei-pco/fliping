import { randomUUID } from "node:crypto";

import type { PrismaClient } from "@prisma/client";

import type { AlertRecord, MotorKind } from "../../domain/model.js";
import type { AlertRepository } from "../../domain/ports.js";

export class PrismaAlertRepository implements AlertRepository {
  constructor(private readonly prisma: PrismaClient) {}

  async create(input: {
    userId: string;
    motor: MotorKind;
    title: string;
    description: string;
    sourceUrl?: string;
    severity: string;
  }): Promise<AlertRecord> {
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

  async listByUser(userId: string, limit = 50): Promise<AlertRecord[]> {
    const alerts = await this.prisma.alert.findMany({
      where: { userId },
      orderBy: { createdAt: "desc" },
      take: limit,
    });

    return alerts.map((alert) => this.mapAlert(alert));
  }

  async listByMotor(userId: string, motor: MotorKind, limit = 50): Promise<AlertRecord[]> {
    const alerts = await this.prisma.alert.findMany({
      where: { userId, motor },
      orderBy: { createdAt: "desc" },
      take: limit,
    });

    return alerts.map((alert) => this.mapAlert(alert));
  }

  async markAsRead(id: string): Promise<void> {
    await this.prisma.alert.update({
      where: { id },
      data: { read: true },
    });
  }

  private mapAlert(alert: {
    id: string;
    userId: string;
    motor: string;
    title: string;
    description: string;
    sourceUrl: string | null;
    severity: string;
    read: boolean;
    createdAt: Date;
    updatedAt: Date;
  }): AlertRecord {
    return {
      id: alert.id,
      userId: alert.userId,
      motor: alert.motor as MotorKind,
      title: alert.title,
      description: alert.description,
      sourceUrl: alert.sourceUrl,
      severity: alert.severity as AlertRecord["severity"],
      read: alert.read,
      createdAt: alert.createdAt.toISOString(),
      updatedAt: alert.updatedAt.toISOString(),
    };
  }
}
