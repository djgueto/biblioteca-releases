"""
Regresión: la calidad medida con MediaInfo de los episodios se perdía en
cada escaneo de la biblioteca (insert_local_episodes borra y reinserta).
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager


def _ep(episode, size=1000.0, **quality):
    return {"season": 1, "episode": episode, "filename": f"S01E0{episode}.mkv",
            "file_path": f"/s/Serie/S01E0{episode}.mkv", "size_mb": size, **quality}


class TestEpisodeQualityPersistence(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        self.db.get_connection().execute("INSERT INTO series (id, name, folder_path) VALUES (1, 'Serie', '/s/Serie')")
        self.db.insert_local_episodes(1, [_ep(1), _ep(2), _ep(3)])
        # Lo que hace scan_missing_resolutions tras leer con MediaInfo:
        self.db.batch_update_episode_resolutions([
            {"id": self._id(1), "resolution_height": 2160, "codec": "x265", "quality_rank": 4, "hdr": "hdr10"},
            {"id": self._id(2), "resolution_height": 1080, "codec": "x264", "quality_rank": 3, "hdr": None},
        ])
        self.db.mark_episode_resolution_unreadable([self._id(3)])

    def tearDown(self):
        self.db.close()

    def _id(self, episode):
        return self.db.get_connection().execute("SELECT id FROM local_episodes WHERE episode = ?", (episode,)).fetchone()[0]

    def _row(self, episode):
        return dict(self.db.get_connection().execute(
            "SELECT resolution_height, codec, hdr, quality_rank FROM local_episodes WHERE episode = ?", (episode,)).fetchone())

    def test_rescan_keeps_measured_quality(self):
        self.db.insert_local_episodes(1, [_ep(1), _ep(2), _ep(3)])  # el siguiente escaneo
        self.assertEqual(self._row(1), {"resolution_height": 2160, "codec": "x265", "hdr": "hdr10", "quality_rank": 4})
        self.assertEqual(self._row(2)["hdr"], "sdr")  # MediaInfo no vio HDR: SDR, no "desconocido"
        self.assertEqual(self._row(2)["resolution_height"], 1080)
        self.assertEqual(self._row(3)["quality_rank"], -1)  # ilegible: no se reintenta
        self.assertEqual(len(self.db.get_episodes_missing_resolution()), 0)

    def test_filename_quality_is_provisional_and_overridden(self):
        self.db.insert_local_episodes(1, [_ep(4, resolution_height=720, codec="x264", quality_rank=2)])
        row = dict(self.db.get_connection().execute(
            "SELECT id, quality_source FROM local_episodes WHERE episode = 4").fetchone())
        self.assertEqual(row["quality_source"], "filename")
        self.assertIn(row["id"], [e["id"] for e in self.db.get_episodes_missing_resolution()])  # se medirá igual
        self.db.batch_update_episode_resolutions([{"id": row["id"], "resolution_height": 1080, "codec": "x265",
                                                   "quality_rank": 3, "hdr": None}])
        self.assertEqual(self._row(4), {"resolution_height": 1080, "codec": "x265", "hdr": "sdr", "quality_rank": 3})

    def test_unreadable_is_retried_after_a_week(self):
        conn = self.db.get_connection()
        self.assertNotIn(self._id(3), [e["id"] for e in self.db.get_episodes_missing_resolution()])
        conn.execute("UPDATE local_episodes SET measured_at = datetime('now', '-8 days') WHERE episode = 3")
        self.assertIn(self._id(3), [e["id"] for e in self.db.get_episodes_missing_resolution()])

    def test_coverage_counts(self):
        self.assertEqual(self.db.count_episode_quality(), {"total": 3, "measured": 2, "unreadable": 1})

    def test_replaced_file_is_measured_again(self):
        self.db.insert_local_episodes(1, [_ep(1, size=4000.0), _ep(2), _ep(3)])
        self.assertIsNone(self._row(1)["resolution_height"])  # sin nombre con calidad: a medir
        self.assertEqual([e["id"] for e in self.db.get_episodes_missing_resolution()], [self._id(1)])


if __name__ == "__main__":
    unittest.main()
