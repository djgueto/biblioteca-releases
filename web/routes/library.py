"""Biblioteca: listado, ficha, carátula, vigilar y buscados."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from config.settings import Settings
from core.covers import get_cover_bytes
from core.database.db_manager import DatabaseManager
from core.repositories import (
    DecisionLogRepository,
    LibraryRepository,
    SeriesRepository,
    SourceOfferRepository,
    SourceRepository,
)
from core.repositories.grab_history_repository import GrabHistoryRepository
from core.tmdb.metadata import TmdbMetadataService, image_url, media_type_for_category


class MonitoredUpdate(BaseModel):
    monitored: bool


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


def build(ctx) -> APIRouter:
    router = APIRouter()
    library_cache = ctx.library_cache

    @router.get("/api/categories")
    def categories():
        items = library_cache.get()
        counts: dict[str, int] = {}
        for item in items:
            counts[item["content_category"]] = counts.get(item["content_category"], 0) + 1
        return [
            {"id": key, "label": cfg["label"], "content_mode": cfg["content_mode"], "count": counts.get(key, 0)}
            for key, cfg in Settings.get_library_categories().items()
        ]

    @router.get("/api/library")
    def library():
        return library_cache.get()

    @router.get("/api/series/{series_id}")
    def series_detail(series_id: int):
        db = DatabaseManager()
        repo = LibraryRepository(db)
        item = repo.get_item(series_id)
        if item is None:
            raise HTTPException(404, "No existe")

        seasons: dict[int, dict] = {}
        for ep in db.get_local_episodes(series_id):
            season = seasons.setdefault(ep["season"], {"season": ep["season"], "local": [], "missing": []})
            measured = ep.get("quality_source") == "mediainfo"
            season["local"].append({
                "episode": ep.get("episode"), "filename": ep.get("filename"), "size_mb": ep.get("size_mb"),
                "measured": measured,
                # Sin analizar con MediaInfo no se muestra calidad: el nombre no es fiable.
                **{k: (ep.get(k) if measured else None) for k in ("resolution_height", "codec", "hdr")},
            })
        for ep in db.get_missing_episodes_for_series(series_id):
            season = seasons.setdefault(ep["season"], {"season": ep["season"], "local": [], "missing": []})
            season["missing"].append({k: ep.get(k) for k in ("episode", "name", "air_date")})

        item["seasons"] = [seasons[k] for k in sorted(seasons)]
        item["offers"] = [_serialize_offer(o) for o in SourceOfferRepository(db).get_all_for_series(series_id)]
        item["history"] = DecisionLogRepository(db).list_for_series(series_id, limit=30)
        item["grabs"] = GrabHistoryRepository(db).list_for_series(series_id)
        item["rejected_sources"] = SourceRepository(db).list_rejected(series_id)
        # Otro elemento con la misma ficha de TMDB: es uno de los motivos de
        # "Revisar match" (dos carpetas que dicen ser la misma obra).
        item["same_tmdb"] = [
            {"id": r[0], "name": r[1], "category_label": Settings.get_category_label(r[2])}
            for r in db.get_connection().execute(
                "SELECT id, COALESCE(NULLIF(tmdb_name, ''), name), content_category FROM series "
                "WHERE tmdb_id = ? AND id != ?", (item.get("tmdb_id"), series_id)).fetchall()
            if item.get("tmdb_id")
            and media_type_for_category(r[2]) == media_type_for_category(item.get("content_category"))
        ]
        item["search_disabled_manual"] = bool(item.get("search_disabled_manual"))
        disabled_at = item.get("search_disabled_at")
        item["search_retry_at"] = (
            (datetime.fromisoformat(str(disabled_at)) + timedelta(days=db.SEARCH_RETRY_DAYS)).strftime("%Y-%m-%d")
            if item.get("search_disabled") and not item["search_disabled_manual"] and disabled_at else None
        )
        episodic = item.get("content_mode") == "episodic"
        tmdb_service = TmdbMetadataService(db)
        meta = tmdb_service.for_item(item, need_seasons=episodic) if item.get("tmdb_id") else None
        if meta and episodic and meta.get("seasons") is not None \
                and set(seasons) - {s["season"] for s in meta["seasons"]}:
            # Hay una temporada que la caché aún no conocía (recién anunciada).
            meta = tmdb_service.get(item["tmdb_id"], "tv", refresh=True) or meta
        season_info = {s["season"]: s for s in (meta or {}).get("seasons") or []}
        for season in item["seasons"]:
            info = season_info.get(season["season"]) or {}
            season["air_date"] = info.get("air_date")
            season["episode_count"] = info.get("episode_count")
        item["tmdb"] = {
            "overview": meta.get("overview"),
            "vote_average": meta.get("vote_average"),
            "vote_count": meta.get("vote_count"),
            "imdb_id": meta.get("imdb_id"),
            "backdrop_url": image_url(meta.get("backdrop_path"), "w1280"),
            "release_date": meta.get("release_date"),
        } if meta else None
        return item

    @router.get("/api/series/{series_id}/cover")
    def series_cover(series_id: int):
        db = DatabaseManager()
        item = db.get_series_by_id(series_id)
        if not item:
            raise HTTPException(404, "No existe")
        item = dict(item)
        # Orden: cartel de TMDB en español/inglés/sin texto, la carátula de
        # Hispashare, y solo si no hay otra cosa el de TMDB en otro idioma.
        meta = (TmdbMetadataService(db).for_item(item) if item.get("tmdb_id") else None) or {}
        candidates = [
            image_url(meta.get("poster_path"), "w342"),
            LibraryRepository(db).cover_url(item),
            image_url(meta.get("poster_other_language"), "w342"),
        ]
        for cover_url in filter(None, candidates):
            data = get_cover_bytes(series_id, cover_url)
            if data:
                return Response(data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
        raise HTTPException(404, "Sin carátula")

    @router.put("/api/series/{series_id}/monitored")
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

    @router.get("/api/wanted")
    def wanted():
        return LibraryRepository(DatabaseManager()).list_missing()

    return router
