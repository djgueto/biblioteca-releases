"""
Tests de core/jobs/library_lock.py: las tareas pesadas no se pisan.
"""

import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.jobs import BackgroundJob, library_lock


class _Job(BackgroundJob):
    name = "test"

    def __init__(self):
        super().__init__()
        self.logs = []
        self.event_bus.subscribe("TASK_LOG", lambda p: self.logs.append(p["message"]))


class TestLibraryLock(unittest.TestCase):
    def test_second_job_waits_for_the_first(self):
        first, second = _Job(), _Job()
        order = []
        inside = threading.Event()
        release = threading.Event()

        def long_task():
            order.append("primera empieza")
            inside.set()
            release.wait(5)
            order.append("primera acaba")

        t = threading.Thread(target=library_lock.run_exclusive, args=(first, long_task, "Ciclo automático"))
        t.start()
        inside.wait(5)
        t2 = threading.Thread(target=library_lock.run_exclusive,
                              args=(second, lambda: order.append("segunda"), "Analizar calidad"))
        t2.start()
        time.sleep(0.2)
        self.assertEqual(order, ["primera empieza"])
        release.set()
        t.join(5)
        t2.join(5)
        self.assertEqual(order, ["primera empieza", "primera acaba", "segunda"])
        self.assertTrue(any("Ciclo automático" in m for m in second.logs))
        self.assertIsNone(library_lock.holder())

    def test_stopped_while_waiting_does_not_run(self):
        first, second = _Job(), _Job()
        inside = threading.Event()
        release = threading.Event()
        t = threading.Thread(target=library_lock.run_exclusive,
                             args=(first, lambda: (inside.set(), release.wait(5)), "Ciclo"))
        t.start()
        inside.wait(5)
        ran = []
        t2 = threading.Thread(target=library_lock.run_exclusive, args=(second, lambda: ran.append(1), "Otra"))
        t2.start()
        second.stop()
        t2.join(5)
        release.set()
        t.join(5)
        self.assertFalse(t2.is_alive())
        self.assertEqual(ran, [])

    def test_lock_released_after_error(self):
        def boom():
            raise RuntimeError("fallo")

        with self.assertRaises(RuntimeError):
            library_lock.run_exclusive(_Job(), boom, "X")
        self.assertEqual(library_lock.run_exclusive(_Job(), lambda: 7, "Y"), 7)


if __name__ == "__main__":
    unittest.main()
