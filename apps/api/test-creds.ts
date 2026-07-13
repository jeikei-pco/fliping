import { PrismaClient } from '@prisma/client'; const prisma = new PrismaClient(); async function main() { const creds = await prisma.credential.findMany(); console.log(creds); } main();
