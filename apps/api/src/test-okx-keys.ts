import { getPrismaClient } from "./infrastructure/services/prisma-client-service.js";
import { AesEncryptionService } from "./infrastructure/services/aes-encryption-service.js";
import ccxt from "ccxt";
import crypto from "crypto";

async function run() {
  const prisma = getPrismaClient();
  const encryption = new AesEncryptionService(process.env.MASTER_KEY || "jk-flipping-local-master-key");
  
  console.log("Connecting to database...");
  const cred = await prisma.credential.findFirst({ where: { provider: "okx", sandbox: true } });
  if (!cred) {
    console.log("No demo credential found in DB.");
    return;
  }
  
  console.log("Decrypting credentials...");
  const decrypted = encryption.decrypt(cred.encryptedPayload);
  const payload = JSON.parse(decrypted);
  console.log("Extracted payload (keys partially hidden):", { 
    apiKey: payload.apiKey ? payload.apiKey.substring(0, 4) + "***" : "missing", 
    secret: payload.secret ? payload.secret.substring(0, 4) + "***" : "missing",
    passphrase: payload.passphrase
  });
  
  const exchange = new ccxt.okx({
    apiKey: payload.apiKey,
    secret: payload.secret,
    password: payload.passphrase,
    enableRateLimit: true,
  });
  exchange.setSandboxMode(true);
  
  try {
    console.log("\n[1] Testing CCXT balance fetch...");
    const balance = await exchange.fetchBalance();
    console.log("Success! Balances found:", Object.keys(balance.total || {}));
  } catch (error: any) {
    console.error("CCXT Error:", error.message);
  }

  // Also test raw HTTP
  try {
    console.log("\n[2] Testing direct HTTP to OKX v5 Demo API...");
    const timestamp = new Date().toISOString();
    const method = 'GET';
    const requestPath = '/api/v5/account/balance';
    const signStr = timestamp + method + requestPath;
    const signature = crypto.createHmac('sha256', payload.secret).update(signStr).digest('base64');
    
    const response = await fetch("https://www.okx.com/api/v5/account/balance", {
      headers: {
        'OK-ACCESS-KEY': payload.apiKey,
        'OK-ACCESS-SIGN': signature,
        'OK-ACCESS-TIMESTAMP': timestamp,
        'OK-ACCESS-PASSPHRASE': payload.passphrase,
        'x-simulated-trading': '1',
      }
    });
    const data = await response.json();
    console.log("Direct HTTP Response Code:", data.code);
    console.log("Direct HTTP Response Msg:", data.msg);
    if (data.data && data.data.length > 0) {
        console.log("Direct HTTP Balances:", data.data[0].details.length, "assets found.");
    }
  } catch (err: any) {
    console.error("Direct HTTP Error:", err.message);
  }
}

run().catch(console.error).finally(() => process.exit(0));
