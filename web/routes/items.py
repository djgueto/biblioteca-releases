"""Correcciones a mano desde la ficha: TMDB, enlace de Hispashare y búsquedas."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.repositories import DecisionLogRepository
from core.services.factory import build_domain_services
from core.services.item_actions import ItemActions, ItemNotFound
from core.tmdb.metadata import TmdbMetadataService, allowed_media_types, image_url, media_type_for_item
from web.jobs import JobAlreadyRunning

logger = logging.getLogger(__name__)

TMDB_LINK = re.compile(r"themoviedb\.org/(tv|movie)/(\d+)")


class HispashareUrlUpdate(BaseModel):
    url: str | None = None


class TmdbUpdate(BaseModel):
    tmdb_id: int
    media_type: str | None = None  # sin indicar: lo que toca por biblioteca


class SearchToggle(BaseModel):
    enabled: bool


def _tmdb_result(data: dict, current: tuple | None, media_type: str) -> dict:
    date = data.get("first_air_date") or data.get("release_date") or ""
    overview = data.get("overview") or ""
    return {
        "id": data["id"],
        "media_type": media_type,
        "title": data.get("name") or data.get("title") or "Sin nombre",
        "original_title": data.get("original_name") or data.get("original_title") or "",
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "overview": overview if len(overview) <= 320 else overview[:320].rsplit(" ", 1)[0] + "…",
        "poster_url": image_url(data.get("poster_path"), "w185"),
        "vote_average": data.get("vote_average"),
        "current": (data["id"], media_type) == current,
    }


def build(ctx) -> APIRouter:
    router = APIRouter()
    jobs = ctx.jobs
    library_cache = ctx.library_cache

    @router.put("/api/series/{series_id}/hispashare_url")
    def set_hispashare_url(series_id: int, body: HispashareUrlUpdate):
        url = (body.url or "").strip()
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(422, "El enlace debe empezar por https://")
        try:
            ItemActions(DatabaseManager()).apply_manual_changes(series_id, {"hispashare_url": url or None})
        except ItemNotFound:
            raise HTTPException(404, "No existe")
        library_cache.invalidate()
        return {"id": series_id, "hispashare_url": url or None}

    @router.post("/api/series/{series_id}/hispashare_search_now")
    def hispashare_search_now(series_id: int):
        if not DatabaseManager().get_series_by_id(series_id):
            raise HTTPException(404, "No existe")
        if not (Settings.HISPASHARE_USER and Settings.HISPASHARE_PASS):
            raise HTTPException(409, "Falta el usuario de Hispashare (Ajustes → Fuentes)")
        try:
            jobs.start("source_search", series_id=series_id)
        except JobAlreadyRunning:
            raise HTTPException(409, "Ya hay una búsqueda en Hispashare en marcha")
        return jobs.state()

    @router.post("/api/series/{series_id}/hispashare_reject")
    def reject_hispashare(series_id: int):
        db = DatabaseManager()
        if not db.get_series_by_id(series_id):
            raise HTTPException(404, "No existe")
        rejected = build_domain_services(db)["reconciliation_service"].reject_source(series_id, "hispashare")
        if not rejected:
            raise HTTPException(409, "No tiene ficha de Hispashare asignada")
        library_cache.invalidate()
        return {"id": series_id, "rejected_url": rejected}

    @router.put("/api/series/{series_id}/hispashare_search")
    def toggle_hispashare_search(series_id: int, body: SearchToggle):
        db = DatabaseManager()
        if not db.get_series_by_id(series_id):
            raise HTTPException(404, "No existe")
        if body.enabled:
            db.enable_series_search(series_id)
        else:
            db.disable_series_search(series_id, manual=True)
        DecisionLogRepository(db).append(
            series_id, "search_toggled",
            "Búsqueda en Hispashare activada a mano" if body.enabled else "Búsqueda en Hispashare desactivada a mano",
            {"enabled": body.enabled, "origin": "web"},
        )
        library_cache.invalidate()
        return {"id": series_id, "search_disabled": not body.enabled}

    def _tmdb_client():
        if not Settings.TMDB_API_KEY:
            raise HTTPException(409, "Falta la API key de TMDB (Sistema → Ajustes)")
        from core.tmdb.api_client import TMDBClient

        return TMDBClient(Settings.TMDB_API_KEY)

    @router.get("/api/series/{series_id}/tmdb_search")
    def tmdb_search(series_id: int, q: str):
        item = DatabaseManager().get_series_by_id(series_id)
        if not item:
            raise HTTPException(404, "No existe")
        item = dict(item)
        allowed = allowed_media_types(item.get("content_category"))
        media = allowed[0]
        client = _tmdb_client()
        q = q.strip()
        link = TMDB_LINK.search(q)
        if link and link.group(1) not in allowed:
            kind = "una película" if link.group(1) == "movie" else "una serie"
            label = Settings.get_category_label(item.get("content_category"))
            raise HTTPException(422, f"Ese enlace es de {kind}, pero «{label}» no es una biblioteca de ese tipo")
        direct = link.group(2) if link else (q if q.isdigit() else None)
        results: list[tuple[dict, str]] = []
        if direct:
            # Un número o un enlace de themoviedb.org: esa ficha exacta. Un
            # número suelto se busca como lo que toca por biblioteca.
            media = link.group(1) if link else media
            data = client._make_request(f"{media}/{direct}")
            results = [(data, media)] if data and "id" in data else []
        elif q:
            for kind in allowed:
                found = client.search_series(q) if kind == "tv" else client.search_movies(q)
                results += [(r, kind) for r in found[:20]]
        current = (item.get("tmdb_id"), media_type_for_item(item))
        return {"media_type": allowed[0],
                "results": [_tmdb_result(r, current, kind) for r, kind in results if "id" in r]}

    @router.post("/api/series/{series_id}/tmdb_confirm")
    def confirm_tmdb(series_id: int):
        """"Revisar match" → "es correcto": la identificación actual se da
        por buena (como si se hubiera elegido a mano)."""
        db = DatabaseManager()
        item = db.get_series_by_id(series_id)
        if not item:
            raise HTTPException(404, "No existe")
        item = dict(item)
        if not item.get("tmdb_id"):
            raise HTTPException(409, "No tiene TMDB que confirmar: vincúlalo primero")
        services = build_domain_services(db)
        services["series_repository"].save_manual_changes(
            series_id, {"needs_review": False, "confidence_score": 1.0}, recalculate_missing=False)
        services["decision_log_repository"].append(
            series_id, "tmdb_confirmed", "Identificación TMDB confirmada a mano",
            {"tmdb_id": item["tmdb_id"], "previous_confidence": item.get("confidence_score"), "origin": "web"},
        )
        missing_count = None
        if Settings.get_category_content_mode(item.get("content_category")) == "episodic" and Settings.TMDB_API_KEY:
            # Mientras estaba en revisión no se calculaban sus faltantes.
            try:
                missing_count = services["missing_episodes_service"].rebuild_for_series(series_id)["missing_count"]
            except Exception:
                logger.exception(f"No se pudieron recalcular los faltantes de {series_id}")
        library_cache.invalidate()
        return {"id": series_id, "tmdb_id": item["tmdb_id"], "missing_count": missing_count}

    @router.put("/api/series/{series_id}/tmdb")
    def set_tmdb(series_id: int, body: TmdbUpdate):
        db = DatabaseManager()
        item = db.get_series_by_id(series_id)
        if not item:
            raise HTTPException(404, "No existe")
        item = dict(item)
        allowed = allowed_media_types(item.get("content_category"))
        media = body.media_type or allowed[0]
        if media not in allowed:
            label = Settings.get_category_label(item.get("content_category"))
            raise HTTPException(422, f"«{label}» no se puede vincular a {'una serie' if media == 'tv' else 'una película'}")
        # Nombre y año se leen de TMDB, no del navegador.
        data = _tmdb_client()._make_request(f"{media}/{body.tmdb_id}")
        if not data or "id" not in data:
            raise HTTPException(422, f"TMDB no tiene ninguna {'serie' if media == 'tv' else 'película'} con ese ID")
        found = _tmdb_result(data, None, media)
        changes = {
            "tmdb_id": found["id"], "tmdb_name": found["title"], "year": found["year"],
            "confidence_score": 1.0, "needs_review": False, "tmdb_media_type": media,
        }
        result = ItemActions(db, log=logger.warning).apply_manual_changes(series_id, changes)
        renamed = result.get("renamed_to")
        try:
            TmdbMetadataService(db).get(found["id"], media, refresh=True)
        except Exception:
            logger.exception("No se pudieron refrescar los datos de TMDB")
        library_cache.invalidate()
        return {"id": series_id, "tmdb_id": found["id"], "tmdb_name": found["title"], "year": found["year"],
                "missing_count": result.get("missing_count", 0), "renamed_to": renamed}

    @router.post("/api/series/{series_id}/tmdb_rematch")
    def rematch_tmdb(series_id: int):
        _tmdb_client()  # 409 si falta la API key
        try:
            result = ItemActions(DatabaseManager()).rematch_tmdb(series_id)
        except ItemNotFound:
            raise HTTPException(404, "No existe")
        library_cache.invalidate()
        match = result.get("match") or {}
        return {"id": series_id, "matched": result["matched"], "tmdb_id": match.get("tmdb_id"),
                "tmdb_name": match.get("name"), "year": match.get("year"),
                "confidence_score": match.get("confidence_score")}

    return router
