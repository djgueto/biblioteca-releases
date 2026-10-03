"""
Tests de las tareas programadas de la web (web/periodic.py).
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.jobs import BackgroundJob
from web.jobs import JobManager
from web.periodic import PeriodicRunner


class _Quick(BackgroundJob):
    def run(self):
        self._finished()


class TestPeriodicRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(Settings, "DB_PATH", os.path.join(self.tmp.name, "t.db"))
        self.db_patch.start()
        DatabaseManager().initialize_database()
        self.clock = [datetime(2026, 10, 3, 12, 0)]
        # El gestor de tareas sella el inicio con su reloj: el mismo reloj simulado.
        self.jobs_clock = patch("web.jobs._now", lambda: self.clock[0].isoformat(timespec="seconds"))
        self.jobs_clock.start()
        self.started = []

        def factory(name):
            def make(bus):
                job = _Quick(bus)
                job.name = name
                self.started.append(name)
                return job
            return make

        names = ("importer", "quality_scan", "discovery", "agenda")
        self.jobs = JobManager({n: factory(n) for n in names})
        self.enabled = {"importer": True, "discovery": True}
        self.runner = PeriodicRunner(self.jobs, now=lambda: self.clock[0],
                                     conditions={k: (lambda k=k: self.enabled[k]) for k in self.enabled})

    def tearDown(self):
        self.jobs.stop_all(timeout=2)
        self.jobs_clock.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def _advance(self, minutes):
        self.clock[0] += timedelta(minutes=minutes)

    def _tick(self):
        launched = self.runner.tick()
        for t in list(self.jobs._threads.values()):
            t.join(2)
        return launched

    def test_staggered_start_then_each_at_its_own_pace(self):
        self.assertEqual(self._tick(), [])  # nada nada más arrancar
        self._advance(5)
        self.assertEqual(sorted(self._tick()), ["agenda", "discovery", "importer", "quality_scan"])
        self._advance(15)
        self.assertEqual(self._tick(), ["importer"])
        self._advance(45)
        self.assertEqual(sorted(self._tick()), ["importer", "quality_scan"])

    def test_switch_off_and_zero_interval_are_respected(self):
        self.enabled["importer"] = False
        self.runner.set_intervals({"agenda": 0})
        self._advance(5)
        self.assertEqual(sorted(self._tick()), ["discovery", "quality_scan"])
        status = {t["name"]: t for t in self.runner.status()}
        self.assertFalse(status["importer"]["enabled"])
        self.assertIsNone(status["agenda"]["next_run"])

    def test_manual_run_reschedules_and_last_run_survives_restart(self):
        self.jobs.start("discovery")  # a mano
        for t in list(self.jobs._threads.values()):
            t.join(2)
        restarted = PeriodicRunner(self.jobs, now=lambda: self.clock[0], conditions={})
        self.assertEqual(restarted.next_runs()["discovery"], self.clock[0] + timedelta(minutes=360))

    def test_unknown_task_rejected(self):
        with self.assertRaises(KeyError):
            self.runner.set_intervals({"nope": 5})


if __name__ == "__main__":
    unittest.main()
