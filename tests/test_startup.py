"""
Tests del arranque: migraciones versionadas, el ciclo que no se repite al
reiniciar y las tareas que el ciclo comparte con las programadas.
"""

import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.events import TASK_LOG
from core.importer import service as import_service
from core.importer.service import ImportService, ImportSettings
from core.jobs import task_runs
from core.scheduler import Scheduler


class _TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(Settings, "DB_PATH", os.path.join(self.tmp.name, "t.db"))
        self.db_patch.start()
        self.db = DatabaseManager()
        self.db.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()


class TestSchemaVersion(_TempDb):
    def test_second_start_skips_migrations(self):
        conn = self.db.get_connection()
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], DatabaseManager.SCHEMA_VERSION)
        with patch("core.database.db_manager.ensure_placeholder_column") as migrate:
            DatabaseManager().initialize_database()
        migrate.assert_not_called()

    def test_old_database_is_migrated_once(self):
        conn = self.db.get_connection()
        conn.execute("PRAGMA user_version = 0")
        conn.execute("UPDATE series SET search_disabled = 0")  # BD ya existente, sin versión
        DatabaseManager().initialize_database()
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], DatabaseManager.SCHEMA_VERSION)


class TestTaskRuns(_TempDb):
    def test_record_never_goes_back(self):
        now = datetime(2026, 10, 3, 12, 0)
        task_runs.record_run(self.db, "discovery", now)
        task_runs.record_run(self.db, "discovery", now - timedelta(hours=1))
        self.assertEqual(task_runs.last_runs(self.db)["discovery"], now)

    def test_ran_recently_uses_task_interval(self):
        now = datetime(2026, 10, 3, 12, 0)
        task_runs.record_run(self.db, "discovery", now)
        self.assertTrue(task_runs.ran_recently(self.db, "discovery", now + timedelta(minutes=359)))
        self.assertFalse(task_runs.ran_recently(self.db, "discovery", now + timedelta(minutes=361)))
        self.assertFalse(task_runs.ran_recently(self.db, "agenda", now))  # nunca se ejecutó


class TestSchedulerStartup(_TempDb):
    def _scheduler(self):
        scheduler = Scheduler(wait_for_interval=True)
        self.logs = []
        scheduler.event_bus.subscribe(TASK_LOG, lambda p: self.logs.append(p["message"]))
        return scheduler

    def test_waits_for_the_rest_of_the_interval(self):
        task_runs.record_run(self.db, "scheduler", datetime.now() - timedelta(hours=1))
        scheduler = self._scheduler()
        with patch.object(Settings, "CHECK_INTERVAL_HOURS", 12), \
                patch("core.scheduler.time.sleep", side_effect=lambda _: scheduler.stop()) as sleep:
            scheduler._wait_until_due()
        sleep.assert_called()
        self.assertTrue(any("Próximo ciclo" in m for m in self.logs))

    def test_overdue_or_first_time_runs_right_away(self):
        scheduler = self._scheduler()
        with patch("core.scheduler.time.sleep") as sleep:
            scheduler._wait_until_due()  # nunca hubo ciclo
            task_runs.record_run(self.db, "scheduler", datetime.now() - timedelta(hours=13))
            with patch.object(Settings, "CHECK_INTERVAL_HOURS", 12):
                scheduler._wait_until_due()
        sleep.assert_not_called()

    def test_cycle_skips_discovery_done_recently_by_the_task(self):
        task_runs.record_run(self.db, "discovery", datetime.now())
        scheduler = self._scheduler()
        with patch("core.discovery.service.DiscoveryService.run") as run:
            scheduler.run_discovery()
        run.assert_not_called()

    def test_cycle_records_what_it_runs(self):
        scheduler = self._scheduler()
        with patch("core.agenda.service.AgendaService.rebuild", return_value={"episodes": 0, "series": 0}):
            scheduler.update_agenda()
        self.assertIsNotNone(task_runs.last_runs(self.db).get("agenda"))


class TestImportLock(_TempDb):
    def test_second_import_at_the_same_time_is_skipped(self):
        settings = ImportSettings(enabled=True, source_dirs=[self.tmp.name])
        inside, release = threading.Event(), threading.Event()

        def slow_run(self_, summary):
            inside.set()
            release.wait(5)
            return summary

        with patch.object(ImportService, "_run", slow_run):
            first = threading.Thread(target=lambda: ImportService(DatabaseManager(), settings=settings).run())
            first.start()
            inside.wait(5)
            logs = []
            ImportService(self.db, settings=settings, log=logs.append).run()
            release.set()
            first.join(5)
        self.assertTrue(any("Ya hay una importación" in m for m in logs))
        self.assertFalse(import_service._RUN_LOCK.locked())


if __name__ == "__main__":
    unittest.main()
