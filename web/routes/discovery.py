"""Novedades de TMDB/IMDb y sus ajustes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.discovery.repository import WantedRepository
from core.discovery.rules import APPROVED, DISMISSED, EXPIRED, IN_LIBRARY, REJECTED, WAITING, DiscoverySettings
from core.discovery.service import DiscoveryService
from core.tmdb.metadata import image_url


def build(ctx) -> APIRouter:
    router = APIRouter()
    library_cache = ctx.library_cache

    @router.get("/api/discovery")
    def discovery_list(status: str = "approved"):
        db = DatabaseManager()
        repo = WantedRepository(db)
        statuses = {"approved": [APPROVED], "waiting": [WAITING], "rejected": [REJECTED, EXPIRED, DISMISSED],
                    "in_library": [IN_LIBRARY]}.get(status, [status])
        items = repo.list(statuses)
        for item in items:
            item["poster_url"] = image_url(item.get("poster_path"), "w342")
            item["category_label"] = Settings.get_category_label(item.get("category"))
        return {"items": items, "counts": repo.counts()}

    @router.post("/api/discovery/{wanted_id}/approve")
    def discovery_approve(wanted_id: int):
        db = DatabaseManager()
        if WantedRepository(db).get(wanted_id) is None:
            raise HTTPException(404, "No existe")
        series_id = DiscoveryService(db).approve(wanted_id, reason="Añadida a mano")
        library_cache.invalidate()
        return {"id": wanted_id, "series_id": series_id}

    @router.post("/api/discovery/{wanted_id}/dismiss")
    def discovery_dismiss(wanted_id: int):
        db = DatabaseManager()
        if WantedRepository(db).get(wanted_id) is None:
            raise HTTPException(404, "No existe")
        DiscoveryService(db).dismiss(wanted_id)
        library_cache.invalidate()
        return {"id": wanted_id}

    @router.get("/api/settings/discovery")
    def get_discovery_settings():
        return {**DiscoverySettings.load(DatabaseManager()).to_dict(),
                "all_categories": [{"id": k, "label": v["label"]} for k, v in Settings.get_library_categories().items()]}

    @router.put("/api/settings/discovery")
    def put_discovery_settings(body: dict):
        current = DiscoverySettings.load(DatabaseManager()).to_dict()
        unknown = set(body) - set(current) - {"all_categories"}
        if unknown:
            raise HTTPException(422, f"Ajustes desconocidos: {', '.join(sorted(unknown))}")
        merged = {**current, **{k: v for k, v in body.items() if k in current}}
        try:
            settings = DiscoverySettings(**merged)
            for key in ("min_tmdb", "min_imdb"):
                if not 0 <= float(getattr(settings, key)) <= 10:
                    raise ValueError(key)
        except (TypeError, ValueError):
            raise HTTPException(422, "Ajustes de novedades no válidos")
        settings.save(DatabaseManager())
        return get_discovery_settings()

    return router
