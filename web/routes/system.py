"""Estado del sistema, jobs (iniciar/detener), registro y eventos en directo."""

from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.download_clients import ED2K, TORRENT, build_download_dispatcher
from core.library.parser import mediainfo_available
from core.library.quality_scan import coverage as quality_coverage
from core.library.roots import LibraryRoots
from core.repositories import LibraryRepository
from web.jobs import JobAlreadyRunning


def build(ctx) -> APIRouter:
    router = APIRouter()
    jobs = ctx.jobs

    @router.get("/api/status")
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
                {"name": "dontorrent", "label": f"DonTorrent ({Settings.DONTORRENT_URL})", "protocol": TORRENT,
                 "enabled": Settings.DONTORRENT_ENABLED},
            ],
            "tmdb_configured": bool(Settings.TMDB_API_KEY),
            "library_paths": LibraryRepository(DatabaseManager()).path_health(),
            "quality_coverage": quality_coverage(DatabaseManager()),
            "mediainfo_available": mediainfo_available(),
            "library_roots": [
                {"category": r.category, "label": Settings.get_category_label(r.category),
                 "path": r.path, "accessible": os.path.exists(r.path)}
                for r in LibraryRoots(DatabaseManager()).configured()
            ],
            "check_interval_hours": Settings.CHECK_INTERVAL_HOURS,
            "version": Settings.APP_VERSION,
            "data_dir": Settings.DATA_DIR,
        }

    @router.get("/api/health", include_in_schema=False)
    def health():
        return {"ok": True}

    @router.get("/api/logs")
    def logs(limit: int = 300):
        return jobs.recent_logs(limit)

    @router.post("/api/jobs/{name}/start")
    def start_job(name: str):
        if name == "source_search":
            raise HTTPException(422, "Se lanza desde la ficha del elemento")
        if name in ("scheduler", "library_scan"):
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

    @router.post("/api/jobs/{name}/stop")
    def stop_job(name: str):
        try:
            jobs.stop(name)
        except KeyError:
            raise HTTPException(404, "Tarea desconocida")
        return jobs.state()

    @router.websocket("/api/ws")
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

    return router
