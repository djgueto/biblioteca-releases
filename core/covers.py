"""Caché de carátulas en disco, compartida por la app de escritorio y la web.

El nombre de archivo es el mismo que usa ui/cover_manager.py
(`<series_id>_<sha1(url)[:12]>.jpg`), así que ambas reutilizan lo ya descargado.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import requests

from config.settings import Settings

logger = logging.getLogger(__name__)


def cover_cache_path(series_id: int, cover_url: str) -> Path:
    url_hash = hashlib.sha1(cover_url.encode("utf-8")).hexdigest()[:12]
    return Path(Settings.COVER_CACHE_DIR) / f"{series_id}_{url_hash}.jpg"


def get_cover_bytes(series_id: int, cover_url: str, timeout: float = 15) -> bytes | None:
    """Devuelve la carátula desde la caché, descargándola si aún no está."""
    if not cover_url:
        return None
    path = cover_cache_path(series_id, cover_url)
    try:
        if path.exists():
            data = path.read_bytes()
            if data:
                return data
    except OSError as e:
        logger.debug(f"No se pudo leer carátula en caché {path}: {e}")

    try:
        response = requests.get(cover_url, timeout=timeout)
    except requests.RequestException as e:
        logger.debug(f"Error descargando carátula {cover_url}: {e}")
        return None
    if response.status_code != 200 or not response.content:
        return None

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
    except OSError as e:
        logger.debug(f"No se pudo guardar carátula {path}: {e}")
    return response.content
