class BalanceService:
    def __init__(self, provider):
        self.provider = provider

    def get(self):
        return self.provider.get_balance()
