"""Tareas programadas de la web (como las de Radarr/Sonarr).

Cada tarea tiene su propio intervalo, independiente del ciclo completo
(búsqueda y descargas, que sigue su CHECK_INTERVAL_HOURS): importar lo
descargado cada pocos minutos no debe esperar 12 horas. La hora de la última
ejecución se guarda en la BD para que al reiniciar no se lance todo a la vez.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Callable

from core.database.db_manager import DatabaseManager
from core.jobs import task_runs
from core.jobs.task_runs import DEFAULT_INTERVALS, INTERVALS_KEY
from core.repositories.app_settings_repository import AppSettingsRepository

logger = logging.getLogger(__name__)

TICK_SECONDS = 30
STARTUP_DELAY_MINUTES = 5

LABELS = {
    "importer": "Importar descargas",
    "quality_scan": "Analizar calidad",
    "discovery": "Buscar novedades",
    "agenda": "Actualizar calendario",
}


def _now() -> datetime:
    return datetime.now()


def _default_conditions() -> dict[str, Callable[[], bool]]:
    """Tareas que dependen de un interruptor: si está apagado no se lanzan
    (no tiene sentido llenar el registro de "importación desactivada")."""
    def importer_enabled():
        from core.importer.service import ImportSettings

        return ImportSettings.load(DatabaseManager()).enabled

    def discovery_enabled():
        from core.discovery.rules import DiscoverySettings

        return DiscoverySettings.load(DatabaseManager()).enabled

    return {"importer": importer_enabled, "discovery": discovery_enabled}


class PeriodicRunner:
    def __init__(self, jobs, *, conditions: dict[str, Callable[[], bool]] | None = None,
                 now: Callable[[], datetime] = _now, db_factory=DatabaseManager):
        self.jobs = jobs
        self.conditions = conditions if conditions is not None else _default_conditions()
        self.now = now
        self.db_factory = db_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = now()
        jobs.add_listener(self._on_message)

    # --- ajustes --------------------------------------------------------------
    def _settings(self):
        return AppSettingsRepository(self.db_factory())

    def intervals(self) -> dict[str, int]:
        return {name: minutes for name, minutes in task_runs.intervals(self.db_factory()).items()
                if name in self.jobs.factories}

    def set_intervals(self, values: dict[str, int]) -> dict[str, int]:
        current = self.intervals()
        for name, minutes in values.items():
            if name not in current:
                raise KeyError(name)
            current[name] = max(0, int(minutes))
        self._settings().set(INTERVALS_KEY, current)
        return current

    def last_runs(self) -> dict[str, datetime | None]:
        saved = task_runs.last_runs(self.db_factory())
        return {name: saved.get(name) for name in DEFAULT_INTERVALS}

    def _record_run(self, name: str, when: datetime) -> None:
        task_runs.record_run(self.db_factory(), name, when)

    def next_runs(self) -> dict[str, datetime | None]:
        last = self.last_runs()
        result = {}
        for name, minutes in self.intervals().items():
            if minutes <= 0:
                result[name] = None
            elif last.get(name):
                result[name] = last[name] + timedelta(minutes=minutes)
            else:
                # Primera vez: no todo a la vez al arrancar.
                result[name] = self._started_at + timedelta(minutes=STARTUP_DELAY_MINUTES)
        return result

    def status(self) -> list[dict]:
        last, nxt, intervals = self.last_runs(), self.next_runs(), self.intervals()
        out = []
        for name, minutes in intervals.items():
            condition = self.conditions.get(name)
            out.append({
                "name": name, "label": LABELS.get(name, name), "interval_minutes": minutes,
                "last_run": last[name].isoformat(timespec="seconds") if last.get(name) else None,
                "next_run": nxt[name].isoformat(timespec="seconds") if nxt.get(name) else None,
                "enabled": minutes > 0 and (condition is None or _safe(condition)),
            })
        return out

    # --- bucle ----------------------------------------------------------------
    def tick(self) -> list[str]:
        """Lanza las tareas que tocan. Devuelve las lanzadas (para tests)."""
        started = []
        now = self.now()
        running = {j["name"] for j in self.jobs.state() if j["running"]}
        for name, due in self.next_runs().items():
            if due is None or due > now or name in running:
                continue
            condition = self.conditions.get(name)
            if condition is not None and not _safe(condition):
                continue
            try:
                self.jobs.start(name)
            except Exception as e:  # ya en marcha, etc.
                logger.debug(f"Tarea programada {name} no lanzada: {e}")
                continue
            self._record_run(name, now)
            started.append(name)
        return started

    def _on_message(self, message: dict):
        # Las ejecuciones a mano también cuentan: la siguiente se programa
        # desde la última, se lanzara como se lanzara.
        # No se exige running=True: una tarea rápida puede haber terminado
        # antes de que salga el aviso de inicio.
        job = message.get("job") if message.get("type") == "job" else None
        if not (isinstance(job, dict) and job.get("name") in DEFAULT_INTERVALS and job.get("started_at")):
            return
        try:
            self._record_run(job["name"], datetime.fromisoformat(job["started_at"]))
        except Exception:
            pass

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="tareas-programadas", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(TICK_SECONDS):
            try:
                self.tick()
            except Exception:
                logger.exception("Error en las tareas programadas")


def _safe(condition: Callable[[], bool]) -> bool:
    try:
        return bool(condition())
    except Exception:
        return False
