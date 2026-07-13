export type ProviderKind = "exchange" | "ai" | "scraper";

export type CredentialRecord = {
  id: string;
  userId: string;
  providerKind: ProviderKind;
  provider: string;
  label: string;
  encryptedPayload: string;
  sandbox: boolean;
  createdAt: string;
  updatedAt: string;
};

export type CredentialPayload = {
  apiKey: string;
  secret?: string;
  passphrase?: string;
  baseUrl?: string;
};

export type SaveCredentialInput = {
  userId: string;
  providerKind: ProviderKind;
  provider: string;
  label: string;
  sandbox?: boolean;
  payload: CredentialPayload;
};

export type ExchangeBalances = {
  exchange: string;
  sandbox: boolean;
  fetchedAt: string;
  balances: Array<{
    asset: string;
    free: number;
    used: number;
    total: number;
  }>;
  note?: string;
};

export type EngineState = {
  enabled: boolean;
  exchange: string;
  symbol: string;
  sandbox: boolean;
  startedAt: string | null;
  lastCheckedAt: string | null;
  lastTicker: {
    bid: number | null;
    ask: number | null;
    last: number | null;
  } | null;
  lastError: string | null;
};

// =============================
// Sprint 3 & 4 — Nuevos tipos
// =============================

export type MotorKind = "tech" | "real-estate" | "saas" | "grid";

export type AlertSeverity = "low" | "medium" | "high";

export type AlertRecord = {
  id: string;
  userId: string;
  motor: MotorKind;
  title: string;
  description: string;
  sourceUrl: string | null;
  severity: AlertSeverity;
  read: boolean;
  createdAt: string;
  updatedAt: string;
};

export type OpportunityRecord = {
  id: string;
  userId: string;
  motor: "real-estate" | "saas";
  title: string;
  description: string;
  sourceUrl: string | null;
  aiAnalysis: string;
  estimatedValue: string | null;
  estimatedRepair: string | null;
  dealScore: number | null;
  tags: string[];
  status: "new" | "reviewed" | "archived";
  createdAt: string;
  updatedAt: string;
};

export type EngineStatusRecord = {
  id: string;
  userId: string;
  motor: MotorKind;
  enabled: boolean;
  startedAt: string | null;
  lastRunAt: string | null;
  lastResult: string | null;
  lastError: string | null;
  config: string | null;
  createdAt: string;
  updatedAt: string;
};

export type ScrapedPage = {
  url: string;
  markdown: string;
  rawHtml?: string;
};

export type LLMResponse = {
  content: string;
  model: string;
  tokensUsed?: number;
};
