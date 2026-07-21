class MarketService:
    def __init__(self, exchange):
        self.exchange = exchange

    def ticker(self, symbol):
        return self.exchange.fetch_ticker(symbol)

    def candles(self, symbol, timeframe="5m", limit=200):
        return self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
