"""Análisis de calidad con MediaInfo de toda la biblioteca.

Solo cuenta lo que se lee del propio archivo (resolución, códec, HDR): lo
que diga el nombre no es fiable. Lo usan el ciclo (por tandas) y la tarea
"Analizar calidad" (hasta terminar).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from config.settings import Settings
from core.library.item_quality import detect_item_quality
from core.library.parser import detect_resolution_from_file, mediainfo_available
from core.repositories.item_quality_repository import ItemQualityRepository

logger = logging.getLogger(__name__)

DEFAULT_WORKERS = 6


class MediaInfoUnavailable(RuntimeError):
    pass


def _ensure_mediainfo():
    if not mediainfo_available():
        # Sin MediaInfo todo saldría "ilegible": mejor no tocar nada y avisar.
        raise MediaInfoUnavailable("MediaInfo no está disponible (instala pymediainfo y la librería MediaInfo)")


def measure_episodes(db, *, limit: int, workers: int = DEFAULT_WORKERS, should_stop=lambda: False) -> dict:
    """Mide hasta `limit` episodios pendientes. → {measured, unreadable, pending}."""
    _ensure_mediainfo()
    episodes = db.get_episodes_missing_resolution(limit=limit)
    measured, unreadable = [], []
    if episodes:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [(ep, pool.submit(detect_resolution_from_file, ep["file_path"])) for ep in episodes]
            for ep, future in futures:
                if should_stop():
                    pool.shutdown(cancel_futures=True)
                    break
                try:
                    result = future.result()
                except Exception:
                    result = None
                (measured if result else unreadable).append({"id": ep["id"], **(result or {})})
        # Las escrituras van en este hilo: la conexión SQLite es por hilo.
        db.batch_update_episode_resolutions(measured)
        db.mark_episode_resolution_unreadable([e["id"] for e in unreadable])
    return {"measured": len(measured), "unreadable": len(unreadable),
            "pending": bool(db.get_episodes_missing_resolution(limit=1))}


def measure_items(db, *, limit: int, workers: int = DEFAULT_WORKERS, should_stop=lambda: False) -> dict:
    """Mide hasta `limit` películas/documentales/conciertos pendientes."""
    _ensure_mediainfo()
    repo = ItemQualityRepository(db)
    categories = Settings.get_single_file_categories()
    pending = repo.pending(categories, limit=limit)
    measured = unreadable = 0
    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [(item, pool.submit(detect_item_quality, item["folder_path"])) for item in pending]
            for item, future in futures:
                if should_stop():
                    pool.shutdown(cancel_futures=True)
                    break
                try:
                    file_path, quality, source = future.result()
                except Exception as e:
                    logger.warning(f"No se pudo medir '{item['name']}': {e}")
                    file_path, quality, source = None, None, None
                repo.save(item["id"], item["folder_path"], file_path=file_path, quality=quality, source=source)
                if quality:
                    measured += 1
                else:
                    unreadable += 1
    return {"measured": measured, "unreadable": unreadable,
            "pending": bool(repo.pending(categories, limit=1))}


def coverage(db) -> dict:
    episodes = db.count_episode_quality()
    items = ItemQualityRepository(db).counts(Settings.get_single_file_categories())
    return {"episodes": episodes, "items": items}
