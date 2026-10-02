"""Ejecución de los jobs de core/jobs en el servidor web.

Equivalente web de ui/workers/job_threads.py: cada job corre en un hilo
propio y sus eventos se convierten en mensajes JSON que se reenvían a los
navegadores conectados (WebSocket). También guarda el estado actual y los
últimos logs para quien abra la página a mitad de un ciclo.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Callable

from core.events import (
    SERIES_UPDATED,
    SOURCE_URL_SAVED,
    TASK_ERROR,
    TASK_FINISHED,
    TASK_LOG,
    TASK_PROGRESS,
    TMDB_MATCHED,
    EventBus,
)
from core.jobs import BackgroundJob

logger = logging.getLogger(__name__)

JOB_LABELS = {
    "scheduler": "Ciclo automático",
    "tmdb_recalc": "Recalcular TMDB",
}


def _default_factories() -> dict[str, Callable[[EventBus], BackgroundJob]]:
    from core.scheduler import Scheduler
    from core.tmdb.recalc_job import TMDBRecalcJob

    return {
        "scheduler": lambda bus: Scheduler(event_bus=bus),
        "tmdb_recalc": lambda bus: TMDBRecalcJob(event_bus=bus),
    }


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JobAlreadyRunning(RuntimeError):
    pass


class JobManager:
    def __init__(self, factories: dict[str, Callable[[EventBus], BackgroundJob]] | None = None, log_size: int = 1000):
        self.factories = factories if factories is not None else _default_factories()
        self.logs: deque[dict] = deque(maxlen=log_size)
        self._lock = threading.Lock()
        self._jobs: dict[str, BackgroundJob] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._listeners: list[Callable[[dict], None]] = []
        self._state = {
            name: {"name": name, "label": JOB_LABELS.get(name, name), "running": False, "stopping": False,
                   "percent": 0, "message": "", "started_at": None, "finished_at": None, "last_error": None}
            for name in self.factories
        }

    # --- consulta -----------------------------------------------------
    def state(self) -> list[dict]:
        with self._lock:
            return [dict(s) for s in self._state.values()]

    def recent_logs(self, limit: int = 300) -> list[dict]:
        with self._lock:
            return list(self.logs)[-limit:]

    # --- suscripción (WebSocket) ----------------------------------------
    def add_listener(self, listener: Callable[[dict], None]):
        with self._lock:
            self._listeners.append(listener)
        return lambda: self._remove_listener(listener)

    def _remove_listener(self, listener):
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _broadcast(self, message: dict):
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(message)
            except Exception:
                logger.exception("Error notificando a un cliente web")

    # --- control ------------------------------------------------------
    def start(self, name: str):
        if name not in self.factories:
            raise KeyError(name)
        with self._lock:
            if self._state[name]["running"]:
                raise JobAlreadyRunning(name)
            bus = EventBus()
            job = self.factories[name](bus)
            self._subscribe(bus, name)
            self._jobs[name] = job
            self._state[name].update(running=True, stopping=False, percent=0, message="Iniciando...",
                                     started_at=_now(), finished_at=None, last_error=None)
            thread = threading.Thread(target=self._run, args=(name, job), name=f"job-{name}", daemon=True)
            self._threads[name] = thread
        thread.start()
        self._broadcast_state(name)

    def stop(self, name: str):
        if name not in self.factories:
            raise KeyError(name)
        with self._lock:
            job = self._jobs.get(name)
            if job is not None and self._state[name]["running"]:
                self._state[name].update(stopping=True, message="Deteniendo...")
        if job is not None:
            job.stop()
        self._broadcast_state(name)

    def stop_all(self, timeout: float = 10):
        for name in list(self._jobs):
            self.stop(name)
        for thread in list(self._threads.values()):
            thread.join(timeout)

    def _run(self, name: str, job: BackgroundJob):
        try:
            job.run()
        except Exception as e:
            logger.exception(f"Job {name} terminó con error")
            self._on_error(name, {"error": str(e)})
        finally:
            with self._lock:
                self._state[name].update(running=False, stopping=False, finished_at=_now())
                self._jobs.pop(name, None)
            self._broadcast_state(name)

    # --- eventos del bus ----------------------------------------------
    def _subscribe(self, bus: EventBus, name: str):
        def only_mine(handler):
            def callback(payload):
                if payload.get("source") == name:
                    handler(name, payload)
            return callback

        bus.subscribe(TASK_PROGRESS, only_mine(self._on_progress))
        bus.subscribe(TASK_LOG, only_mine(self._on_log))
        bus.subscribe(TASK_ERROR, only_mine(self._on_error))
        bus.subscribe(TASK_FINISHED, only_mine(self._on_finished))
        bus.subscribe(SERIES_UPDATED, only_mine(self._on_series_updated))
        bus.subscribe(SOURCE_URL_SAVED, only_mine(self._on_series_updated))
        bus.subscribe(TMDB_MATCHED, only_mine(self._on_series_updated))

    def _on_progress(self, name, payload):
        with self._lock:
            self._state[name].update(percent=payload["percent"], message=payload["message"])
        self._broadcast_state(name)

    def _on_log(self, name, payload):
        entry = {"time": _now(), "job": name, "message": payload["message"]}
        with self._lock:
            self.logs.append(entry)
        self._broadcast({"type": "log", **entry})

    def _on_error(self, name, payload):
        with self._lock:
            self._state[name]["last_error"] = payload["error"]
        self._on_log(name, {"message": f"❌ {payload['error']}"})
        self._broadcast_state(name)

    def _on_finished(self, name, payload):
        self._broadcast({"type": "job_finished", "job": name, "result": payload.get("result") or {}})

    def _on_series_updated(self, name, payload):
        self._broadcast({"type": "series_updated", "series_id": payload["series_id"]})

    def _broadcast_state(self, name: str):
        with self._lock:
            state = dict(self._state[name])
        self._broadcast({"type": "job", "job": state})
