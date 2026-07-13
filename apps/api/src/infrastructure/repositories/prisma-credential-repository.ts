import { randomUUID } from "node:crypto";

import type { PrismaClient } from "@prisma/client";

import type { CredentialRecord, SaveCredentialInput } from "../../domain/model.js";
import type { CredentialRepository } from "../../domain/ports.js";

export class PrismaCredentialRepository implements CredentialRepository {
  constructor(private readonly prisma: PrismaClient) {}

  async save(input: SaveCredentialInput, encryptedPayload: string): Promise<CredentialRecord> {
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

  async listByUser(userId: string): Promise<CredentialRecord[]> {
    const credentials = await this.prisma.credential.findMany({
      where: { userId },
      orderBy: {
        updatedAt: "desc",
      },
    });

    return credentials.map((credential) => this.mapCredential(credential));
  }

  async findByProvider(userId: string, provider: string): Promise<CredentialRecord | null> {
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

  private mapCredential(credential: {
    id: string;
    userId: string;
    providerKind: string;
    provider: string;
    label: string;
    encryptedPayload: string;
    sandbox: boolean;
    createdAt: Date;
    updatedAt: Date;
  }): CredentialRecord {
    return {
      id: credential.id,
      userId: credential.userId,
      providerKind: credential.providerKind as CredentialRecord["providerKind"],
      provider: credential.provider,
      label: credential.label,
      encryptedPayload: credential.encryptedPayload,
      sandbox: credential.sandbox,
      createdAt: credential.createdAt.toISOString(),
      updatedAt: credential.updatedAt.toISOString(),
    };
  }

  private inferEmail(userId: string) {
    return `${userId}@jk-flipping.local`;
  }

  private inferDisplayName(userId: string) {
    return userId === "demo-user" ? "JK Operator" : userId;
  }
}
