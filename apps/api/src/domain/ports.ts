import type {
  AlertRecord,
  CredentialRecord,
  EngineState,
  EngineStatusRecord,
  ExchangeBalances,
  LLMResponse,
  MotorKind,
  OpportunityRecord,
  SaveCredentialInput,
  ScrapedPage,
} from "./model.js";

// =============================
// Sprint 1 & 2 — Puertos existentes
// =============================

export interface CredentialRepository {
  save(input: SaveCredentialInput, encryptedPayload: string): Promise<CredentialRecord>;
  listByUser(userId: string): Promise<CredentialRecord[]>;
  findByProvider(userId: string, provider: string, sandbox?: boolean): Promise<CredentialRecord | null>;
}

export interface EncryptionPort {
  encrypt(value: string): string;
  decrypt(value: string): string;
}

export interface ExchangePort {
  fetchBalances(params: {
    provider: string;
    sandbox: boolean;
    credentials?: {
      apiKey: string;
      secret?: string;
      passphrase?: string;
    };
  }): Promise<ExchangeBalances>;
  fetchTicker(params: {
    provider: string;
    symbol: string;
    sandbox: boolean;
    credentials?: {
      apiKey: string;
      secret?: string;
      passphrase?: string;
    };
  }): Promise<EngineState["lastTicker"]>;
}

export interface CryptoEnginePort {
  start(params: {
    exchange: string;
    symbol: string;
    sandbox: boolean;
    credentials?: {
      apiKey: string;
      secret?: string;
      passphrase?: string;
    };
  }): Promise<EngineState>;
  stop(): EngineState;
  getState(): EngineState;
}

// =============================
// Sprint 3 & 4 — Nuevos puertos
// =============================

export interface AlertRepository {
  create(input: {
    userId: string;
    motor: MotorKind;
    title: string;
    description: string;
    sourceUrl?: string;
    severity: string;
  }): Promise<AlertRecord>;
  listByUser(userId: string, limit?: number): Promise<AlertRecord[]>;
  listByMotor(userId: string, motor: MotorKind, limit?: number): Promise<AlertRecord[]>;
  markAsRead(id: string): Promise<void>;
}

export interface OpportunityRepository {
  create(input: {
    userId: string;
    motor: "real-estate" | "saas";
    title: string;
    description: string;
    sourceUrl?: string;
    aiAnalysis: string;
    estimatedValue?: string;
    estimatedRepair?: string;
    dealScore?: number;
    tags: string[];
  }): Promise<OpportunityRecord>;
  listByUser(userId: string, limit?: number): Promise<OpportunityRecord[]>;
  listByMotor(userId: string, motor: "real-estate" | "saas", limit?: number): Promise<OpportunityRecord[]>;
  findById(id: string): Promise<OpportunityRecord | null>;
  updateStatus(id: string, status: "reviewed" | "archived"): Promise<void>;
}

export interface EngineStatusRepository {
  findByUserAndMotor(userId: string, motor: MotorKind): Promise<EngineStatusRecord | null>;
  save(input: {
    userId: string;
    motor: MotorKind;
    enabled: boolean;
    startedAt?: string;
    lastRunAt?: string;
    lastResult?: string;
    lastError?: string;
    config?: string;
  }): Promise<EngineStatusRecord>;
}

export interface ScraperPort {
  crawl(params: {
    url: string;
    apiKey: string;
    limit?: number;
  }): Promise<ScrapedPage[]>;
}

export interface LLMPort {
  chat(params: {
    apiKey: string;
    model: string;
    messages: Array<{ role: "system" | "user" | "assistant"; content: string }>;
    temperature?: number;
  }): Promise<LLMResponse>;
}
