"""Las tareas que escriben mucho en la biblioteca, de una en una.

El ciclo automático y las tareas programadas o manuales (importar, analizar
calidad, escanear, recalcular TMDB...) corrían a la vez y se pisaban: dos
escrituras largas sobre SQLite acaban en "database is locked" (y peor con la
BD en el NAS). Con este candado, la que llega segunda espera su turno.

Es un candado del proceso: entre servidores distintos ya está CycleLock.
"""

from __future__ import annotations

import threading
from typing import Callable

_lock = threading.Lock()
_holder: str | None = None


def holder() -> str | None:
    return _holder


def run_exclusive(job, fn: Callable[[], object], label: str):
    """Ejecuta fn() con el candado. Si lo tiene otra tarea, avisa y espera
    (sin dejar de atender a job.stop()). Devuelve lo que devuelva fn, o None
    si se detuvo la tarea mientras esperaba."""
    global _holder
    if not _lock.acquire(blocking=False):
        message = f"⏳ Esperando a que termine «{_holder or 'otra tarea'}»..."
        job._log(message)
        job._progress(0, message)
        while not _lock.acquire(timeout=1):
            if not job.running:
                return None
    _holder = label
    try:
        return fn()
    finally:
        _holder = None
        _lock.release()
