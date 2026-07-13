import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";

import type { EncryptionPort } from "../../domain/ports.js";

export class AesEncryptionService implements EncryptionPort {
  private readonly key: Buffer;

  constructor(masterKey: string) {
    this.key = createHash("sha256").update(masterKey).digest();
  }

  encrypt(value: string): string {
    const iv = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", this.key, iv);
    const encrypted = Buffer.concat([cipher.update(value, "utf8"), cipher.final()]);
    const tag = cipher.getAuthTag();
    return `${iv.toString("base64")}:${tag.toString("base64")}:${encrypted.toString("base64")}`;
  }

  decrypt(value: string): string {
    const [ivEncoded, tagEncoded, encryptedEncoded] = value.split(":");
    const decipher = createDecipheriv("aes-256-gcm", this.key, Buffer.from(ivEncoded, "base64"));
    decipher.setAuthTag(Buffer.from(tagEncoded, "base64"));
    const decrypted = Buffer.concat([
      decipher.update(Buffer.from(encryptedEncoded, "base64")),
      decipher.final(),
    ]);
    return decrypted.toString("utf8");
  }
}
