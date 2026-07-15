import { randomUUID } from "node:crypto";

import type { PrismaClient } from "@prisma/client";

import type { CredentialRecord, SaveCredentialInput } from "../../domain/model.js";
import type { CredentialRepository } from "../../domain/ports.js";

export class PrismaCredentialRepository implements CredentialRepository {
  constructor(private readonly prisma: PrismaClient) {}

  async save(input: SaveCredentialInput, encryptedPayload: string): Promise<CredentialRecord> {
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

  async listByUser(userId: string): Promise<CredentialRecord[]> {
    const credentials = await this.prisma.credential.findMany({
      where: { userId },
      orderBy: {
        updatedAt: "desc",
      },
    });

    return credentials.map((credential) => this.mapCredential(credential));
  }

  async findByProvider(userId: string, provider: string, sandbox?: boolean): Promise<CredentialRecord | null> {
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
