"""Servidor web (API + interfaz) de la biblioteca.

Arranque: `python -m web` (ver web/__main__.py). Usa un único proceso: los
jobs corren en hilos del propio servidor, así que no debe lanzarse con varios
workers de uvicorn.

Las rutas están en web/routes/, una por área; aquí solo se monta la app, el
arranque en segundo plano y la caché de la biblioteca.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.database.db_manager import DatabaseManager
from core.library.roots import sync_library_roots
from core.repositories import LibraryRepository
from web.jobs import JobManager
from web.periodic import PeriodicRunner
from web.routes import calendar, discovery, items, library, settings, setup, system
from web.routes.settings import autostart_enabled

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
LIBRARY_CACHE_SECONDS = 30


class _LibraryCache:
    """La consulta de biblioteca tarda ~2 s con la BD en el NAS: se cachea y
    se invalida cuando un job avisa de cambios."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: list[dict] | None = None
        self._loaded_at = 0.0

    def get(self) -> list[dict]:
        with self._lock:
            if self._items is not None and time.monotonic() - self._loaded_at < LIBRARY_CACHE_SECONDS:
                return self._items
        items = LibraryRepository(DatabaseManager()).list_items()
        with self._lock:
            self._items, self._loaded_at = items, time.monotonic()
        return items

    def invalidate(self, *_):
        with self._lock:
            self._items = None


@dataclass
class AppContext:
    """Lo que comparten las rutas: jobs en marcha, tareas programadas y caché."""

    jobs: JobManager
    periodic: PeriodicRunner
    library_cache: _LibraryCache


def create_app(job_manager: JobManager | None = None, *, init_db: bool = True) -> FastAPI:
    jobs = job_manager or JobManager()
    ctx = AppContext(jobs=jobs, periodic=PeriodicRunner(jobs), library_cache=_LibraryCache())
    jobs.add_listener(
        lambda msg: ctx.library_cache.invalidate() if msg.get("type") in ("series_updated", "job_finished") else None)

    def prepare_database():
        # Antes de servir: la reubicación de bibliotecas cambia rutas de la BD.
        db = DatabaseManager()
        db.initialize_database()
        sync_library_roots(db)

    def start_background():
        # Ya con el servidor respondiendo (la BD puede estar en un NAS).
        try:
            if autostart_enabled():
                if not setup.setup_status()["complete"]:
                    logger.info("Arranque automático del ciclo omitido: falta completar la configuración inicial")
                elif LibraryRepository(DatabaseManager()).path_health()["ok"]:
                    jobs.start("scheduler", wait_for_interval=True)
                else:
                    logger.error("Arranque automático del ciclo omitido: las rutas de la BD no coinciden con las configuradas")
        except Exception:
            logger.exception("Error en el arranque automático del ciclo")
        if os.getenv("WEB_PERIODIC_TASKS", "true").lower() != "false":
            ctx.periodic.start()
        try:
            ctx.library_cache.get()  # la primera visita no espera a la consulta
        except Exception:
            logger.exception("No se pudo precargar la biblioteca")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if init_db:
            await asyncio.to_thread(prepare_database)
            threading.Thread(target=start_background, name="arranque", daemon=True).start()
        yield
        ctx.periodic.stop()
        await asyncio.to_thread(jobs.stop_all)

    app = FastAPI(title="Biblioteca", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.state.jobs = jobs
    app.state.library_cache = ctx.library_cache

    app.include_router(setup.router)
    for module in (library, items, calendar, discovery, settings, system):
        app.include_router(module.build(ctx))

    # --- interfaz -------------------------------------------------------
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        return FileResponse(STATIC_DIR / "index.html")

    return app
