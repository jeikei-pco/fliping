export class DemoAwareCryptoEngineService {
    exchange;
    intervalMs = 15000;
    timer = null;
    state = {
        enabled: false,
        exchange: "okx",
        symbol: "BTC/USDT",
        sandbox: true,
        startedAt: null,
        lastCheckedAt: null,
        lastTicker: null,
        lastError: null,
    };
    constructor(exchange) {
        this.exchange = exchange;
    }
    async start(params) {
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
    stop() {
        this.clearTimer();
        this.state = {
            ...this.state,
            enabled: false,
        };
        return this.state;
    }
    getState() {
        return this.state;
    }
    async refreshTicker(params) {
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
        }
        catch (error) {
            this.state = {
                ...this.state,
                lastCheckedAt: new Date().toISOString(),
                lastError: error instanceof Error ? error.message : "No fue posible consultar el ticker.",
            };
        }
    }
    clearTimer() {
        if (this.timer) {
            clearInterval(this.timer);
            this.timer = null;
        }
    }
}
