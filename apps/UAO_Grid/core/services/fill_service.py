from core.events import ExecutionEvent, ExecutionEventType


class FillService:
    def __init__(self, event_bus):
        self.event_bus = event_bus

    def publish(self, fill):
        event = ExecutionEvent(ExecutionEventType.ORDER_FILLED, fill, fill.timestamp)
        self.event_bus.publish(event)
