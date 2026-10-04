"""Sincronizar la BD con lo que hay en disco y vincularlo con TMDB.

Lo usan el ciclo automático (Scheduler) y el job "Escanear biblioteca"
(LibraryScanJob), que hace solo esta parte sin entrar en Hispashare.
"""

from __future__ import annotations

import logging
from typing import Callable

from config.settings import Settings
from core.library.category_scanner import CategoryLibraryScanner
from core.library.scanner import LibraryScanner

logger = logging.getLogger(__name__)


def _noop(*_args, **_kwargs):
    return None


class LibrarySync:
    def __init__(
        self,
        db,
        series_repository,
        reconciliation_service,
        decision_log_repository,
        *,
        log: Callable[[str], None] = _noop,
        should_continue: Callable[[], bool] = lambda: True,
        on_matched: Callable[[int, dict], None] = _noop,
    ):
        self.db = db
        self.series_repository = series_repository
        self.reconciliation_service = reconciliation_service
        self.decision_log_repository = decision_log_repository
        self.log = log
        self.should_continue = should_continue
        self.on_matched = on_matched

    # --- disco -> BD ------------------------------------------------------
    def scan_episodic(self) -> list[dict]:
        series_list = LibraryScanner.scan_all_episodic_categories(db_manager=self.db)
        self.log("🧹 Limpiando episodios duplicados...")
        cleaned = self.db.cleanup_duplicate_episodes()
        if cleaned > 0:
            self.log(f"   ✅ {cleaned} episodios duplicados eliminados")
        for series in series_list:
            if not self.should_continue():
                break
            self.db.insert_or_update_series(series)
        return series_list

    def scan_single_file(self) -> list[dict]:
        discovered = []
        skip_paths = self.db.get_unmonitored_folder_paths()
        for category in Settings.get_single_file_categories():
            scanner = CategoryLibraryScanner(category, Settings.get_paths_for_category(category), skip_paths=skip_paths)
            for item in scanner.scan():
                if not self.should_continue():
                    return discovered
                item_data = {
                    "name": item.get("name", ""),
                    "folder_path": item.get("path", ""),
                    "content_category": category,
                }
                self.db.insert_or_update_series(item_data)
                discovered.append(item_data)
        return discovered

    # --- TMDB -------------------------------------------------------------
    def match_unmatched(self, items: list[dict] | None = None) -> int:
        """Vincula con TMDB lo que aún no tiene ficha. Devuelve cuántos."""
        from core.tmdb.api_client import TMDBClient
        from core.tmdb.auto_matcher import TMDBAutoMatcher

        if not Settings.TMDB_API_KEY:
            self.log("   ⚠️ Sin API key de TMDB: no se vincula nada")
            return 0
        matcher = TMDBAutoMatcher(TMDBClient(Settings.TMDB_API_KEY))
        matched = 0
        for series in items if items is not None else self.db.get_all_series():
            if not self.should_continue():
                break
            if series.get("tmdb_id"):
                continue
            category = series.get("content_category", "series")
            self.log(f"   🔗 Matching [{Settings.get_category_label(category)}]: {series['name']}...")
            try:
                match_result = matcher.match_content(series)
            except Exception as e:
                logger.error(f"Error haciendo match TMDB de '{series.get('name')}': {e}")
                self.log(f"      ❌ Error en match: {e}")
                continue
            if not match_result:
                self.log("      ❌ No se encontró match")
                continue

            update_result = self.series_repository.update_tmdb_identity(series["id"], match_result)
            self.reconciliation_service.on_tmdb_changed(
                series["id"],
                update_result.get("previous_item") or {},
                update_result.get("updated_item") or {},
                is_library=Settings.get_category_content_mode(category) == "single_file",
                rebuild_missing=False,
            )
            self.decision_log_repository.append(
                series["id"], "tmdb_scheduler_matched", "TMDB asignado automáticamente",
                {k: match_result.get(k) for k in ("tmdb_id", "name", "year", "confidence_score")},
            )
            matched += 1
            self.on_matched(series["id"], match_result)
            emoji = "✅" if match_result["confidence_score"] >= 0.85 else "⚠️"
            self.log(f"      {emoji} {match_result['name']} ({match_result['year']}) - "
                     f"Confianza: {match_result['confidence_score']:.1%}")
        self.log(f"   ✅ {matched} elementos vinculados con TMDB")
        return matched
