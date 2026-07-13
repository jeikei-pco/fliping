import ccxt from "ccxt";
export class CcxtExchangeService {
    async fetchBalances(params) {
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
    async fetchTicker(params) {
        const exchange = this.createExchange(params.provider, params.credentials, params.sandbox);
        const ticker = await exchange.fetchTicker(params.symbol);
        return {
            bid: ticker.bid ?? null,
            ask: ticker.ask ?? null,
            last: ticker.last ?? null,
        };
    }
    createExchange(provider, credentials, sandbox = true) {
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
    resolveExchange(provider) {
        const exchangeFactory = ccxt[provider];
        if (!exchangeFactory) {
            throw new Error(`Exchange no soportado: ${provider}`);
        }
        return exchangeFactory;
    }
}
