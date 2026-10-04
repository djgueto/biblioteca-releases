from __future__ import annotations

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.events import TMDB_MATCHED
from core.jobs import BackgroundJob
from core.library.sync import LibrarySync
from core.services.factory import build_domain_services


class LibraryScanJob(BackgroundJob):
    """Botón "Escanear biblioteca": lee el disco, vincula con TMDB lo nuevo y
    recalcula faltantes, sin entrar en Hispashare ni descargar nada."""

    name = "library_scan"

    def run(self):
        try:
            db = DatabaseManager()
            services = build_domain_services(db, self.event_bus)
            sync = LibrarySync(
                db, services["series_repository"], services["reconciliation_service"],
                services["decision_log_repository"],
                log=self._log,
                should_continue=lambda: self.running,
                on_matched=lambda sid, match: self._emit(TMDB_MATCHED, series_id=sid, match=match),
            )
            self._progress(5, "📁 Escaneando series...")
            series = sync.scan_episodic()
            self._log(f"📁 {len(series)} series en disco")
            if not self.running:
                return
            self._progress(35, "🎞️ Escaneando películas y demás...")
            items = sync.scan_single_file()
            self._log(f"🎞️ {len(items)} elementos de archivo único en disco")
            if not self.running:
                return
            db.cleanup_duplicate_series()
            self._progress(60, "🎬 Vinculando con TMDB...")
            matched = sync.match_unmatched()
            total_missing = 0
            if self.running and Settings.TMDB_API_KEY:
                self._progress(85, "🔄 Recalculando episodios faltantes...")
                total_missing = services["missing_episodes_service"].rebuild_for_categories(
                    db.get_episodic_categories()).get("total_missing", 0)
            self._progress(100, "✅ Biblioteca escaneada")
            self._finished(series=len(series), items=len(items), matched=matched, total_missing=total_missing)
        except Exception as e:
            self._error(str(e))
