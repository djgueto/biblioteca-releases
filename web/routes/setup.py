"""Configuración que antes solo se podía tocar en el .env o en la app de
escritorio: bibliotecas, Hispashare, eMule/aMule y el asistente de primer
arranque. Todo se guarda en el .env (Settings.update_env)."""

from __future__ import annotations

import os
import string
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.library.roots import sync_library_roots

router = APIRouter()


def mask(secret: str) -> str:
    """Las claves nunca salen enteras hacia el navegador."""
    secret = secret or ""
    return f"••••••{secret[-4:]}" if len(secret) > 4 else ("••••" if secret else "")


# --- bibliotecas ------------------------------------------------------------
def _libraries() -> list[dict]:
    out = []
    for category, cfg in Settings.get_library_categories().items():
        paths = [p for p, _ in Settings._configured_paths(category)]
        out.append({
            "id": category, "label": cfg["label"], "content_mode": cfg["content_mode"],
            "paths": [{"path": p, "accessible": Path(p).is_dir()} for p in paths],
        })
    return out


@router.get("/api/settings/libraries")
def get_libraries():
    return {"libraries": _libraries()}


@router.put("/api/settings/libraries")
def put_libraries(body: dict, request: Request):
    wanted = body.get("libraries")
    categories = Settings.get_library_categories()
    if not isinstance(wanted, dict) or set(wanted) - set(categories):
        raise HTTPException(422, "Bibliotecas no válidas")

    seen: dict[str, str] = {}
    values = {}
    for category, paths in wanted.items():
        if not isinstance(paths, list):
            raise HTTPException(422, "Bibliotecas no válidas")
        clean = []
        for raw in paths:
            path = str(raw or "").strip()
            if not path or path in clean:
                continue
            if not Path(path).is_dir():
                raise HTTPException(422, f"No existe la carpeta: {path}")
            key = Settings._path_key(path)
            if key in seen and seen[key] != category:
                raise HTTPException(422, f"La carpeta {path} ya está en «{categories[seen[key]]['label']}»: "
                                         "cada carpeta solo puede estar en una biblioteca")
            seen[key] = category
            clean.append(path)
        cfg = categories[category]
        values[cfg["primary_key"]] = clean[0] if clean else ""
        values[cfg["additional_key"]] = ";".join(clean[1:])
    if values:
        Settings.update_env(values)
    # Si una ruta cambió de sitio, las fichas de la BD se reubican ya (lo
    # mismo que haría el siguiente arranque).
    report = sync_library_roots(DatabaseManager())
    cache = getattr(request.app.state, "library_cache", None)
    if cache is not None:
        cache.invalidate()
    return {"libraries": _libraries(), "relocation": {
        "status": getattr(report, "status", None), "changes": getattr(report, "changes", 0),
        "message": getattr(report, "message", "")}}


# --- fuentes y descargas -------------------------------------------------------
@dataclass(frozen=True)
class Field:
    name: str  # en la API
    env: str  # en el .env / Settings
    kind: type = str
    secret: bool = False
    minimum: float | None = None


SOURCE_FIELDS = [
    Field("hispashare_user", "HISPASHARE_USER"),
    Field("hispashare_pass", "HISPASHARE_PASS", secret=True),
    Field("selenium_headless", "SELENIUM_HEADLESS", bool),
    Field("emule_url", "EMULE_URL"),
    Field("emule_username", "EMULE_USERNAME"),
    Field("emule_password", "EMULE_PASSWORD", secret=True),
    Field("emule_auto_send", "EMULE_AUTO_SEND", bool),
    Field("upgrade_enabled", "UPGRADE_ENABLED", bool),
    Field("upgrade_max_series_per_cycle", "UPGRADE_MAX_SERIES_PER_CYCLE", int, minimum=0),
    Field("upgrade_max_links_per_cycle", "UPGRADE_MAX_LINKS_PER_CYCLE", int, minimum=0),
    Field("wolfmax_max_downloads_per_cycle", "WOLFMAX_MAX_DOWNLOADS_PER_CYCLE", int, minimum=0),
    Field("wolfmax_staging_dir", "WOLFMAX_STAGING_DIR"),
    Field("minimum_movie_size_gb", "MINIMUM_MOVIE_SIZE_GB", float, minimum=0),
    Field("auto_rename_folder", "AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN", bool),
]


def _source_settings() -> dict:
    out = {}
    for f in SOURCE_FIELDS:
        value = getattr(Settings, f.env, None)
        out[f.name] = mask(value) if f.secret else value
    return out


@router.get("/api/settings/sources")
def get_sources():
    return _source_settings()


@router.put("/api/settings/sources")
def put_sources(body: dict):
    fields = {f.name: f for f in SOURCE_FIELDS}
    unknown = set(body) - set(fields)
    if unknown:
        raise HTTPException(422, f"Ajustes desconocidos: {', '.join(sorted(unknown))}")
    values = {}
    for name, raw in body.items():
        f = fields[name]
        if f.secret and raw == mask(getattr(Settings, f.env, "")):
            continue  # sin tocar
        try:
            if f.kind is bool:
                if not isinstance(raw, bool):
                    raise ValueError
                value = raw
            elif f.kind is str:
                value = str(raw or "").strip()
            else:
                value = f.kind(raw)
                if f.minimum is not None and value < f.minimum:
                    raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(422, f"Valor no válido para {name}")
        if name == "emule_url" and value and not value.startswith(("http://", "https://")):
            raise HTTPException(422, "La dirección de eMule/aMule debe empezar por http://")
        if name == "wolfmax_staging_dir" and value and not Path(value).is_dir():
            raise HTTPException(422, f"No existe la carpeta: {value}")
        values[f.env] = value
    if values:
        Settings.update_env(values)
    return _source_settings()


# --- asistente de primer arranque ---------------------------------------------
def setup_status() -> dict:
    from core.download_clients import ED2K, TORRENT, build_download_dispatcher

    has_libraries = any(Settings.get_paths_for_category(c) for c in Settings.get_library_categories())
    dispatcher = build_download_dispatcher()
    steps = [
        {"id": "tmdb", "label": "Clave de TMDB", "required": True, "done": bool(Settings.TMDB_API_KEY)},
        {"id": "libraries", "label": "Carpetas de la biblioteca", "required": True, "done": has_libraries},
        {"id": "sources", "label": "Fuentes (Hispashare, Wolfmax, DonTorrent)", "required": False,
         "done": bool(Settings.HISPASHARE_USER) or Settings.DONTORRENT_ENABLED
         or bool(getattr(Settings, "WOLFMAX_UPGRADE_ENABLED", False))},
        {"id": "downloads", "label": "Cliente de descargas (torrent o eMule/aMule)", "required": False,
         "done": dispatcher.can_handle(TORRENT) or dispatcher.can_handle(ED2K)},
    ]
    return {"complete": all(s["done"] for s in steps if s["required"]), "steps": steps}


@router.get("/api/setup")
def get_setup():
    return setup_status()


# --- explorador de carpetas (para elegir rutas sin escribirlas) ---------------
def _roots() -> list[str]:
    if os.name == "nt":
        return [f"{d}:/" for d in string.ascii_uppercase if os.path.isdir(f"{d}:/")]
    return ["/"]


@router.get("/api/fs")
def browse(path: str = ""):
    if not path:
        return {"path": "", "parent": None, "dirs": [{"name": r, "path": r} for r in _roots()]}
    current = Path(path)
    if not current.is_dir():
        raise HTTPException(404, "No existe la carpeta")
    try:
        entries = sorted((p for p in current.iterdir() if p.is_dir() and not p.name.startswith(".")),
                         key=lambda p: p.name.lower())
    except OSError as e:
        raise HTTPException(403, f"No se puede leer la carpeta: {e.strerror or e}")
    parent = current.parent
    return {
        "path": current.as_posix(),
        "parent": "" if parent == current else parent.as_posix(),
        "dirs": [{"name": p.name, "path": p.as_posix()} for p in entries[:500]],
    }
