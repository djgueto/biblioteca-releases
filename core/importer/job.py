from __future__ import annotations

from core.database.db_manager import DatabaseManager
from core.jobs import BackgroundJob


class ImportJob(BackgroundJob):
    """Importa lo descargado a la biblioteca fuera del ciclo."""

    name = "importer"

    def run(self):
        from core.importer.service import ImportService

        try:
            service = ImportService(DatabaseManager(), log=self._log)
            if not service.settings.enabled:
                self._log("📥 La importación automática está desactivada (Ajustes → Importación)")
                self._finished(imported=0)
                return
            self._progress(10, "📥 Buscando descargas terminadas...")
            result = service.run()
            self._log(f"📥 Importación: {result['imported']} nuevos, {result['upgraded']} mejoras, "
                      f"{result['skipped']} omitidos")
            self._progress(100, "✅ Importación terminada")
            self._finished(**result)
        except Exception as e:
            self._error(str(e))
