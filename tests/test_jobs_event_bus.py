"""
Tests para el desacoplamiento core <-> Qt: EventBus, BackgroundJob y los
adaptadores QThread de ui/workers/job_threads.py.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from PyQt6.QtCore import QCoreApplication

from core.events import (
    SERIES_UPDATED,
    SOURCE_URL_SAVED,
    TASK_ERROR,
    TASK_FINISHED,
    TASK_LOG,
    TASK_PROGRESS,
    TMDB_MATCHED,
    EventBus,
)
from core.jobs import BackgroundJob
from core.tmdb.recalc_job import TMDBRecalcJob

_app = QCoreApplication.instance() or QCoreApplication([])


class TestEventBus(unittest.TestCase):
    def test_failing_subscriber_does_not_break_publisher_or_others(self):
        bus = EventBus()
        received = []

        def broken(payload):
            raise RuntimeError("boom")

        bus.subscribe("E", broken)
        bus.subscribe("E", received.append)
        with self.assertLogs("core.events.event_bus", level="ERROR"):
            bus.publish("E", {"x": 1})
        self.assertEqual(received, [{"x": 1}])

    def test_unsubscribe_handle(self):
        bus = EventBus()
        received = []
        unsubscribe = bus.subscribe("E", received.append)
        unsubscribe()
        bus.publish("E", {})
        self.assertEqual(received, [])


class _DemoJob(BackgroundJob):
    name = "demo"

    def run(self):
        self._progress(50, "mitad")
        self._log("hola")
        self._series_updated(7)
        self._finished(count=3)


class TestBackgroundJob(unittest.TestCase):
    def test_events_carry_source(self):
        job = _DemoJob()
        events = []
        for event_type in (TASK_PROGRESS, TASK_LOG, SERIES_UPDATED, TASK_FINISHED):
            job.event_bus.subscribe(event_type, lambda p, t=event_type: events.append((t, p)))
        job.run()
        self.assertEqual(events, [
            (TASK_PROGRESS, {"source": "demo", "percent": 50, "message": "mitad"}),
            (TASK_LOG, {"source": "demo", "message": "hola"}),
            (SERIES_UPDATED, {"source": "demo", "series_id": 7}),
            (TASK_FINISHED, {"source": "demo", "result": {"count": 3}}),
        ])

    def test_stop_clears_running(self):
        job = _DemoJob()
        job.stop()
        self.assertFalse(job.running)

    def test_recalc_without_api_key_reports_error(self):
        job = TMDBRecalcJob()
        errors = []
        job.event_bus.subscribe(TASK_ERROR, errors.append)
        with patch("core.tmdb.recalc_job.Settings.TMDB_API_KEY", ""):
            job.run()
        self.assertEqual(errors, [{"source": "tmdb_recalc", "error": "TMDB_API_KEY no configurada"}])


class TestQtJobThreads(unittest.TestCase):
    def _scheduler_thread(self):
        from ui.workers.job_threads import SchedulerThread

        with patch("core.scheduler.DatabaseManager", MagicMock()):
            return SchedulerThread()

    def test_scheduler_events_become_signals(self):
        thread = self._scheduler_thread()
        got = []
        thread.progress_signal.connect(lambda v, m: got.append(("progress", v, m)))
        thread.log_signal.connect(lambda m: got.append(("log", m)))
        thread.url_saved_signal.connect(lambda sid, url: got.append(("url", sid, url)))
        thread.tmdb_matched_signal.connect(lambda sid, data: got.append(("tmdb", sid, data)))
        thread.series_updated_signal.connect(lambda sid: got.append(("series", sid)))
        thread.finished_signal.connect(lambda: got.append(("finished",)))

        job = thread.job
        job._progress(5, "login")
        job._log("msg")
        job._emit(SOURCE_URL_SAVED, series_id=1, url="http://x")
        job._emit(TMDB_MATCHED, series_id=2, match={"tmdb_id": 9})
        job._series_updated(3)
        job._finished()

        self.assertEqual(got, [
            ("progress", 5, "login"),
            ("log", "msg"),
            ("url", 1, "http://x"),
            ("tmdb", 2, {"tmdb_id": 9}),
            ("series", 3),
            ("finished",),
        ])

    def test_service_events_without_source_are_not_bridged(self):
        # ReconciliationService publica SERIES_UPDATED en el mismo bus sin
        # "source"; antes no llegaban a la UI y deben seguir sin llegar.
        thread = self._scheduler_thread()
        got = []
        thread.series_updated_signal.connect(got.append)
        thread.job.event_bus.publish(SERIES_UPDATED, {"series_id": 4})
        self.assertEqual(got, [])

    def test_tmdb_recalc_finished_unpacks_result(self):
        from ui.workers.job_threads import TMDBRecalcThread

        thread = TMDBRecalcThread()
        got = []
        thread.finished_signal.connect(lambda *args: got.append(args))
        thread.job._finished(total=10, updated=8, failed=2)
        self.assertEqual(got, [(10, 8, 2)])


if __name__ == "__main__":
    unittest.main()
