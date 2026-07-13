import type { EngineState } from "../../domain/model.js";
import type { CryptoEnginePort, ExchangePort } from "../../domain/ports.js";

type StartParams = {
  exchange: string;
  symbol: string;
  sandbox: boolean;
  credentials?: {
    apiKey: string;
    secret?: string;
    passphrase?: string;
  };
};

export class DemoAwareCryptoEngineService implements CryptoEnginePort {
  private readonly intervalMs = 15000;
  private timer: NodeJS.Timeout | null = null;
  private state: EngineState = {
    enabled: false,
    exchange: "okx",
    symbol: "BTC/USDT",
    sandbox: true,
    startedAt: null,
    lastCheckedAt: null,
    lastTicker: null,
    lastError: null,
  };

  constructor(private readonly exchange: ExchangePort) {}

  async start(params: StartParams): Promise<EngineState> {
    this.state = {
      ...this.state,
      enabled: true,
      exchange: params.exchange,
      symbol: params.symbol,
      sandbox: params.sandbox,
      startedAt: new Date().toISOString(),
      lastError: null,
    };

    await this.refreshTicker(params);
    this.clearTimer();
    this.timer = setInterval(() => {
      void this.refreshTicker(params);
    }, this.intervalMs);

    return this.state;
  }

  stop(): EngineState {
    this.clearTimer();
    this.state = {
      ...this.state,
      enabled: false,
    };

    return this.state;
  }

  getState(): EngineState {
    return this.state;
  }

  private async refreshTicker(params: StartParams) {
    try {
      const ticker = await this.exchange.fetchTicker({
        provider: params.exchange,
        symbol: params.symbol,
        sandbox: params.sandbox,
        credentials: params.credentials,
      });

      this.state = {
        ...this.state,
        lastTicker: ticker,
        lastCheckedAt: new Date().toISOString(),
        lastError: null,
      };
    } catch (error) {
      this.state = {
        ...this.state,
        lastCheckedAt: new Date().toISOString(),
        lastError: error instanceof Error ? error.message : "No fue posible consultar el ticker.",
      };
    }
  }

  private clearTimer() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }
}
