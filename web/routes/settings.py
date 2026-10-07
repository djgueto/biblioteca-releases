"""Ajustes generales, importación, perfil de calidad y tareas programadas."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.importer.service import ImportSettings
from core.services.quality_profile import TIERS, QualityProfile
from web.routes.setup import mask


class QualityProfileUpdate(BaseModel):
    order: list[str]
    minimum: str


class GeneralSettingsUpdate(BaseModel):
    tmdb_api_key: str | None = None
    omdb_api_key: str | None = None
    wolfmax_enabled: bool | None = None
    dontorrent_enabled: bool | None = None
    dontorrent_url: str | None = None
    torrent_watch_dir: str | None = None
    autostart_scheduler: bool | None = None


def autostart_enabled() -> bool:
    """¿Arrancar el ciclo automático al iniciar la web? Ajuste de la web
    (activado por defecto); WEB_SCHEDULER_AUTOSTART en el entorno manda si
    está definido (Docker)."""
    env = os.getenv("WEB_SCHEDULER_AUTOSTART")
    if env is not None:
        return env.lower() == "true"
    from core.repositories.app_settings_repository import AppSettingsRepository

    return bool(AppSettingsRepository(DatabaseManager()).get("autostart_scheduler", True))


def _tmdb_key_works(key: str) -> bool:
    import requests

    try:
        return requests.get("https://api.themoviedb.org/3/configuration", params={"api_key": key}, timeout=10).status_code == 200
    except requests.RequestException:
        return False


def _omdb_key_works(key: str) -> bool:
    import requests

    try:
        r = requests.get("https://www.omdbapi.com/", params={"apikey": key, "i": "tt0133093"}, timeout=10)
        return r.status_code == 200 and r.json().get("Response") == "True"
    except (requests.RequestException, ValueError):
        return False


def build(ctx) -> APIRouter:
    router = APIRouter()
    periodic = ctx.periodic

    @router.get("/api/settings/general")
    def get_general_settings():
        return {
            "tmdb_api_key": mask(Settings.TMDB_API_KEY),
            "omdb_api_key": mask(Settings.OMDB_API_KEY),
            "wolfmax_enabled": bool(getattr(Settings, "WOLFMAX_UPGRADE_ENABLED", False)),
            "dontorrent_enabled": Settings.DONTORRENT_ENABLED,
            "dontorrent_url": Settings.DONTORRENT_URL,
            "torrent_watch_dir": Settings.get_torrent_watch_dir(),
            "autostart_scheduler": autostart_enabled(),
        }

    @router.put("/api/settings/general")
    def put_general_settings(body: GeneralSettingsUpdate):
        values = {}
        if body.tmdb_api_key is not None and body.tmdb_api_key.strip() != mask(Settings.TMDB_API_KEY):
            key = body.tmdb_api_key.strip()
            if key and not _tmdb_key_works(key):
                raise HTTPException(422, "TMDB rechaza esa API key")
            values["TMDB_API_KEY"] = key
        if body.omdb_api_key is not None and body.omdb_api_key.strip() != mask(Settings.OMDB_API_KEY):
            key = body.omdb_api_key.strip()
            if key and not _omdb_key_works(key):
                raise HTTPException(422, "OMDb (IMDb) rechaza esa API key")
            values["OMDB_API_KEY"] = key
        if body.wolfmax_enabled is not None:
            values["WOLFMAX_UPGRADE_ENABLED"] = body.wolfmax_enabled
        if body.dontorrent_enabled is not None:
            values["DONTORRENT_ENABLED"] = body.dontorrent_enabled
        if body.dontorrent_url is not None:
            url = body.dontorrent_url.strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                raise HTTPException(422, "La dirección de DonTorrent debe empezar por https://")
            values["DONTORRENT_URL"] = url
        if body.torrent_watch_dir is not None:
            values["TORRENT_WATCH_DIR"] = body.torrent_watch_dir.strip()
        if body.autostart_scheduler is not None:
            from core.repositories.app_settings_repository import AppSettingsRepository

            AppSettingsRepository(DatabaseManager()).set("autostart_scheduler", body.autostart_scheduler)
        if values:
            Settings.update_env(values)
        return get_general_settings()

    @router.get("/api/settings/import")
    def get_import_settings():
        return ImportSettings.load(DatabaseManager()).to_dict()

    @router.put("/api/settings/import")
    def put_import_settings(body: dict):
        current = ImportSettings.load(DatabaseManager()).to_dict()
        unknown = set(body) - set(current)
        if unknown:
            raise HTTPException(422, f"Ajustes desconocidos: {', '.join(sorted(unknown))}")
        merged = {**current, **body}
        if merged["mode"] not in ("copy", "move"):
            raise HTTPException(422, "El modo debe ser copiar o mover")
        merged["source_dirs"] = [d.strip() for d in merged.get("source_dirs") or [] if d and d.strip()]
        if merged["enabled"] and not merged["source_dirs"]:
            raise HTTPException(422, "Indica al menos una carpeta de descargas")
        missing = [d for d in merged["source_dirs"] if not os.path.isdir(d)]
        if missing:
            raise HTTPException(422, f"No existe la carpeta: {missing[0]}")
        try:
            merged["min_age_minutes"] = max(1, int(merged["min_age_minutes"]))
        except (TypeError, ValueError):
            raise HTTPException(422, "Minutos sin cambios no válidos")
        ImportSettings(**merged).save(DatabaseManager())
        return get_import_settings()

    @router.get("/api/settings/quality")
    def get_quality_profile():
        profile = QualityProfile.load(DatabaseManager())
        return {**profile.to_dict(), "tiers": [{"id": t, "label": TIERS[t]} for t in profile.order]}

    @router.put("/api/settings/quality")
    def put_quality_profile(body: QualityProfileUpdate):
        if set(body.order) != set(TIERS) or len(body.order) != len(TIERS) or body.minimum not in TIERS:
            raise HTTPException(422, "Perfil de calidad no válido")
        QualityProfile(order=body.order, minimum=body.minimum).save(DatabaseManager())
        return get_quality_profile()

    @router.get("/api/tasks")
    def scheduled_tasks():
        return {"tasks": periodic.status(), "cycle_interval_hours": Settings.CHECK_INTERVAL_HOURS}

    @router.put("/api/settings/tasks")
    def put_task_settings(body: dict):
        intervals = body.get("intervals") or {}
        try:
            periodic.set_intervals({k: int(v) for k, v in intervals.items()})
        except KeyError as e:
            raise HTTPException(422, f"Tarea desconocida: {e}")
        except (TypeError, ValueError):
            raise HTTPException(422, "Intervalo no válido")
        if body.get("cycle_interval_hours") is not None:
            try:
                hours = int(body["cycle_interval_hours"])
            except (TypeError, ValueError):
                raise HTTPException(422, "Intervalo del ciclo no válido")
            if not 1 <= hours <= 168:
                raise HTTPException(422, "El ciclo debe ser de 1 a 168 horas")
            Settings.update_env({"CHECK_INTERVAL_HOURS": hours})
        return scheduled_tasks()

    return router
