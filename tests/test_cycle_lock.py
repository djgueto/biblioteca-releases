"""
Tests del bloqueo de ciclo entre procesos (core/jobs/cycle_lock.py).
"""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.jobs.cycle_lock import CycleLock


class TestCycleLock(unittest.TestCase):
    def setUp(self):
        # Archivo real (no :memory:): dos "procesos" = dos conexiones distintas.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = os.path.join(self.tmp.name, "lock.db")
        self.web = DatabaseManager(self.path)
        self.desktop = DatabaseManager(self.path)
        # Cada DatabaseManager de este hilo comparte conexión por ruta; para
        # simular dos procesos se fuerzan conexiones propias.
        self.web.conn = self.web._open_connection()
        self.desktop.conn = self.desktop._open_connection()

    def tearDown(self):
        for db in (self.web, self.desktop):
            db.conn.close()
        self.tmp.cleanup()

    def test_only_one_holder(self):
        a, b = CycleLock(self.web), CycleLock(self.desktop)
        self.assertTrue(a.acquire())
        self.assertFalse(b.acquire())
        self.assertEqual(b.holder(), a.owner)
        self.assertTrue(a.acquire())  # el mismo dueño puede renovarlo
        a.release()
        self.assertTrue(b.acquire())

    def test_stale_lock_can_be_taken(self):
        a, b = CycleLock(self.web), CycleLock(self.desktop)
        a.acquire()
        self.web.conn.execute("UPDATE cycle_lock SET heartbeat = datetime('now', '-31 minutes')")
        self.web.conn.commit()
        self.assertIsNone(b.holder())
        self.assertTrue(b.acquire())

    def test_lock_of_dead_local_process_is_taken_at_once(self):
        a, b = CycleLock(self.web), CycleLock(self.desktop)
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        a.owner = f"{socket.gethostname()}:{child.pid}:abcd1234"  # se cerró sin soltarlo
        a.acquire()
        self.assertIsNone(b.holder())
        self.assertTrue(b.acquire())

    def test_lock_of_live_or_remote_process_is_kept(self):
        for owner in (f"{socket.gethostname()}:{os.getpid()}:abcd1234", "otro-pc:999999:abcd1234"):
            a, b = CycleLock(self.web), CycleLock(self.desktop)
            a.owner = owner
            a.acquire()
            self.assertEqual(b.holder(), owner)
            self.assertFalse(b.acquire())
            a.release()

    def test_heartbeat_keeps_it_alive(self):
        a, b = CycleLock(self.web), CycleLock(self.desktop)
        a.acquire()
        self.web.conn.execute("UPDATE cycle_lock SET heartbeat = datetime('now', '-29 minutes')")
        self.web.conn.commit()
        a.heartbeat()
        self.assertFalse(b.acquire())


class TestSchedulerWaitsForOtherProcess(unittest.TestCase):
    def test_cycle_does_not_run_while_other_process_holds_lock(self):
        db = DatabaseManager(":memory:")
        db.initialize_database()
        with patch("core.scheduler.DatabaseManager", return_value=db):
            from core.scheduler import Scheduler

            scheduler = Scheduler()
        other = CycleLock(db)
        other.owner = "otro-proceso"
        other.acquire()
        logs = []
        scheduler.event_bus.subscribe("TASK_LOG", lambda p: logs.append(p["message"]))
        ran = []
        scheduler.execute_cycle = lambda: ran.append(True)
        with patch("core.scheduler.time.sleep", side_effect=lambda s: scheduler.stop()):
            scheduler.run()
        self.assertEqual(ran, [])
        self.assertTrue(any("otro proceso" in m for m in logs))
        db.close()


if __name__ == "__main__":
    unittest.main()
