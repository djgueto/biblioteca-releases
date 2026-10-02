from __future__ import annotations

from core.events import (
    SERIES_UPDATED,
    TASK_ERROR,
    TASK_FINISHED,
    TASK_LOG,
    TASK_PROGRESS,
    EventBus,
)


class BackgroundJob:
    """Tarea larga sin dependencias de UI.

    Comunica progreso, logs y resultados publicando en `event_bus`; quien la
    ejecute (QThread en la app de escritorio, un hilo del backend web) se
    suscribe al bus y lo traduce a su propio mecanismo. Todos los payloads
    llevan `source=self.name`, para distinguir de qué job vienen cuando varios
    comparten bus, y de los eventos que publican los servicios internos.
    """

    name = "job"

    def __init__(self, event_bus: EventBus | None = None):
        self.event_bus = event_bus or EventBus()
        self.running = True

    def run(self):
        raise NotImplementedError

    def stop(self):
        self.running = False

    def _emit(self, event_type: str, **payload):
        self.event_bus.publish(event_type, {"source": self.name, **payload})

    def _log(self, message: str):
        self._emit(TASK_LOG, message=message)

    def _progress(self, percent: int, message: str):
        self._emit(TASK_PROGRESS, percent=percent, message=message)

    def _finished(self, **result):
        self._emit(TASK_FINISHED, result=result)

    def _error(self, error: str):
        self._emit(TASK_ERROR, error=error)

    def _series_updated(self, series_id: int):
        self._emit(SERIES_UPDATED, series_id=series_id)
