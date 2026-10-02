"""Servidor web (API + interfaz) de la biblioteca.

Arranque: `python -m web` (ver web/__main__.py). Usa un único proceso: los
jobs corren en hilos del propio servidor, así que no debe lanzarse con varios
workers de uvicorn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.settings import Settings
from core.covers import get_cover_bytes
from core.database.db_manager import DatabaseManager
from core.download_clients import ED2K, TORRENT, build_download_dispatcher
from core.library.roots import LibraryRoots, sync_library_roots
from core.repositories import DecisionLogRepository, LibraryRepository, SeriesRepository, SourceOfferRepository
from web.jobs import JobAlreadyRunning, JobManager

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
LIBRARY_CACHE_SECONDS = 30


class MonitoredUpdate(BaseModel):
    monitored: bool


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


def _serialize_offer(offer) -> dict:
    return {
        "provider": offer.provider,
        "source_url": offer.source_url,
        "resolution": offer.quality.resolution_height,
        "codec": offer.quality.codec,
        "hdr": offer.quality.hdr.name.lower(),
        "match_score": offer.match_score,
        "available": offer.available,
    }


def create_app(job_manager: JobManager | None = None, *, init_db: bool = True) -> FastAPI:
    jobs = job_manager or JobManager()
    library_cache = _LibraryCache()
    jobs.add_listener(lambda msg: library_cache.invalidate() if msg.get("type") in ("series_updated", "job_finished") else None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if init_db:
            db = DatabaseManager()
            db.initialize_database()
            sync_library_roots(db)
        if os.getenv("WEB_SCHEDULER_AUTOSTART", "false").lower() == "true":
            if LibraryRepository(DatabaseManager()).path_health()["ok"]:
                jobs.start("scheduler")
            else:
                logger.error("WEB_SCHEDULER_AUTOSTART ignorado: las rutas de la BD no coinciden con las configuradas")
        yield
        await asyncio.to_thread(jobs.stop_all)

    app = FastAPI(title="Biblioteca", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.state.jobs = jobs

    # --- biblioteca -----------------------------------------------------
    @app.get("/api/categories")
    def categories():
        items = library_cache.get()
        counts: dict[str, int] = {}
        for item in items:
            counts[item["content_category"]] = counts.get(item["content_category"], 0) + 1
        return [
            {"id": key, "label": cfg["label"], "content_mode": cfg["content_mode"], "count": counts.get(key, 0)}
            for key, cfg in Settings.get_library_categories().items()
        ]

    @app.get("/api/library")
    def library():
        return library_cache.get()

    @app.get("/api/series/{series_id}")
    def series_detail(series_id: int):
        db = DatabaseManager()
        repo = LibraryRepository(db)
        item = repo.get_item(series_id)
        if item is None:
            raise HTTPException(404, "No existe")

        seasons: dict[int, dict] = {}
        for ep in db.get_local_episodes(series_id):
            season = seasons.setdefault(ep["season"], {"season": ep["season"], "local": [], "missing": []})
            season["local"].append({k: ep.get(k) for k in ("episode", "filename", "resolution_height", "codec", "hdr", "size_mb")})
        for ep in db.get_missing_episodes_for_series(series_id):
            season = seasons.setdefault(ep["season"], {"season": ep["season"], "local": [], "missing": []})
            season["missing"].append({k: ep.get(k) for k in ("episode", "name", "air_date")})

        item["seasons"] = [seasons[k] for k in sorted(seasons)]
        item["offers"] = [_serialize_offer(o) for o in SourceOfferRepository(db).get_all_for_series(series_id)]
        item["history"] = DecisionLogRepository(db).list_for_series(series_id, limit=30)
        return item

    @app.get("/api/series/{series_id}/cover")
    def series_cover(series_id: int):
        db = DatabaseManager()
        repo = LibraryRepository(db)
        item = db.get_series_by_id(series_id)
        cover_url = repo.cover_url(dict(item)) if item else ""
        data = get_cover_bytes(series_id, cover_url) if cover_url else None
        if not data:
            raise HTTPException(404, "Sin carátula")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

    @app.put("/api/series/{series_id}/monitored")
    def set_monitored(series_id: int, body: MonitoredUpdate):
        db = DatabaseManager()
        SeriesRepository(db).set_monitored(series_id, body.monitored)
        DecisionLogRepository(db).append(
            series_id,
            "monitored_changed",
            "Serie monitorizada" if body.monitored else "Serie marcada como no monitorizada",
            {"monitored": body.monitored, "origin": "web"},
        )
        library_cache.invalidate()
        return {"id": series_id, "monitored": body.monitored}

    @app.get("/api/wanted")
    def wanted():
        return LibraryRepository(DatabaseManager()).list_missing()

    # --- sistema --------------------------------------------------------
    @app.get("/api/status")
    def status():
        dispatcher = build_download_dispatcher()
        return {
            "jobs": jobs.state(),
            "download_clients": [
                {"protocol": TORRENT, "label": "Torrent (carpeta vigilada)",
                 "configured": dispatcher.can_handle(TORRENT), "target": Settings.get_torrent_watch_dir()},
                {"protocol": ED2K, "label": "eMule / aMule",
                 "configured": dispatcher.can_handle(ED2K), "target": Settings.EMULE_URL},
            ],
            "providers": [
                {"name": "hispashare", "label": "Hispashare", "protocol": ED2K,
                 "enabled": bool(Settings.HISPASHARE_USER)},
                {"name": "wolfmax", "label": "Wolfmax4k", "protocol": TORRENT,
                 "enabled": bool(getattr(Settings, "WOLFMAX_UPGRADE_ENABLED", False))},
            ],
            "tmdb_configured": bool(Settings.TMDB_API_KEY),
            "library_paths": LibraryRepository(DatabaseManager()).path_health(),
            "library_roots": [
                {"category": r.category, "label": Settings.get_category_label(r.category),
                 "path": r.path, "accessible": Path(r.path).exists()}
                for r in LibraryRoots(DatabaseManager()).configured()
            ],
            "check_interval_hours": Settings.CHECK_INTERVAL_HOURS,
        }

    @app.get("/api/health", include_in_schema=False)
    def health():
        return {"ok": True}

    @app.get("/api/logs")
    def logs(limit: int = 300):
        return jobs.recent_logs(limit)

    @app.post("/api/jobs/{name}/start")
    def start_job(name: str):
        if name == "scheduler":
            health = LibraryRepository(DatabaseManager()).path_health()
            if not health["ok"]:
                raise HTTPException(
                    409,
                    f"Solo {health['in_configured_roots']} de {health['total']} elementos de la base de datos "
                    "están dentro de las rutas de biblioteca configuradas. Un ciclo crearía duplicados: "
                    "revisa las rutas (o migra las de la BD) antes de iniciarlo.",
                )
        try:
            jobs.start(name)
        except KeyError:
            raise HTTPException(404, "Tarea desconocida")
        except JobAlreadyRunning:
            raise HTTPException(409, "La tarea ya está en marcha")
        return jobs.state()

    @app.post("/api/jobs/{name}/stop")
    def stop_job(name: str):
        try:
            jobs.stop(name)
        except KeyError:
            raise HTTPException(404, "Tarea desconocida")
        return jobs.state()

    @app.websocket("/api/ws")
    async def events(ws: WebSocket):
        await ws.accept()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)

        def enqueue(message: dict):
            # Llamado desde los hilos de los jobs.
            def put():
                if not queue.full():
                    queue.put_nowait(message)
            loop.call_soon_threadsafe(put)

        remove = jobs.add_listener(enqueue)
        try:
            await ws.send_text(json.dumps({"type": "hello", "jobs": jobs.state()}))
            while True:
                await ws.send_text(json.dumps(await queue.get(), ensure_ascii=False))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            remove()

    # --- interfaz -------------------------------------------------------
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        return FileResponse(STATIC_DIR / "index.html")

    return app
