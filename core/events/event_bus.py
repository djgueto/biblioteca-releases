from __future__ import annotations


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list] = {}

    def subscribe(self, event_type: str, callback):
        self._subscribers.setdefault(event_type, []).append(callback)

    def publish(self, event_type: str, payload: dict):
        for callback in self._subscribers.get(event_type, []):
            callback(payload)
