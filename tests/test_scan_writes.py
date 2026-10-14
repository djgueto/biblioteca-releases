"""
El escaneo no reescribe en la BD lo que no ha cambiado en disco, y el
recorrido con os.scandir encuentra lo mismo que antes.
"""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.library.scanner import LibraryScanner


class TestScanWrites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.series_dir = root / "Serie"
        (self.series_dir / "Season 01").mkdir(parents=True)
        (self.series_dir / "Season 01" / "Serie S01E01.mkv").write_bytes(b"x" * 10)
        (self.series_dir / "Season 01" / "Serie S01E02 1080p.mkv").write_bytes(b"x" * 20)
        (self.series_dir / "Serie S02E01.mp4").write_bytes(b"x")  # suelto en la raíz
        (self.series_dir / "Season 01" / "notas.txt").write_text("no es vídeo")
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        self.scanner = LibraryScanner([root], db_manager=self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _scan(self):
        data = self.scanner.scan_series(self.series_dir)
        return data, self.db.insert_or_update_series(data)

    def test_finds_all_episodes(self):
        data, _ = self._scan()
        found = sorted((e["season"], e["episode"]) for e in data["episodes"])
        self.assertEqual(found, [(1, 1), (1, 2), (2, 1)])
        sizes = {e["episode"]: e["size_mb"] for e in data["episodes"] if e["season"] == 1}
        self.assertEqual(sizes, {1: round(10 / 1048576, 2), 2: round(20 / 1048576, 2)})

    def test_unchanged_series_is_not_rewritten(self):
        _, series_id = self._scan()
        conn = self.db.get_connection()
        conn.execute("UPDATE series SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (series_id,))
        conn.commit()
        changes_before = conn.total_changes

        data = self.scanner.scan_series(self.series_dir)
        self.assertFalse(self.db.insert_local_episodes(series_id, data["episodes"]))
        self.db.insert_or_update_series(data)
        self.assertEqual(conn.total_changes, changes_before)
        updated = conn.execute("SELECT updated_at FROM series WHERE id = ?", (series_id,)).fetchone()[0]
        self.assertEqual(updated, "2000-01-01 00:00:00")

    def test_new_episode_is_written_and_marks_updated(self):
        _, series_id = self._scan()
        conn = self.db.get_connection()
        conn.execute("UPDATE series SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (series_id,))
        conn.commit()
        (self.series_dir / "Season 01" / "Serie S01E03.mkv").write_bytes(b"x")
        self._scan()
        count = conn.execute("SELECT COUNT(*) FROM local_episodes WHERE series_id = ?", (series_id,)).fetchone()[0]
        self.assertEqual(count, 4)
        updated = conn.execute("SELECT updated_at FROM series WHERE id = ?", (series_id,)).fetchone()[0]
        self.assertNotEqual(updated, "2000-01-01 00:00:00")

    def test_measured_quality_survives_rescan_without_rewrite(self):
        _, series_id = self._scan()
        conn = self.db.get_connection()
        conn.execute("UPDATE local_episodes SET resolution_height = 2160, quality_source = 'mediainfo', "
                     "codec = 'hevc' WHERE series_id = ? AND episode = 1 AND season = 1", (series_id,))
        conn.commit()
        data = self.scanner.scan_series(self.series_dir)
        self.assertFalse(self.db.insert_local_episodes(series_id, data["episodes"]))
        row = conn.execute("SELECT resolution_height, codec FROM local_episodes WHERE series_id = ? "
                           "AND season = 1 AND episode = 1", (series_id,)).fetchone()
        self.assertEqual(tuple(row), (2160, "hevc"))


if __name__ == "__main__":
    unittest.main()
