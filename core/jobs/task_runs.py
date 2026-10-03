"""Última ejecución de cada tarea, guardada en la BD.

La comparten las tareas programadas de la web (web/periodic.py) y el ciclo
completo (core/scheduler.py): el ciclo también importa, busca novedades y
actualiza el calendario, y así ninguno repite lo que el otro acaba de hacer,
ni un reinicio relanza lo que se hizo hace poco.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from core.repositories.app_settings_repository import AppSettingsRepository

INTERVALS_KEY = "task_intervals"
LAST_RUN_KEY = "task_last_run"

# minutos; 0 = no se ejecuta sola
DEFAULT_INTERVALS = {
    "importer": 15,
    "quality_scan": 60,
    "discovery": 360,
    "agenda": 720,
}

# Leer-modificar-escribir del JSON: dos hilos a la vez se pisarían.
_lock = threading.Lock()


def _parse(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def last_runs(db) -> dict[str, datetime | None]:
    saved = AppSettingsRepository(db).get(LAST_RUN_KEY) or {}
    return {name: _parse(value) for name, value in saved.items()}


def record_run(db, name: str, when: datetime) -> None:
    """Guarda `when` como última ejecución de `name` (nunca retrocede)."""
    with _lock:
        settings = AppSettingsRepository(db)
        saved = settings.get(LAST_RUN_KEY) or {}
        previous = _parse(saved.get(name))
        if previous is None or when > previous:
            saved[name] = when.isoformat(timespec="seconds")
            settings.set(LAST_RUN_KEY, saved)


def intervals(db) -> dict[str, int]:
    saved = AppSettingsRepository(db).get(INTERVALS_KEY) or {}
    return {name: int(saved.get(name, default)) for name, default in DEFAULT_INTERVALS.items()}


def ran_recently(db, name: str, now: datetime | None = None) -> bool:
    """¿Se ejecutó `name` hace menos de su intervalo? (intervalo 0 = nunca)."""
    minutes = intervals(db).get(name, 0)
    last = last_runs(db).get(name)
    if minutes <= 0 or last is None:
        return False
    return (now or datetime.now()) - last < timedelta(minutes=minutes)
