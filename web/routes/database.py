"""Exportar / importar la base de datos (Sistema → Base de datos)."""

from __future__ import annotations

import logging
import os
import socket
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from config.settings import DATA_DIR, Settings
from core.database import transfer
from web import runtime

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_IMPORT_BYTES = 4 * 1024 ** 3


def _restart():
    """Docker: terminar basta (restart: unless-stopped lo vuelve a arrancar)."""
    if runtime.is_docker():
        def _exit():
            time.sleep(0.8)  # que la respuesta llegue al navegador
            os._exit(0)

        threading.Thread(target=_exit, name="reinicio", daemon=True).start()
    else:
        runtime.restart()


@router.get("/api/system/database/export")
def export_database():
    try:
        copy = transfer.export_copy(Settings.DB_PATH)
    except Exception as e:
        logger.exception("No se pudo exportar la base de datos")
        raise HTTPException(500, f"No se pudo exportar la base de datos: {e}")
    host = "".join(c for c in socket.gethostname() if c.isalnum() or c in "-_") or "biblioteca"
    name = f"biblioteca-{host}-{datetime.now():%Y%m%d-%H%M}.db"
    return FileResponse(copy, filename=name, media_type="application/vnd.sqlite3",
                        background=BackgroundTask(lambda: copy.unlink(missing_ok=True)))


@router.post("/api/system/database/import")
async def import_database(request: Request):
    """Cuerpo: el archivo .db tal cual. Se valida, se deja preparado y la app
    se reinicia para aplicarlo."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="biblioteca-import-", suffix=".db", dir=DATA_DIR)
    upload = Path(name)
    size = 0
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise HTTPException(413, "El archivo es demasiado grande")
                f.write(chunk)
        if not size:
            raise HTTPException(422, "No se ha recibido ningún archivo")
        try:
            summary = transfer.stage_import(upload, Settings.DB_PATH)
        except ValueError as e:
            raise HTTPException(422, str(e))
    finally:
        upload.unlink(missing_ok=True)
    logger.info(f"Importación de base de datos preparada ({summary['series']} fichas); reiniciando")
    _restart()
    return {"ok": True, **summary}
