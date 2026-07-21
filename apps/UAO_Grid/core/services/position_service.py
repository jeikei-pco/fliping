class PositionService:
    def __init__(self, provider):
        self.provider = provider

    def sync(self, symbol=None):
        return self.provider.get_open_positions(symbol)
