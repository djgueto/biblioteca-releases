from __future__ import annotations

from core.database.db_manager import DatabaseManager
from core.jobs import BackgroundJob
from core.library.quality_scan import coverage, measure_episodes, measure_items

BATCH = 200


class QualityScanJob(BackgroundJob):
    """Analiza con MediaInfo todos los archivos pendientes, sin esperar a
    los ciclos (que solo hacen una tanda cada vez)."""

    name = "quality_scan"

    def run(self):
        try:
            db = DatabaseManager()
            totals = {"measured": 0, "unreadable": 0}
            for label, measure in (("episodios", measure_episodes), ("películas y documentales", measure_items)):
                while self.running:
                    result = measure(db, limit=BATCH, should_stop=lambda: not self.running)
                    totals["measured"] += result["measured"]
                    totals["unreadable"] += result["unreadable"]
                    self._report(db, label)
                    if not result["pending"] or not (result["measured"] or result["unreadable"]):
                        break
            if totals["measured"] or totals["unreadable"]:
                self._log(f"✅ Análisis de calidad: {totals['measured']} analizados, {totals['unreadable']} sin poder leer")
            self._finished(**totals)
        except Exception as e:
            self._error(str(e))

    def _report(self, db, label: str):
        cov = coverage(db)
        done = cov["episodes"]["measured"] + cov["items"]["measured"]
        total = cov["episodes"]["total"] + cov["items"]["total"]
        percent = int(100 * done / total) if total else 100
        self._progress(percent, f"🔬 Analizando {label}: {done}/{total} archivos con calidad leída")
