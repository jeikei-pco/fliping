import ccxt from "ccxt";
import dns from "node:dns";

// Forzar el uso de IPv4 primero a nivel global en Node.js para evitar bloqueos por IPv6
dns.setDefaultResultOrder("ipv4first");

import type { ExchangeBalances } from "../../domain/model.js";
import type { ExchangePort } from "../../domain/ports.js";

type Credentials = {
  apiKey: string;
  secret?: string;
  passphrase?: string;
};

export class CcxtExchangeService implements ExchangePort {
  async fetchBalances(params: {
    provider: string;
    sandbox: boolean;
    credentials?: Credentials;
  }): Promise<ExchangeBalances> {
    if (!params.credentials?.apiKey || !params.credentials.secret) {
      return {
        exchange: params.provider,
        sandbox: params.sandbox,
        fetchedAt: new Date().toISOString(),
        balances: [],
        note: "Faltan API key y secret para consultar saldos reales.",
      };
    }

    const exchange = this.createExchange(params.provider, params.credentials, params.sandbox);
    const balance = await exchange.fetchBalance();
    const balances = Object.entries(balance.total ?? {})
      .map(([asset, total]) => {
        const free = Number(balance.free?.[asset] ?? 0);
        const used = Number(balance.used?.[asset] ?? 0);
        const totalValue = Number(total ?? 0);
        return {
          asset,
          free,
          used,
          total: totalValue,
        };
      })
      .filter((entry) => entry.total > 0)
      .sort((left, right) => right.total - left.total);

    return {
      exchange: params.provider,
      sandbox: params.sandbox,
      fetchedAt: new Date().toISOString(),
      balances,
      note: balances.length === 0 ? "El exchange respondió sin saldos con fondos disponibles." : undefined,
    };
  }

  async fetchTicker(params: {
    provider: string;
    symbol: string;
    sandbox: boolean;
    credentials?: Credentials;
  }) {
    const exchange = this.createExchange(params.provider, params.credentials, params.sandbox);
    const ticker = await exchange.fetchTicker(params.symbol);

    return {
      bid: ticker.bid ?? null,
      ask: ticker.ask ?? null,
      last: ticker.last ?? null,
    };
  }

  private createExchange(provider: string, credentials?: Credentials, sandbox = true) {
    const exchangeFactory = this.resolveExchange(provider);
    const exchange = new exchangeFactory({
      apiKey: credentials?.apiKey,
      secret: credentials?.secret,
      password: credentials?.passphrase,
      enableRateLimit: true,
    });

    if (sandbox && typeof exchange.setSandboxMode === "function") {
      exchange.setSandboxMode(true);
    }

    return exchange;
  }

  private resolveExchange(provider: string) {
    const exchangeFactory = (ccxt as unknown as Record<
      string,
      new (config: Record<string, unknown>) => {
        setSandboxMode?: (enabled: boolean) => void;
        fetchBalance: () => Promise<{
          total?: Record<string, number>;
          free?: Record<string, number>;
          used?: Record<string, number>;
        }>;
        fetchTicker: (symbol: string) => Promise<{
          bid?: number;
          ask?: number;
          last?: number;
        }>;
      }
    >)[provider];

    if (!exchangeFactory) {
      throw new Error(`Exchange no soportado: ${provider}`);
    }

    return exchangeFactory;
  }
}
