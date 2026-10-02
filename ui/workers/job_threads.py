"""Adaptadores QThread para los jobs de core/jobs.

Los jobs publican en su EventBus desde el hilo de trabajo; aquí cada evento
se reemite como pyqtSignal. Como el QThread vive en el hilo principal y la
emisión ocurre en el hilo de trabajo, Qt encola la entrega a los slots de la
ventana (AutoConnection), igual que cuando los jobs emitían las señales
directamente.
"""

from PyQt6.QtCore import QThread, pyqtSignal

from core.events import (
    SERIES_UPDATED,
    SOURCE_URL_SAVED,
    TASK_ERROR,
    TASK_FINISHED,
    TASK_LOG,
    TASK_PROGRESS,
    TMDB_MATCHED,
)
from core.jobs import BackgroundJob
from core.scheduler import Scheduler
from core.tmdb.recalc_job import TMDBRecalcJob


class JobThread(QThread):
    progress_signal = pyqtSignal(int, str)
    log_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    series_updated_signal = pyqtSignal(int)

    def __init__(self, job: BackgroundJob):
        super().__init__()
        self.job = job
        self._bridge(TASK_PROGRESS, lambda p: self.progress_signal.emit(p["percent"], p["message"]))
        self._bridge(TASK_LOG, lambda p: self.log_signal.emit(p["message"]))
        self._bridge(TASK_ERROR, lambda p: self.error_signal.emit(p["error"]))
        self._bridge(SERIES_UPDATED, lambda p: self.series_updated_signal.emit(p["series_id"]))
        self._bridge(TASK_FINISHED, lambda p: self._emit_finished(p.get("result") or {}))

    def _bridge(self, event_type, handler):
        # Solo los eventos de este job: los servicios que comparten bus
        # (ReconciliationService) publican SERIES_UPDATED sin "source".
        def callback(payload):
            if payload.get("source") == self.job.name:
                handler(payload)

        self.job.event_bus.subscribe(event_type, callback)

    def _emit_finished(self, result: dict):
        raise NotImplementedError

    def run(self):
        self.job.run()

    def stop(self):
        self.job.stop()
        self.requestInterruption()


class SchedulerThread(JobThread):
    finished_signal = pyqtSignal()
    url_saved_signal = pyqtSignal(int, str)  # series_id, url
    tmdb_matched_signal = pyqtSignal(int, dict)  # series_id, match_data

    def __init__(self):
        super().__init__(Scheduler())
        self._bridge(SOURCE_URL_SAVED, lambda p: self.url_saved_signal.emit(p["series_id"], p["url"]))
        self._bridge(TMDB_MATCHED, lambda p: self.tmdb_matched_signal.emit(p["series_id"], p["match"]))

    def _emit_finished(self, result: dict):
        self.finished_signal.emit()


class TMDBRecalcThread(JobThread):
    finished_signal = pyqtSignal(int, int, int)  # total, updated, failed

    def __init__(self):
        super().__init__(TMDBRecalcJob())

    def _emit_finished(self, result: dict):
        self.finished_signal.emit(result["total"], result["updated"], result["failed"])
