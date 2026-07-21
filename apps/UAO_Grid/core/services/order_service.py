class OrderService:
    def __init__(self, provider):
        self.provider = provider

    def sync(self, symbol=None):
        return self.provider.get_open_orders(symbol)

    def reconcile(self, desired, current):
        return self.provider.reconciliar_ordenes(desired, current)

    def cancel_all(self, symbol):
        return self.provider.cancel_all_orders(symbol)
