"""
Tests de EventBus y BackgroundJob (los jobs no dependen de ninguna UI).
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

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


if __name__ == "__main__":
    unittest.main()
