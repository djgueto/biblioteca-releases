"""Notas de IMDb.

IMDb no tiene API pública. Publica cada día sus notas oficiales en
https://datasets.imdbws.com/title.ratings.tsv.gz (≈8 MB, ~1,5 millones de
títulos): se descarga como mucho una vez al día y se vuelca a una SQLite
local en la carpeta de caché para consultar al instante por identificador
(tt1234567). Si hay clave de OMDb, se usa como respaldo para títulos que aún
no están en el volcado (estrenos de los últimos días).
"""

from __future__ import annotations

import gzip
import io
import logging
import os
import sqlite3
import time
from pathlib import Path

import requests

from config.settings import Settings

logger = logging.getLogger(__name__)

DATASET_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
REFRESH_SECONDS = 24 * 3600


def cache_dir() -> Path:
    return Path(Settings.COVER_CACHE_DIR).parent / "imdb"


class ImdbRatings:
    def __init__(self, directory: Path | None = None, *, omdb_api_key: str | None = None, session=None):
        self.directory = Path(directory or cache_dir())
        self.db_path = self.directory / "ratings.db"
        self.omdb_api_key = Settings.OMDB_API_KEY if omdb_api_key is None else omdb_api_key
        self.session = session or requests.Session()
        self._omdb_cache: dict[str, tuple[float, int] | None] = {}

    # --- volcado oficial ----------------------------------------------------
    def dataset_age_seconds(self) -> float | None:
        try:
            return time.time() - self.db_path.stat().st_mtime
        except OSError:
            return None

    def ensure_dataset(self, *, force: bool = False) -> bool:
        """Descarga y vuelca el archivo de notas si no existe o tiene más de
        un día. Devuelve True si hay datos utilizables."""
        age = self.dataset_age_seconds()
        if not force and age is not None and age < REFRESH_SECONDS:
            return True
        try:
            response = self.session.get(DATASET_URL, timeout=120)
            response.raise_for_status()
            self._load(response.content)
            logger.info("Notas de IMDb actualizadas")
            return True
        except Exception as e:
            logger.warning(f"No se pudieron actualizar las notas de IMDb: {e}")
            return age is not None  # mejor datos de ayer que ninguno

    def _load(self, gz_bytes: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp_path = self.db_path.with_suffix(".tmp")
        tmp_path.unlink(missing_ok=True)
        conn = sqlite3.connect(tmp_path)
        try:
            conn.execute("PRAGMA journal_mode=OFF")
            conn.execute("PRAGMA synchronous=OFF")
            conn.execute("CREATE TABLE ratings (tconst TEXT PRIMARY KEY, rating REAL, votes INTEGER) WITHOUT ROWID")
            with gzip.open(io.BytesIO(gz_bytes), "rt", encoding="utf-8") as f:
                next(f)  # cabecera: tconst averageRating numVotes
                batch = []
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) == 3:
                        batch.append((parts[0], float(parts[1]), int(parts[2])))
                    if len(batch) >= 50000:
                        conn.executemany("INSERT OR REPLACE INTO ratings VALUES (?, ?, ?)", batch)
                        batch.clear()
                conn.executemany("INSERT OR REPLACE INTO ratings VALUES (?, ?, ?)", batch)
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp_path, self.db_path)

    # --- consulta -------------------------------------------------------------
    def get(self, imdb_id: str | None) -> tuple[float, int] | None:
        """→ (nota, votos) o None si IMDb aún no tiene nota para ese título."""
        if not imdb_id:
            return None
        if self.db_path.exists():
            conn = sqlite3.connect(self.db_path)
            try:
                row = conn.execute("SELECT rating, votes FROM ratings WHERE tconst = ?", (imdb_id,)).fetchone()
            finally:
                conn.close()
            if row:
                return float(row[0]), int(row[1])
        return self._from_omdb(imdb_id)

    def _from_omdb(self, imdb_id: str) -> tuple[float, int] | None:
        if not self.omdb_api_key:
            return None
        if imdb_id in self._omdb_cache:
            return self._omdb_cache[imdb_id]
        result = None
        try:
            data = self.session.get("https://www.omdbapi.com/", params={"apikey": self.omdb_api_key, "i": imdb_id},
                                    timeout=15).json()
            rating, votes = data.get("imdbRating"), (data.get("imdbVotes") or "").replace(",", "")
            if data.get("Response") == "True" and rating not in (None, "N/A") and votes.isdigit():
                result = (float(rating), int(votes))
        except Exception as e:
            logger.debug(f"OMDb sin respuesta para {imdb_id}: {e}")
        self._omdb_cache[imdb_id] = result
        return result
