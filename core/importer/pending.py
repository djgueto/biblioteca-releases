"""Qué hay ya en las carpetas de descargas (terminado o a medias).

Sirve para no volver a pedir algo que ya se descargó pero todavía no se ha
pasado a la biblioteca (a mano, con otro programa o con la importación).
Usa las carpetas de descargas de Ajustes → Importación, esté la importación
activada o no.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from core.importer.service import (
    _CUT_TOKENS,
    _YEAR,
    MATCH_THRESHOLD,
    VIDEO_EXTENSIONS,
    ImportSettings,
    _clean_name,
    _comparable,
    _episode_title_part,
    iter_download_files,
    parse_release_episode,
)
from core.wolfmax.category_scanner import _title_score

logger = logging.getLogger(__name__)

CACHE_SECONDS = 300
STILL_WRITING_SECONDS = 600
_PARTIAL = re.compile(r"\.(part|!qb|crdownload|tmp|importing)$", re.IGNORECASE)


@dataclass(frozen=True)
class DownloadedFile:
    path: str
    name: str
    size: int
    title: str  # comparable
    season: int | None
    episode: int | None
    year: int | None
    partial: bool = False  # a medias (.part, .!qb...)


class DownloadsIndex:
    def __init__(self, source_dirs: list[str]):
        self.files: list[DownloadedFile] = []
        self.names: set[str] = set()
        self.sizes: set[int] = set()
        for source in source_dirs:
            base = Path(source)
            if not base.is_dir():
                continue
            for path in iter_download_files(base):
                try:
                    if not path.is_file():
                        continue
                    name = _PARTIAL.sub("", path.name)  # una descarga a medias también cuenta
                    if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
                        continue
                    stat = path.stat()
                    size = stat.st_size
                except OSError:
                    continue
                # Como en la importación: sin cambios en un rato = terminado.
                partial = name != path.name or stat.st_mtime > time.time() - STILL_WRITING_SECONDS
                stem = Path(name).stem
                found = parse_release_episode(stem)
                if found:
                    start, season, episode = found
                    title, year = _episode_title_part(stem[:start]), None
                else:
                    clean = _clean_name(stem)
                    year_match = _YEAR.search(clean)
                    cut = _CUT_TOKENS.search(clean)
                    end = min(year_match.start() if year_match else len(clean), cut.start() if cut else len(clean))
                    title, season, episode = clean[:end], None, None
                    year = int(year_match.group(1)) if year_match else None
                self.files.append(DownloadedFile(str(path), name.lower(), size,
                                                 _comparable(_clean_name(title)), season, episode, year,
                                                 partial=partial))
                self.names.add(name.lower())
                self.sizes.add(size)

    def _matches(self, f: DownloadedFile, series: dict) -> bool:
        names = [n for n in (series.get("tmdb_name"), series.get("name")) if n]
        return any(_title_score(f.title, _comparable(n)) >= MATCH_THRESHOLD for n in names)

    def episodes_for(self, series: dict) -> set[tuple[int, int]]:
        return {(f.season, f.episode) for f in self.files if f.season is not None and self._matches(f, series)}

    def episode_progress(self, series: dict) -> dict[tuple[int, int], bool]:
        """(temporada, episodio) -> ¿terminado? (si hay una copia terminada y
        otra a medias, cuenta la terminada)."""
        found: dict[tuple[int, int], bool] = {}
        for f in self.files:
            if f.season is not None and self._matches(f, series):
                key = (f.season, f.episode)
                found[key] = found.get(key, False) or not f.partial
        return found

    def has_movie(self, series: dict) -> bool:
        year = series.get("year")
        return any(
            f.season is None and self._matches(f, series)
            and not (year and f.year and abs(f.year - int(year)) > 1)
            for f in self.files
        )

    def has_ed2k_file(self, link: str) -> bool:
        """¿Está ya en descargas el archivo de este enlace ed2k (mismo nombre o
        mismo tamaño exacto)? ed2k://|file|NOMBRE|TAMAÑO|HASH|/"""
        parts = link.split("|")
        if len(parts) < 4:
            return False
        name, size = parts[2].lower(), parts[3]
        return name in self.names or (size.isdigit() and int(size) in self.sizes)


_cache: dict[tuple, tuple[float, DownloadsIndex]] = {}
_lock = threading.Lock()


def downloads_index(db, *, max_age: float = CACHE_SECONDS) -> DownloadsIndex:
    """Índice de las carpetas de descargas configuradas (en caché unos
    minutos: recorrer una carpeta en red en cada consulta sería lento)."""
    dirs = tuple(ImportSettings.load(db).source_dirs)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(dirs)
        if cached and now - cached[0] < max_age:
            return cached[1]
    index = DownloadsIndex(list(dirs))
    with _lock:
        _cache[dirs] = (now, index)
    return index


def clear_cache():
    with _lock:
        _cache.clear()
