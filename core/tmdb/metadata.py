"""Metadatos de TMDB en caché: carátula, fondo, sinopsis, nota, votos e
identificador de IMDb.

Se guardan por (tmdb_id, media_type): TMDB numera películas y series por
separado, así que el mismo número puede ser dos obras distintas. Lo usan las
carátulas de la biblioteca y las novedades (lista de seguimiento).
"""

from __future__ import annotations

import json
import logging

from config.settings import Settings

logger = logging.getLogger(__name__)

IMAGE_BASE = "https://image.tmdb.org/t/p/"
MAX_AGE_DAYS = 30


def media_type_for_category(category: str | None) -> str:
    """Las bibliotecas de episodios se emparejan como series de TMDB y las de
    archivo único como películas (ver TMDBAutoMatcher.match_content)."""
    return "tv" if Settings.get_category_content_mode(category or "series") == "episodic" else "movie"


def image_url(path: str | None, size: str = "w342") -> str:
    return f"{IMAGE_BASE}{size}{path}" if path else ""


class TmdbMetaRepository:
    def __init__(self, db_manager):
        self.db = db_manager
        conn = self.db.get_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tmdb_meta (
                tmdb_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                title TEXT,
                original_title TEXT,
                release_date TEXT,
                poster_path TEXT,
                poster_other_language TEXT,
                backdrop_path TEXT,
                overview TEXT,
                vote_average REAL,
                vote_count INTEGER,
                imdb_id TEXT,
                genre_ids TEXT,
                status TEXT,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tmdb_id, media_type)
            )
        ''')
        columns = {r[1] for r in conn.execute("PRAGMA table_info(tmdb_meta)")}
        if "poster_other_language" not in columns:
            conn.execute("ALTER TABLE tmdb_meta ADD COLUMN poster_other_language TEXT")
        conn.commit()

    def get(self, tmdb_id: int, media_type: str, max_age_days: int | None = MAX_AGE_DAYS) -> dict | None:
        query = "SELECT * FROM tmdb_meta WHERE tmdb_id = ? AND media_type = ?"
        params: tuple = (tmdb_id, media_type)
        if max_age_days is not None:
            query += " AND fetched_at >= datetime('now', ?)"
            params += (f"-{int(max_age_days)} days",)
        row = self.db.get_connection().execute(query, params).fetchone()
        if not row:
            return None
        meta = dict(row)
        meta["genre_ids"] = json.loads(meta.get("genre_ids") or "[]")
        return meta

    def save(self, meta: dict) -> None:
        conn = self.db.get_connection()
        conn.execute(
            """
            INSERT INTO tmdb_meta (tmdb_id, media_type, title, original_title, release_date, poster_path,
                                   poster_other_language, backdrop_path, overview, vote_average, vote_count,
                                   imdb_id, genre_ids, status, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(tmdb_id, media_type) DO UPDATE SET
                title = excluded.title, original_title = excluded.original_title,
                release_date = excluded.release_date, poster_path = excluded.poster_path,
                poster_other_language = excluded.poster_other_language,
                backdrop_path = excluded.backdrop_path, overview = excluded.overview,
                vote_average = excluded.vote_average, vote_count = excluded.vote_count,
                imdb_id = COALESCE(excluded.imdb_id, tmdb_meta.imdb_id), genre_ids = excluded.genre_ids,
                status = excluded.status, fetched_at = CURRENT_TIMESTAMP
            """,
            (meta["tmdb_id"], meta["media_type"], meta.get("title"), meta.get("original_title"),
             meta.get("release_date"), meta.get("poster_path"), meta.get("poster_other_language"),
             meta.get("backdrop_path"), meta.get("overview"),
             meta.get("vote_average"), meta.get("vote_count"), meta.get("imdb_id"),
             json.dumps(meta.get("genre_ids") or []), meta.get("status")),
        )
        conn.commit()


def meta_from_tmdb(data: dict, media_type: str) -> dict:
    """Normaliza la respuesta de TMDB (detalle o resultado de listado)."""
    imdb_id = data.get("imdb_id") or (data.get("external_ids") or {}).get("imdb_id")
    genre_ids = data.get("genre_ids") or [g["id"] for g in data.get("genres") or [] if "id" in g]
    return {
        "tmdb_id": data["id"],
        "media_type": media_type,
        "title": data.get("title") or data.get("name"),
        "original_title": data.get("original_title") or data.get("original_name"),
        "release_date": data.get("release_date") or data.get("first_air_date") or None,
        "poster_path": data.get("poster_path"),
        "backdrop_path": data.get("backdrop_path"),
        "overview": data.get("overview"),
        "vote_average": data.get("vote_average"),
        "vote_count": data.get("vote_count"),
        "imdb_id": imdb_id or None,
        "genre_ids": genre_ids,
        "status": data.get("status"),
    }


def preferred_poster(data: dict) -> str | None:
    """Cartel en español, si no en inglés, si no sin texto. Sin esto TMDB
    devuelve el cartel principal de la obra, que puede estar en cualquier
    idioma (p. ej. checo) cuando no hay uno en español."""
    posters = (data.get("images") or {}).get("posters") or []
    for language in ("es", "en", None):
        candidates = [p for p in posters if p.get("iso_639_1") == language and p.get("file_path")]
        if candidates:
            best = max(candidates, key=lambda p: (p.get("vote_average") or 0, p.get("vote_count") or 0))
            return best["file_path"]
    return None


class TmdbMetadataService:
    def __init__(self, db_manager, client=None):
        self.repo = TmdbMetaRepository(db_manager)
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from core.tmdb.api_client import TMDBClient

            self._client = TMDBClient(Settings.TMDB_API_KEY)
        return self._client

    def get(self, tmdb_id: int | None, media_type: str, *, refresh: bool = False) -> dict | None:
        if not tmdb_id:
            return None
        if not refresh:
            cached = self.repo.get(tmdb_id, media_type)
            if cached:
                return cached
        if not Settings.TMDB_API_KEY:
            return self.repo.get(tmdb_id, media_type, max_age_days=None)
        data = self.client._make_request(
            f"{media_type}/{tmdb_id}",
            {"append_to_response": "external_ids,images", "include_image_language": "es,en,null"},
        )
        if not data or "id" not in data:
            # Sin respuesta: mejor un dato viejo que ninguno.
            return self.repo.get(tmdb_id, media_type, max_age_days=None)
        meta = meta_from_tmdb(data, media_type)
        preferred = preferred_poster(data)
        if not preferred and meta["poster_path"]:
            # Solo hay cartel en otro idioma: se guarda aparte para usarlo
            # solo si tampoco hay carátula de Hispashare.
            meta["poster_other_language"], meta["poster_path"] = meta["poster_path"], None
        elif preferred:
            meta["poster_path"] = preferred
        self.repo.save(meta)
        return self.repo.get(tmdb_id, media_type, max_age_days=None)

    def for_item(self, item: dict) -> dict | None:
        return self.get(item.get("tmdb_id"), media_type_for_category(item.get("content_category")))
