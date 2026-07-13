import { PrismaClient } from "@prisma/client";
let prismaClient;
export const getPrismaClient = () => {
    if (!prismaClient) {
        prismaClient = new PrismaClient();
    }
    return prismaClient;
};
