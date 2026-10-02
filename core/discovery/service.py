"""Novedades: estrenos de TMDB con notas de TMDB e IMDb, como las listas
automáticas de Radarr/Sonarr.

1. `fetch_candidates`: estrenos recientes y próximos de TMDB (películas y
   series nuevas) que no están en la biblioteca → lista de seguimiento.
2. `evaluate`: actualiza notas y decide (rules.evaluate): en espera,
   aprobada, descartada o caducada.
3. Las aprobadas se convierten en una ficha "en camino" (series.placeholder=1)
   en su biblioteca: a partir de ahí el ciclo normal las trata como cualquier
   otra (episodios faltantes, búsqueda en todas las fuentes, perfil de
   calidad).
4. `reconcile_placeholders`: cuando llega el archivo real, la ficha en camino
   se une a él.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path

from config.settings import Settings
from core.discovery.repository import WantedRepository
from core.discovery.rules import (
    APPROVED, DISMISSED, EXCLUDED_TV_GENRES, EXPIRED, IN_LIBRARY, REJECTED, WAITING,
    DiscoverySettings, classify_category, evaluate,
)
from core.tmdb.metadata import TmdbMetadataService, media_type_for_category

logger = logging.getLogger(__name__)

_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*]')


class DiscoveryService:
    def __init__(self, db_manager, *, tmdb_client=None, imdb=None, settings: DiscoverySettings | None = None,
                 log=None, today: date | None = None):
        self.db = db_manager
        self.repo = WantedRepository(db_manager)
        self.meta = TmdbMetadataService(db_manager, client=tmdb_client)
        self._imdb = imdb
        self.settings = settings or DiscoverySettings.load(db_manager)
        self.log = log or (lambda message: logger.info(message))
        self.today = today or date.today()

    @property
    def imdb(self):
        if self._imdb is None:
            from core.imdb.ratings import ImdbRatings

            self._imdb = ImdbRatings()
        return self._imdb

    # --- 1. candidatos ----------------------------------------------------------
    def fetch_candidates(self) -> int:
        s = self.settings
        start = (self.today - timedelta(days=s.days_back)).isoformat()
        end = (self.today + timedelta(days=s.days_ahead)).isoformat()
        lists = [
            ("movie", {"primary_release_date.gte": start, "primary_release_date.lte": end,
                       "sort_by": "popularity.desc", "include_adult": "false"}),
            ("tv", {"first_air_date.gte": start, "first_air_date.lte": end, "sort_by": "popularity.desc",
                    "without_genres": ",".join(str(g) for g in EXCLUDED_TV_GENRES)}),
        ]
        in_library = self._library_tmdb_ids()
        added = 0
        for media_type, params in lists:
            for page in range(1, s.pages + 1):
                data = self.meta.client._make_request(f"discover/{media_type}", {**params, "page": page}) or {}
                for result in data.get("results") or []:
                    category = classify_category(media_type, result.get("genre_ids") or [])
                    if category not in s.categories or (result["id"], media_type) in in_library:
                        continue
                    release = result.get("release_date") or result.get("first_air_date") or None
                    if self.repo.add({
                        "tmdb_id": result["id"], "media_type": media_type,
                        "title": result.get("title") or result.get("name"),
                        "original_title": result.get("original_title") or result.get("original_name"),
                        "year": int(release[:4]) if release else None, "release_date": release,
                        "category": category, "poster_path": result.get("poster_path"),
                        "overview": result.get("overview"), "genre_ids": result.get("genre_ids") or [],
                        "tmdb_rating": result.get("vote_average"), "tmdb_votes": result.get("vote_count"),
                    }):
                        added += 1
                if page >= (data.get("total_pages") or 0):
                    break
        return added

    def _library_tmdb_ids(self) -> set[tuple[int, str]]:
        rows = self.db.get_connection().execute(
            "SELECT tmdb_id, content_category FROM series WHERE tmdb_id IS NOT NULL AND COALESCE(placeholder, 0) = 0"
        ).fetchall()
        return {(r[0], media_type_for_category(r[1])) for r in rows}

    # --- 2. evaluación ----------------------------------------------------------
    def evaluate_pending(self) -> dict:
        pending = self.repo.to_evaluate()
        if not pending:
            return {}
        self.imdb.ensure_dataset()
        in_library = self._library_tmdb_ids()
        summary: dict[str, int] = {}
        for item in pending:
            if (item["tmdb_id"], item["media_type"]) in in_library:
                self.repo.update(item["id"], status=IN_LIBRARY, reason="Ya está en la biblioteca")
                summary[IN_LIBRARY] = summary.get(IN_LIBRARY, 0) + 1
                continue
            status = self._evaluate_one(item)
            summary[status] = summary.get(status, 0) + 1
        return summary

    def _evaluate_one(self, item: dict) -> str:
        meta = self.meta.get(item["tmdb_id"], item["media_type"], refresh=True) or {}
        rating = self.imdb.get(meta.get("imdb_id") or item.get("imdb_id"))
        fields = {
            "title": meta.get("title") or item.get("title"),
            "release_date": meta.get("release_date") or item.get("release_date"),
            "poster_path": meta.get("poster_path") or meta.get("poster_other_language") or item.get("poster_path"),
            "overview": meta.get("overview") or item.get("overview"),
            "tmdb_rating": meta.get("vote_average", item.get("tmdb_rating")),
            "tmdb_votes": meta.get("vote_count", item.get("tmdb_votes")),
            "imdb_id": meta.get("imdb_id") or item.get("imdb_id"),
            "imdb_rating": rating[0] if rating else None,
            "imdb_votes": rating[1] if rating else None,
        }
        status, reason = evaluate({**item, **fields}, self.settings, self.today)
        self.repo.update(item["id"], **fields, status=status, reason=reason, evaluated_at=self.today.isoformat())
        if status == APPROVED:
            self.approve(item["id"], reason=reason)
        return status

    # --- 3. aprobar: ficha "en camino" -------------------------------------------
    def approve(self, wanted_id: int, *, reason: str = "Añadida a mano") -> int | None:
        item = self.repo.get(wanted_id)
        if item is None:
            return None
        existing = self._library_row(item["tmdb_id"], item["media_type"])
        if existing:
            self.repo.update(wanted_id, status=IN_LIBRARY if not existing["placeholder"] else APPROVED,
                             series_id=existing["id"], reason=reason)
            return existing["id"]

        roots = Settings.get_paths_for_category(item["category"])
        if not roots:
            self.repo.update(wanted_id, status=APPROVED, reason=f"{reason} · sin ruta configurada para {item['category']}")
            return None
        title = item.get("title") or item.get("original_title") or str(item["tmdb_id"])
        folder_name = _INVALID_PATH_CHARS.sub("", f"{title} ({item['year']})" if item.get("year") else title).strip(" .")
        folder = self.db.normalize_path(os.path.join(roots[0], folder_name))

        conn = self.db.get_connection()
        cursor = conn.execute(
            """
            INSERT INTO series (name, folder_path, content_category, tmdb_id, tmdb_name, year, confidence_score,
                                needs_review, monitored, status, placeholder)
            VALUES (?, ?, ?, ?, ?, ?, 1.0, 0, 1, 'wanted', 1)
            """,
            (folder_name, folder, item["category"], item["tmdb_id"], title, item.get("year")),
        )
        conn.commit()
        series_id = cursor.lastrowid
        self.repo.update(wanted_id, status=APPROVED, series_id=series_id, reason=reason)

        from core.repositories import DecisionLogRepository

        DecisionLogRepository(self.db).append(series_id, "discovery_approved", f"Novedad añadida: {reason}",
                                              {"tmdb_id": item["tmdb_id"], "wanted_id": wanted_id})
        if media_type_for_category(item["category"]) == "tv" and Settings.TMDB_API_KEY:
            try:
                self.db.recalculate_missing_episodes_for_series(series_id)
            except Exception as e:
                logger.warning(f"No se pudieron calcular los episodios de '{title}': {e}")
        self.log(f"   ➕ {title} ({item.get('year') or '?'}) → {Settings.get_category_label(item['category'])}: {reason}")
        return series_id

    def dismiss(self, wanted_id: int) -> None:
        item = self.repo.get(wanted_id)
        if item is None:
            return
        if item.get("series_id"):
            self._delete_placeholder(item["series_id"])
        self.repo.update(wanted_id, status=DISMISSED, reason="Descartada a mano", series_id=None)

    def _library_row(self, tmdb_id: int, media_type: str) -> dict | None:
        rows = self.db.get_connection().execute(
            "SELECT id, content_category, COALESCE(placeholder, 0) AS placeholder FROM series WHERE tmdb_id = ? "
            "ORDER BY placeholder ASC", (tmdb_id,)).fetchall()
        for row in rows:
            if media_type_for_category(row["content_category"]) == media_type:
                return dict(row)
        return None

    # --- 4. cuando llega el archivo real -----------------------------------------
    def reconcile_placeholders(self) -> int:
        conn = self.db.get_connection()
        placeholders = conn.execute(
            "SELECT id, tmdb_id, content_category, folder_path FROM series WHERE COALESCE(placeholder, 0) = 1"
        ).fetchall()
        resolved = 0
        for p in placeholders:
            media_type = media_type_for_category(p["content_category"])
            real = next((
                r for r in conn.execute(
                    "SELECT id, content_category FROM series WHERE tmdb_id = ? AND id != ? AND COALESCE(placeholder, 0) = 0",
                    (p["tmdb_id"], p["id"])).fetchall()
                if media_type_for_category(r["content_category"]) == media_type
            ), None)
            if real is not None:
                # El archivo llegó con otro nombre y el emparejado TMDB lo identificó.
                if _table_exists(conn, "grab_history"):
                    conn.execute("UPDATE grab_history SET series_id = ? WHERE series_id = ?", (real["id"], p["id"]))
                self._delete_placeholder(p["id"])
                self._mark_in_library(p["tmdb_id"], media_type, real["id"])
                resolved += 1
            elif Path(p["folder_path"]).exists():
                # Llegó justo con el nombre previsto: la ficha pasa a ser la real.
                conn.execute("UPDATE series SET placeholder = 0, status = 'pending' WHERE id = ?", (p["id"],))
                conn.commit()
                self._mark_in_library(p["tmdb_id"], media_type, p["id"])
                resolved += 1
        return resolved

    def _mark_in_library(self, tmdb_id: int, media_type: str, series_id: int) -> None:
        item = self.repo.find(tmdb_id, media_type)
        if item:
            self.repo.update(item["id"], status=IN_LIBRARY, series_id=series_id, reason="Ya está en la biblioteca")

    def _delete_placeholder(self, series_id: int) -> None:
        conn = self.db.get_connection()
        if not conn.execute("SELECT 1 FROM series WHERE id = ? AND COALESCE(placeholder, 0) = 1", (series_id,)).fetchone():
            return  # nunca borrar una ficha real
        for table in ("missing_episodes", "source_offers", "source_matches", "item_quality"):
            if _table_exists(conn, table):
                conn.execute(f"DELETE FROM {table} WHERE series_id = ?", (series_id,))
        conn.execute("DELETE FROM series WHERE id = ?", (series_id,))
        conn.commit()

    # --- todo junto --------------------------------------------------------------
    def run(self) -> dict:
        if not self.settings.enabled:
            return {"enabled": False}
        if not Settings.TMDB_API_KEY:
            self.log("   ⚠️ Novedades: falta la clave de TMDB")
            return {"enabled": True, "error": "sin clave TMDB"}
        resolved = self.reconcile_placeholders()
        added = self.fetch_candidates()
        summary = self.evaluate_pending()
        self.log(
            f"   ✅ Novedades: {added} nuevas en seguimiento · {summary.get(APPROVED, 0)} aprobadas · "
            f"{summary.get(WAITING, 0)} en espera · {summary.get(REJECTED, 0)} descartadas · "
            f"{resolved} ya en la biblioteca"
        )
        return {"enabled": True, "added": added, "evaluated": summary, "resolved": resolved}


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)).fetchone() is not None


__all__ = ["DiscoveryService", "DISMISSED", "EXPIRED"]
