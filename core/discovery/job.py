from __future__ import annotations

from core.database.db_manager import DatabaseManager
from core.jobs import BackgroundJob


class DiscoveryJob(BackgroundJob):
    """Revisa las novedades fuera del ciclo (botón "Buscar novedades")."""

    name = "discovery"

    def run(self):
        from core.discovery.service import DiscoveryService

        try:
            self._progress(10, "🆕 Revisando novedades de TMDB/IMDb...")
            db = DatabaseManager()
            result = DiscoveryService(db, log=self._log).run()
            self._progress(100, "✅ Novedades revisadas")
            self._finished(**{k: v for k, v in result.items() if k != "evaluated"})
        except Exception as e:
            self._error(str(e))
