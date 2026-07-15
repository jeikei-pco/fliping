import { getPrismaClient } from "./infrastructure/services/prisma-client-service.js";
import { AesEncryptionService } from "./infrastructure/services/aes-encryption-service.js";
import { spawn } from "child_process";
import * as path from "path";

async function run() {
  const prisma = getPrismaClient();
  const encryption = new AesEncryptionService(process.env.MASTER_KEY || "jk-flipping-local-master-key");
  
  console.log("Obteniendo credenciales de demostración desde la base de datos...");
  const cred = await prisma.credential.findFirst({ where: { provider: "okx", sandbox: true } });
  if (!cred) {
    console.log("No se encontraron credenciales de demo en la BD.");
    return;
  }
  
  const decrypted = encryption.decrypt(cred.encryptedPayload);
  const payload = JSON.parse(decrypted);
  
  console.log("Credenciales descifradas correctamente. Lanzando el test en Docker (jk-flipping-grid-worker)...");
  
  const args = [
    "exec",
    "-e", `OKX_API_KEY=${payload.apiKey}`,
    "-e", `OKX_SECRET=${payload.secret}`,
    "-e", `OKX_PASSPHRASE=${payload.passphrase}`,
    "jk-flipping-grid-worker",
    "python", "test_screener.py"
  ];
  
  const pyProcess = spawn("docker", args, {
    stdio: "inherit"
  });

  pyProcess.on("close", (code) => {
    console.log(`Proceso de Python finalizado con código ${code}`);
    process.exit(code || 0);
  });
}

run().catch(console.error);
