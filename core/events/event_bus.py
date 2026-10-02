from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class EventBus:
    """Bus pub/sub síncrono y seguro entre hilos.

    Los callbacks se ejecutan en el hilo que publica: quien necesite cambiar
    de hilo (Qt, asyncio/WebSocket) debe hacerlo en su propio callback. Un
    suscriptor que lanza una excepción no interrumpe al publicador (p. ej. el
    ciclo del scheduler) ni al resto de suscriptores.
    """

    def __init__(self):
        self._subscribers: dict[str, list] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, callback):
        """Suscribe `callback(payload)`; devuelve una función para desuscribirse."""
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(callback)
        return lambda: self.unsubscribe(event_type, callback)

    def unsubscribe(self, event_type: str, callback):
        with self._lock:
            callbacks = self._subscribers.get(event_type, [])
            if callback in callbacks:
                callbacks.remove(callback)

    def publish(self, event_type: str, payload: dict):
        with self._lock:
            callbacks = list(self._subscribers.get(event_type, []))
        for callback in callbacks:
            try:
                callback(payload)
            except Exception:
                logger.exception("Error en suscriptor de %s", event_type)
