"""
Tests del servidor web (web/app.py, web/jobs.py) sobre una BD temporal.
"""

import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.jobs import BackgroundJob
from web.app import create_app
from web.jobs import JobAlreadyRunning, JobManager


class _GatedJob(BackgroundJob):
    """Publica un par de eventos y espera a que el test le deje terminar."""

    name = "demo"

    def __init__(self, event_bus=None, gate=None):
        super().__init__(event_bus)
        self.gate = gate

    def run(self):
        self._progress(40, "trabajando")
        self._log("hola desde el job")
        self._series_updated(1)
        while self.running and not self.gate.wait(0.01):
            pass
        self._finished(ok=True)


def _manager(gate):
    return JobManager({"demo": lambda bus: _GatedJob(bus, gate)})


class TestJobManager(unittest.TestCase):
    def test_lifecycle_state_logs_and_broadcast(self):
        gate = threading.Event()
        jobs = _manager(gate)
        messages = []
        jobs.add_listener(messages.append)

        jobs.start("demo")
        with self.assertRaises(JobAlreadyRunning):
            jobs.start("demo")
        gate.set()
        jobs._threads["demo"].join(5)

        state = jobs.state()[0]
        self.assertFalse(state["running"])
        self.assertIsNotNone(state["finished_at"])
        self.assertEqual(state["percent"], 40)
        self.assertEqual([l["message"] for l in jobs.recent_logs()], ["hola desde el job"])
        types = [m["type"] for m in messages]
        for expected in ("job", "log", "series_updated", "job_finished"):
            self.assertIn(expected, types)

    def test_stop_ends_job(self):
        jobs = _manager(threading.Event())
        jobs.start("demo")
        jobs.stop("demo")
        jobs._threads["demo"].join(5)
        self.assertFalse(jobs.state()[0]["running"])

    def test_unknown_job(self):
        with self.assertRaises(KeyError):
            _manager(threading.Event()).start("nope")


class TestWebApi(unittest.TestCase):
    def setUp(self):
        # Las peticiones abren conexiones SQLite en hilos del servidor de
        # pruebas que siguen vivas al terminar: en Windows el archivo queda
        # bloqueado, así que se ignora el error al borrar el temporal.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.patches = [
            patch.object(Settings, "DB_PATH", self.db_path),
            patch.object(Settings, "COVER_CACHE_DIR", os.path.join(self.tmp.name, "covers")),
            # Nunca llamar a TMDB de verdad desde los tests.
            patch.object(Settings, "TMDB_API_KEY", ""),
        ]
        for p in self.patches:
            p.start()
        DatabaseManager().initialize_database()
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            INSERT INTO series (id, name, folder_path, content_category, tmdb_id, tmdb_name, year, monitored,
                                hispashare_url)
            VALUES (1, 'serie local', '/media/series/a', 'series', 10, 'Serie A', 2020, 1,
                    'https://www.hispashare.org/?view=title&id=77');
            INSERT INTO series (id, name, folder_path, content_category, year, monitored)
            VALUES (2, 'Peli B', '/media/movies/b.mkv', 'movies', 2021, 0);
            INSERT INTO local_episodes (series_id, season, episode, filename, file_path, resolution_height)
            VALUES (1, 1, 1, 'a.s01e01.mkv', '/media/series/a/1.mkv', 2160),
                   (1, 1, 2, 'a.s01e02.mkv', '/media/series/a/2.mkv', 1080);
            INSERT INTO missing_episodes (series_id, series_name, season, episode, name, air_date, status)
            VALUES (1, 'Serie A', 1, 3, 'Tercero', '2026-01-01', 'missing');
        """)
        conn.commit()
        conn.close()
        self.gate = threading.Event()
        self.client = TestClient(create_app(_manager(self.gate), init_db=False))

    def tearDown(self):
        self.gate.set()
        self.client.close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_library_aggregates(self):
        items = {i["id"]: i for i in self.client.get("/api/library").json()}
        a, b = items[1], items[2]
        self.assertEqual((a["local_count"], a["missing_count"], a["max_resolution"]), (2, 1, 2160))
        self.assertEqual(a["providers"], ["hispashare"])
        self.assertTrue(a["has_cover"])
        self.assertEqual(a["category_label"], "Series")
        self.assertFalse(b["monitored"])
        self.assertNotIn("hispashare_cover_url", a)

    def test_categories_count(self):
        cats = {c["id"]: c["count"] for c in self.client.get("/api/categories").json()}
        self.assertEqual((cats["series"], cats["movies"], cats["music"]), (1, 1, 0))

    def test_series_detail_groups_seasons(self):
        detail = self.client.get("/api/series/1").json()
        self.assertEqual(len(detail["seasons"]), 1)
        season = detail["seasons"][0]
        self.assertEqual([e["episode"] for e in season["local"]], [1, 2])
        self.assertEqual(season["missing"], [{"episode": 3, "name": "Tercero", "air_date": "2026-01-01"}])
        self.assertEqual(detail["cover_url"], "https://www.hispashare.org/images/titles/77/cover.jpg")
        self.assertEqual(self.client.get("/api/series/999").status_code, 404)

    def test_toggle_monitored_invalidates_library(self):
        self.client.get("/api/library")
        r = self.client.put("/api/series/1/monitored", json={"monitored": False})
        self.assertEqual(r.status_code, 200)
        items = {i["id"]: i for i in self.client.get("/api/library").json()}
        self.assertFalse(items[1]["monitored"])
        history = self.client.get("/api/series/1").json()["history"]
        self.assertEqual(history[0]["decision_type"], "monitored_changed")

    def test_wanted_excludes_unmonitored(self):
        self.assertEqual(len(self.client.get("/api/wanted").json()), 1)
        self.client.put("/api/series/1/monitored", json={"monitored": False})
        self.assertEqual(self.client.get("/api/wanted").json(), [])

    def test_cover_served_from_cache(self):
        from core.covers import cover_cache_path

        url = "https://www.hispashare.org/images/titles/77/cover.jpg"
        path = cover_cache_path(1, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8jpeg")
        r = self.client.get("/api/series/1/cover")
        self.assertEqual((r.status_code, r.content), (200, b"\xff\xd8jpeg"))
        self.assertEqual(self.client.get("/api/series/2/cover").status_code, 404)

    def test_jobs_endpoints_and_websocket(self):
        with self.client.websocket_connect("/api/ws") as ws:
            self.assertEqual(ws.receive_json()["type"], "hello")
            self.assertEqual(self.client.post("/api/jobs/demo/start").status_code, 200)
            self.assertEqual(self.client.post("/api/jobs/demo/start").status_code, 409)
            seen = set()
            while "log" not in seen:
                seen.add(ws.receive_json()["type"])
        self.assertEqual(self.client.post("/api/jobs/nope/start").status_code, 404)
        self.assertEqual(self.client.get("/api/logs").json()[-1]["message"], "hola desde el job")

    def test_scheduler_blocked_when_db_paths_do_not_match(self):
        gate = threading.Event()
        jobs = JobManager({"scheduler": lambda bus: _GatedJob(bus, gate)})
        client = TestClient(create_app(jobs, init_db=False))
        try:
            with patch.object(Settings, "get_paths_for_category", lambda c: ["/otra/biblioteca"]):
                r = client.post("/api/jobs/scheduler/start")
                self.assertEqual(r.status_code, 409)
                self.assertFalse(client.get("/api/status").json()["library_paths"]["ok"])
            with patch.object(Settings, "get_paths_for_category", lambda c: ["/media"]):
                self.assertEqual(client.post("/api/jobs/scheduler/start").status_code, 200)
        finally:
            gate.set()
            client.close()

    def test_quality_profile_settings(self):
        data = self.client.get("/api/settings/quality").json()
        self.assertEqual(data["order"][0], "2160p_dv")
        order = list(reversed(data["order"]))
        r = self.client.put("/api/settings/quality", json={"order": order, "minimum": "720p"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["order"], order)
        self.assertEqual(self.client.put("/api/settings/quality", json={"order": ["2160p"], "minimum": "sd"}).status_code, 422)

    def test_general_settings_write_only_changed_keys(self):
        import config.settings as settings_module

        env = Path(self.tmp.name) / "test.env"
        env.write_text("# mi config\nTMDB_API_KEY=abcdef123456\nOTRA=1\n", encoding="utf-8")
        with patch.object(settings_module, "ENV_PATH", env), \
                patch.object(Settings, "TMDB_API_KEY", "abcdef123456"), \
                patch.object(Settings, "DONTORRENT_URL", "https://dontorrent.moi"), \
                patch.object(Settings, "DONTORRENT_ENABLED", False), \
                patch.dict(os.environ, {}, clear=False):
            data = self.client.get("/api/settings/general").json()
            self.assertEqual(data["tmdb_api_key"], "••••••3456")  # nunca entera
            r = self.client.put("/api/settings/general", json={
                "tmdb_api_key": data["tmdb_api_key"],  # sin tocar
                "dontorrent_enabled": True, "dontorrent_url": "https://dontorrent.nuevo/"})
            self.assertEqual(r.status_code, 200)
            text = env.read_text(encoding="utf-8")
            self.assertIn("# mi config", text)
            self.assertIn("TMDB_API_KEY=abcdef123456", text)
            self.assertIn("DONTORRENT_ENABLED=True", text)
            self.assertIn("DONTORRENT_URL=https://dontorrent.nuevo", text)
            self.assertTrue(Settings.DONTORRENT_ENABLED)
            with patch("web.app._tmdb_key_works", return_value=False):
                self.assertEqual(self.client.put("/api/settings/general", json={"tmdb_api_key": "mala"}).status_code, 422)
            self.assertEqual(self.client.put("/api/settings/general", json={"dontorrent_url": "dontorrent.x"}).status_code, 422)

    def test_edit_hispashare_url(self):
        url = "https://www.hispashare.org/?view=title&id=555"
        self.assertEqual(self.client.put("/api/series/2/hispashare_url", json={"url": url}).status_code, 200)
        detail = self.client.get("/api/series/2").json()
        self.assertEqual(detail["hispashare_url"], url)
        self.assertEqual(detail["cover_url"], "https://www.hispashare.org/images/titles/555/cover.jpg")
        self.assertEqual(detail["history"][0]["decision_type"], "source_set_manual")
        self.client.put("/api/series/2/hispashare_url", json={"url": ""})
        self.assertFalse(self.client.get("/api/series/2").json()["hispashare_url"])
        self.assertEqual(self.client.put("/api/series/2/hispashare_url", json={"url": "ftp://x"}).status_code, 422)

    def test_cover_prefers_tmdb_poster(self):
        from core.tmdb.metadata import TmdbMetaRepository

        TmdbMetaRepository(DatabaseManager()).save({"tmdb_id": 10, "media_type": "tv", "poster_path": "/p.jpg"})
        with patch("web.app.get_cover_bytes", side_effect=lambda sid, url: b"tmdb" if "image.tmdb.org" in url else b"h") as get:
            r = self.client.get("/api/series/1/cover")
        self.assertEqual(r.content, b"tmdb")
        self.assertEqual(get.call_args.args[1], "https://image.tmdb.org/t/p/w342/p.jpg")
        with patch("web.app.get_cover_bytes", side_effect=lambda sid, url: None if "tmdb" in url else b"h"):
            self.assertEqual(self.client.get("/api/series/1/cover").content, b"h")  # respaldo Hispashare

    def test_spa_fallback_and_static(self):
        self.assertIn("app.js", self.client.get("/series/1").text)
        self.assertEqual(self.client.get("/static/vendor/preact-htm.module.js").status_code, 200)
        self.assertEqual(self.client.get("/api/nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
