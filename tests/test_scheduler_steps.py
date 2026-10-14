"""
Tests del ciclo (core/scheduler.py): un paso que falla no aborta el resto.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager


class TestCycleSurvivesFailingSteps(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        with patch("core.scheduler.DatabaseManager", return_value=self.db):
            from core.scheduler import Scheduler

            self.scheduler = Scheduler()
        self.scheduler.running = True
        self.logs = []
        self.scheduler.event_bus.subscribe("TASK_LOG", lambda p: self.logs.append(p["message"]))
        self.ran = []
        s = self.scheduler
        for name in ("run_import", "scan_single_file_library", "match_tmdb", "run_discovery",
                     "update_agenda", "detect_missing", "retry_disabled_searches",
                     "search_hispashare_for_new_series", "search_hispashare_for_single_file_items",
                     "search_hispashare", "discover_search_offers", "rotate_provider_searches",
                     "detect_failed_downloads",
                     "process_downloads", "process_wanted_movies", "scan_missing_resolutions",
                     "scan_item_qualities", "process_upgrades", "scan_new_content",
                     "scan_wolfmax_categories", "relink_wolfmax"):
            setattr(s, name, lambda *a, _n=name, **k: self.ran.append(_n) or [])
        s._record_task = lambda *a, **k: None

    def tearDown(self):
        self.db.close()

    def _run(self):
        with patch("core.scheduler.build_provider_registry", return_value={}):
            self.scheduler.execute_cycle()

    def test_scan_failure_does_not_stop_downloads(self):
        self.scheduler.login_hispashare = lambda: None

        def broken_scan():
            raise OSError("[WinError 1326] El nombre de usuario o la contraseña no son correctos: 'Y:\\DIBUJOS'")

        self.scheduler.scan_library = broken_scan
        self._run()
        self.assertIn("process_downloads", self.ran)
        self.assertIn("detect_missing", self.ran)
        self.assertEqual(len(self.scheduler._cycle_warnings), 1)
        self.assertTrue(any("CICLO COMPLETADO CON 1 AVISO" in m for m in self.logs))

    def test_login_failure_skips_only_hispashare(self):
        def broken_login():
            raise Exception("Login fallido en Hispashare")

        self.scheduler.login_hispashare = broken_login
        self.scheduler.scan_library = lambda: []
        self._run()
        self.assertNotIn("search_hispashare", self.ran)
        self.assertNotIn("scan_new_content", self.ran)
        self.assertIn("process_downloads", self.ran)
        self.assertIn("discover_search_offers", self.ran)

    def test_wolfmax_offers_found_before_downloads(self):
        self.scheduler.login_hispashare = lambda: None
        self.scheduler.scan_library = lambda: []
        with patch("core.scheduler.Settings.WOLFMAX_UPGRADE_ENABLED", True, create=True), \
                patch("core.scheduler.Settings.UPGRADE_ENABLED", True, create=True):
            self._run()
        self.assertLess(self.ran.index("scan_wolfmax_categories"), self.ran.index("process_downloads"))
        self.assertLess(self.ran.index("relink_wolfmax"), self.ran.index("process_upgrades"))

    def test_clean_cycle_reports_success(self):
        self.scheduler.login_hispashare = lambda: None
        self.scheduler.scan_library = lambda: []
        self._run()
        self.assertEqual(self.scheduler._cycle_warnings, [])
        self.assertTrue(any("CICLO COMPLETADO CON ÉXITO" in m for m in self.logs))


if __name__ == "__main__":
    unittest.main()
