from __future__ import annotations

from core.database.db_manager import DatabaseManager
from core.events import SOURCE_URL_SAVED
from core.jobs import BackgroundJob
from core.services.factory import build_domain_services


class SourceSearchJob(BackgroundJob):
    """"Buscar ahora en Hispashare" para un solo elemento (abre el navegador)."""

    name = "source_search"

    def __init__(self, event_bus=None, *, series_id: int, force_recheck: bool = True):
        super().__init__(event_bus)
        self.series_id = series_id
        self.force_recheck = force_recheck

    def run(self):
        try:
            db = DatabaseManager()
            self._progress(10, "🔎 Buscando en Hispashare...")
            result = build_domain_services(db, self.event_bus)["source_match_service"].revalidate_or_search(
                self.series_id, force_recheck=self.force_recheck, log_callback=self._log)
            if result.get("found") and result.get("url"):
                self._log(f"✅ Ficha de Hispashare: {result['url']}")
                self._emit(SOURCE_URL_SAVED, series_id=self.series_id, url=result["url"])
            else:
                self._log("❌ No se encontró en Hispashare")
            self._series_updated(self.series_id)
            self._progress(100, "✅ Búsqueda terminada")
            self._finished(series_id=self.series_id, found=bool(result.get("found")), url=result.get("url"))
        except Exception as e:
            self._error(str(e))
