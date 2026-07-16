import { getPrismaClient } from './infrastructure/services/prisma-client-service.js';
import { AesEncryptionService } from './infrastructure/services/aes-encryption-service.js';

async function run() {
  const prisma = getPrismaClient();
  const encryption = new AesEncryptionService(process.env.MASTER_KEY || 'jk-flipping-local-master-key');
  const cred = await prisma.credential.findFirst({ where: { provider: 'okx', sandbox: true } });
  
  if (cred) {
    const payload = JSON.parse(encryption.decrypt(cred.encryptedPayload));
    console.log("OKX_API_KEY_DEMO=" + payload.apiKey);
    console.log("OKX_API_SECRET_DEMO=" + payload.secret);
    console.log("OKX_PASSPHRASE_DEMO=" + payload.passphrase);
  } else {
    console.log("No credentials found");
  }
}

run().catch(console.error).finally(() => process.exit(0));
