import "dotenv/config";
import { PrismaClient } from "@prisma/client";
import { PrismaPg } from "@prisma/adapter-pg";

let prismaClient: PrismaClient | undefined;

export const getPrismaClient = () => {
  if (!prismaClient) {
    const adapter = new PrismaPg({ connectionString: process.env["DATABASE_URL"] });
    prismaClient = new PrismaClient({ adapter });
  }

  return prismaClient;
};
