"""
Ficha de Hispashare equivocada: descartarla, que no se vuelva a elegir sola,
apagar/encender la búsqueda a mano y reintentar cada mes lo que no se encontró.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.hispashare.searcher import HispashareSearcher
from core.repositories import SourceOfferRepository, SourceRepository
from core.repositories.source_repository import source_key
from core.services.providers.base import SourceOffer
from core.services.quality import Quality
from core.services.source_arbitration_service import SourceArbitrationService
from web.app import create_app
from web.jobs import JobManager

WRONG = "https://www.hispashare.org/?view=title&id=77"
RIGHT = "https://www.hispashare.org/?view=title&id=88"


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmp.name, "t.db")
        self.patches = [patch.object(Settings, "DB_PATH", self.db_path), patch.object(Settings, "TMDB_API_KEY", "")]
        for p in self.patches:
            p.start()
        self.db = DatabaseManager()
        self.db.initialize_database()
        conn = sqlite3.connect(self.db_path)
        conn.executescript(f"""
            INSERT INTO series (id, name, folder_path, content_category, tmdb_id, tmdb_name, monitored, hispashare_url)
            VALUES (1, 'Serie A', '/media/series/a', 'series', 10, 'Serie A', 1, '{WRONG}');
            INSERT INTO series (id, name, folder_path, content_category, monitored) VALUES
                (2, 'Vieja', '/media/series/b', 'series', 1),
                (3, 'Reciente', '/media/series/c', 'series', 1),
                (4, 'Apagada', '/media/series/d', 'series', 1);
        """)
        conn.commit()
        conn.close()
        SourceOfferRepository(self.db).upsert(1, SourceOffer(provider="hispashare", source_url=WRONG, quality=Quality()))
        self.client = TestClient(create_app(JobManager({}), init_db=False))

    def tearDown(self):
        self.client.close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()


class TestRejectFromWeb(_Base):
    def test_reject_clears_link_and_offer_and_remembers_it(self):
        r = self.client.post("/api/series/1/hispashare_reject")
        self.assertEqual((r.status_code, r.json()["rejected_url"]), (200, WRONG))
        detail = self.client.get("/api/series/1").json()
        self.assertFalse(detail["hispashare_url"])
        self.assertEqual(detail["offers"], [])  # la oferta no queda viva para el ciclo
        self.assertEqual([s["source_url"] for s in detail["rejected_sources"]], [WRONG])
        self.assertIn("source_rejected", [h["decision_type"] for h in detail["history"]])
        self.assertFalse(detail["search_disabled"])  # se vuelve a buscar la buena
        self.assertEqual(self.client.post("/api/series/1/hispashare_reject").status_code, 409)

    def test_other_sources_are_untouched(self):
        wolfmax = "https://wolfmax4k.com/serie/serie-a"
        offers = SourceOfferRepository(self.db)
        offers.upsert(1, SourceOffer(provider="wolfmax", source_url=wolfmax, quality=Quality(resolution_height=2160)))
        conn = self.db.get_connection()
        conn.execute("INSERT INTO missing_episodes (series_id, season, episode, status) VALUES (1, 1, 3, 'missing')")
        conn.commit()

        self.client.post("/api/series/1/hispashare_reject")
        self.client.put("/api/series/1/hispashare_search", json={"enabled": False})

        self.assertEqual([(o.provider, o.source_url) for o in offers.get_all_for_series(1)], [("wolfmax", wolfmax)])
        # Sigue en la cola de descargas (tiene oferta de otra fuente)...
        self.assertIn(1, [s["id"] for s in self.db.get_series_with_missing_episodes_and_any_offer()])
        # ...y DonTorrent la sigue buscando aunque Hispashare esté apagado.
        self.assertIn(1, [s["id"] for s in offers.series_missing_without_offer("dontorrent", ["series"])])

    def test_automatic_match_never_reuses_a_rejected_link(self):
        self.client.post("/api/series/1/hispashare_reject")
        sources = SourceRepository(self.db)
        # Otra forma de escribir la misma ficha: también cuenta como rechazada.
        self.assertFalse(sources.save_match(1, {"source_url": "https://hispashare.org/?id=77&view=title"}))
        self.assertFalse(self.db.get_series_by_id(1)["hispashare_url"])
        self.assertTrue(sources.save_match(1, {"source_url": RIGHT}))

    def test_putting_it_back_by_hand_wins(self):
        self.client.post("/api/series/1/hispashare_reject")
        self.assertEqual(self.client.put("/api/series/1/hispashare_url", json={"url": WRONG}).status_code, 200)
        detail = self.client.get("/api/series/1").json()
        self.assertEqual((detail["hispashare_url"], detail["rejected_sources"]), (WRONG, []))

    def test_clearing_the_link_also_forgets_the_offer(self):
        self.client.put("/api/series/1/hispashare_url", json={"url": ""})
        self.assertEqual(SourceOfferRepository(self.db).get_all_for_series(1), [])

    def test_offer_refresh_does_not_revive_a_rejected_link(self):
        SourceRepository(self.db).reject(1, WRONG)
        provider = MagicMock()
        service = SourceArbitrationService(self.db, SourceOfferRepository(self.db), {"hispashare": provider})
        service.refresh_all_offers(1, {"hispashare_url": WRONG})
        provider.refresh_offer.assert_not_called()
        self.assertEqual(SourceOfferRepository(self.db).get_all_for_series(1), [])


class TestSearcherSkipsRejected(_Base):
    def test_rejected_results_are_ignored(self):
        SourceRepository(self.db).reject(1, WRONG)
        searcher = HispashareSearcher(driver=None, db_manager=self.db)
        seen = []

        def best(results, *args):
            seen.extend(r["url"] for r in results)
            return results[0] if results else None

        with patch.object(searcher, "perform_search"), patch("core.hispashare.searcher.time.sleep"), \
                patch.object(searcher, "extract_results", return_value=[
                    {"url": "/?view=title&id=77", "title": "Serie A"}, {"url": RIGHT, "title": "Serie A"}]), \
                patch.object(searcher, "select_best_match", side_effect=best):
            result = searcher._search_with_mode("Serie A", None, 1, "episodic")
        self.assertEqual(result["url"], RIGHT)
        self.assertNotIn("/?view=title&id=77", seen)

    def test_source_key(self):
        self.assertEqual(source_key(WRONG), source_key("/?view=title&id=77"))
        self.assertNotEqual(source_key(WRONG), source_key(RIGHT))


class TestSearchToggleAndRetry(_Base):
    def _age(self, series_id, days):
        conn = self.db.get_connection()
        conn.execute("UPDATE series SET search_disabled_at = datetime('now', ?) WHERE id = ?", (f"-{days} days", series_id))
        conn.commit()

    def test_failures_record_when_search_was_disabled(self):
        for _ in range(3):
            self.db.register_search_failure(2, disable_after=3)
        row = self.db.get_series_by_id(2)
        self.assertEqual((row["search_disabled"], row["search_disabled_manual"]), (1, 0))
        self.assertIsNotNone(row["search_disabled_at"])

    def test_only_old_automatic_disables_are_retried(self):
        for series_id in (2, 3):
            self.db.register_search_failure(series_id, disable_after=1)
        self.assertEqual(self.client.put("/api/series/4/hispashare_search", json={"enabled": False}).status_code, 200)
        self._age(2, 31)
        self._age(3, 5)
        self._age(4, 400)  # apagada a mano: da igual cuánto tiempo pase

        retried = self.db.retry_expired_searches()
        self.assertEqual([r["id"] for r in retried], [2])
        old = self.db.get_series_by_id(2)
        self.assertEqual((old["search_disabled"], old["search_failures"]), (0, 2))
        # Un fallo más y vuelve a esperar otro mes.
        self.db.register_search_failure(2, disable_after=3)
        self.assertEqual(self.db.get_series_by_id(2)["search_disabled"], 1)
        self.assertEqual(self.db.retry_expired_searches(), [])

    def test_manual_toggle_from_web(self):
        self.client.put("/api/series/4/hispashare_search", json={"enabled": False})
        detail = self.client.get("/api/series/4").json()
        self.assertEqual((bool(detail["search_disabled"]), detail["search_disabled_manual"], detail["search_retry_at"]),
                         (True, True, None))
        self.client.put("/api/series/4/hispashare_search", json={"enabled": True})
        detail = self.client.get("/api/series/4").json()
        self.assertEqual((bool(detail["search_disabled"]), detail["search_disabled_manual"]), (False, False))

    def test_automatic_disable_shows_retry_date(self):
        self.db.register_search_failure(3, disable_after=1)
        detail = self.client.get("/api/series/3").json()
        self.assertFalse(detail["search_disabled_manual"])
        self.assertRegex(detail["search_retry_at"], r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
