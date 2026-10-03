from __future__ import annotations

from core.database.db_manager import DatabaseManager
from core.jobs import BackgroundJob


class AgendaJob(BackgroundJob):
    """Actualiza el calendario fuera del ciclo (botón "Actualizar calendario")."""

    name = "agenda"

    def run(self):
        from core.agenda.service import AgendaService

        try:
            self._progress(10, "📅 Actualizando calendario con TMDB...")
            result = AgendaService(DatabaseManager()).rebuild()
            self._log(f"📅 Calendario: {result['episodes']} episodios de {result['series']} series en emisión")
            self._progress(100, "✅ Calendario actualizado")
            self._finished(**result)
        except Exception as e:
            self._error(str(e))
