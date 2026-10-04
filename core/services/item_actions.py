"""Acciones manuales sobre un elemento de la biblioteca (desde la web).

Antes vivían en las tareas Qt de la app de escritorio (ManualUpdateTask,
TMDBRescanTask); aquí quedan sin UI para que las use cualquier interfaz.
"""

from __future__ import annotations

import logging
from typing import Callable

from config.settings import Settings
from core.services.factory import build_domain_services

logger = logging.getLogger(__name__)


class ItemNotFound(LookupError):
    pass


class ItemActions:
    def __init__(self, db, *, log: Callable[[str], None] | None = None):
        self.db = db
        self.log = log or (lambda _msg: None)
        self.services = build_domain_services(db)

    def _item(self, series_id: int) -> dict:
        item = self.db.get_series_by_id(series_id)
        if not item:
            raise ItemNotFound(series_id)
        return dict(item)

    @staticmethod
    def is_library(item: dict) -> bool:
        return Settings.get_category_content_mode(item.get("content_category")) == "single_file"

    def apply_manual_changes(self, series_id: int, changes: dict) -> dict:
        """Guarda cambios hechos a mano (TMDB, URL de Hispashare...) y aplica
        sus consecuencias: faltantes, fuentes y, si está activado, renombrar
        la carpeta al nombre de TMDB."""
        item = self._item(series_id)
        saved = self.services["series_repository"].save_manual_changes(series_id, changes, recalculate_missing=False)
        result = self.services["reconciliation_service"].on_manual_update(
            series_id, saved.get("previous_item") or {}, saved.get("updated_item") or {}, changes,
            is_library=self.is_library(item),
        )
        renamed_to = None
        if Settings.AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN and changes.get("tmdb_name"):
            rename = self.db.rename_series_folder(series_id, changes["tmdb_name"])
            if rename.get("renamed"):
                renamed_to = rename.get("new_name")
                self.log(f"📁 Carpeta renombrada: '{rename['old_name']}' -> '{rename['new_name']}'")
            elif not rename.get("success"):
                self.log(f"⚠️ No se pudo renombrar la carpeta: {rename.get('error', 'Error desconocido')}")
        return {**result, "renamed_to": renamed_to}

    def rematch_tmdb(self, series_id: int) -> dict:
        """Vuelve a identificar automáticamente con TMDB."""
        from core.tmdb.api_client import TMDBClient
        from core.tmdb.auto_matcher import TMDBAutoMatcher

        if not Settings.TMDB_API_KEY:
            raise RuntimeError("Falta la API key de TMDB")
        item = self._item(series_id)
        matcher = TMDBAutoMatcher(TMDBClient(Settings.TMDB_API_KEY))
        if self.is_library(item):
            match_result = matcher.match_content(item)
        else:
            episodes = self.db.get_local_episodes(series_id)
            seasons: dict[int, int] = {}
            for ep in episodes:
                if ep.get("season") is not None:
                    seasons[ep["season"]] = seasons.get(ep["season"], 0) + 1
            match_result = matcher.match_series({
                "id": series_id, "name": item["name"], "folder_path": item.get("folder_path", ""),
                "seasons": seasons, "episodes": episodes,
            })

        log = self.services["decision_log_repository"]
        if not match_result:
            log.append(series_id, "tmdb_rescan_no_match",
                       "No se encontró un match TMDB adecuado al reidentificar", {"name": item.get("name", "")})
            return {"matched": False}

        update = self.services["series_repository"].update_tmdb_identity(series_id, match_result)
        result = self.services["reconciliation_service"].on_tmdb_changed(
            series_id, update.get("previous_item") or {}, update.get("updated_item") or {},
            is_library=self.is_library(item),
        )
        log.append(series_id, "tmdb_rescan_matched", "TMDB reasignado al reidentificar",
                   {k: match_result.get(k) for k in ("tmdb_id", "name", "year", "confidence_score")})
        return {"matched": True, "match": match_result, "missing_count": (result or {}).get("missing_count", 0)}
