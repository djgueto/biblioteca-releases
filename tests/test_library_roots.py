"""
Tests de core/library/roots.py: reubicar bibliotecas cuando cambia su ruta.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.library.roots import LibraryRoots, canon


# Simulan un Windows con unidades mapeadas (Z: -> \NAS\...): solo tienen
# sentido allí.
WINDOWS_ONLY = unittest.skipUnless(os.name == "nt", "rutas de Windows (unidades mapeadas y UNC)")


class LibraryRootsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name).resolve()
        self.base = base
        self.new_series = base / "nas101" / "SERIES"
        self.new_movies = base / "nas100" / "PELICULAS"
        self.new_anim = base / "nas100" / "PELICULAS DE ANIMACION"
        for p in (self.new_series, self.new_movies, self.new_anim):
            p.mkdir(parents=True)

        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        # Equivalencias letra de unidad -> UNC, como `net use` en el PC.
        self.db._drive_unc_map = {"Z:": r"\\192.168.1.101\Multimedia", "Y:": r"\\192.168.1.100\Multimedia"}
        self.paths = {
            "series": ["Z:/SERIES"],
            "movies": ["Y:/PELICULAS", "Y:/PELICULAS DE ANIMACION"],
            "animated_movies": ["Y:/PELICULAS DE ANIMACION"],
        }
        self.patch = patch.object(Settings, "get_paths_for_category", lambda c: self.paths.get(c, []))
        self.patch.start()

        conn = self.db.get_connection()
        conn.executescript(r"""
            INSERT INTO series (id, name, folder_path, content_category) VALUES
              (1, 'Ahsoka', '\\192.168.1.101\Multimedia\SERIES\Ahsoka', 'series'),
              (2, 'Muertos', '\\?\UNC\192.168.1.101\Multimedia\SERIES\Muertos S.L.', 'series'),
              (3, 'Dune', '\\192.168.1.100\Multimedia\PELICULAS\Dune (2021).mkv', 'movies'),
              (4, 'Coco', '\\192.168.1.100\Multimedia\PELICULAS DE ANIMACION\Coco (2017).mkv', 'animated_movies');
            INSERT INTO local_episodes (series_id, season, episode, filename, file_path) VALUES
              (1, 1, 1, 'Ahsoka 1x01.mkv', 'Z:\SERIES\Ahsoka\Temporada 1\Ahsoka 1x01.mkv');
        """)
        conn.commit()

    def tearDown(self):
        self.patch.stop()
        self.db.close()
        self.tmp.cleanup()

    def _paths(self):
        conn = self.db.get_connection()
        series = dict(conn.execute("SELECT id, folder_path FROM series").fetchall())
        episodes = [r[0] for r in conn.execute("SELECT file_path FROM local_episodes").fetchall()]
        return series, episodes

    def _move_to_new_roots(self):
        self.paths = {
            "series": [str(self.new_series)],
            "movies": [str(self.new_movies), str(self.new_anim)],
            "animated_movies": [str(self.new_anim)],
        }

    @WINDOWS_ONLY
    def test_first_run_takes_snapshot_without_changes(self):
        before = self._paths()
        self.assertEqual(LibraryRoots(self.db).sync().status, "initialized")
        self.assertEqual(self._paths(), before)
        self.assertEqual(LibraryRoots(self.db).sync().status, "unchanged")

    @WINDOWS_ONLY
    def test_relocates_all_path_formats(self):
        roots = LibraryRoots(self.db)
        roots.sync()
        self._move_to_new_roots()

        preview = roots.sync(dry_run=True)
        self.assertEqual(preview.status, "relocated")
        self.assertEqual(preview.changes, 5)
        self.assertEqual(self._paths()[0][1], r"\\192.168.1.101\Multimedia\SERIES\Ahsoka")  # dry run

        report = roots.sync()
        self.assertEqual(report.status, "relocated")
        series, episodes = self._paths()
        self.assertEqual(series[1], str(self.new_series / "Ahsoka"))
        self.assertEqual(series[2], str(self.new_series / "Muertos S.L."))
        self.assertEqual(series[3], str(self.new_movies / "Dune (2021).mkv"))
        # La raíz más específica gana: no acaba en PELICULAS/ DE ANIMACION.
        self.assertEqual(series[4], str(self.new_anim / "Coco (2017).mkv"))
        self.assertEqual(episodes, [str(self.new_series / "Ahsoka" / "Temporada 1" / "Ahsoka 1x01.mkv")])
        self.assertEqual(roots.sync().status, "unchanged")

    @WINDOWS_ONLY
    def test_scanner_recognizes_relocated_series(self):
        roots = LibraryRoots(self.db)
        roots.sync()
        self._move_to_new_roots()
        roots.sync()
        (self.new_series / "Ahsoka").mkdir()
        self.db.insert_or_update_series({"name": "Ahsoka", "folder_path": str(self.new_series / "Ahsoka"),
                                         "content_category": "series"})
        count = self.db.get_connection().execute("SELECT COUNT(*) FROM series").fetchone()[0]
        self.assertEqual(count, 4)

    @WINDOWS_ONLY
    def test_unreachable_new_root_waits(self):
        roots = LibraryRoots(self.db)
        roots.sync()
        before = self._paths()
        self.paths = {**self.paths, "series": [str(self.base / "no_montado" / "SERIES")]}
        report = roots.sync()
        self.assertEqual(report.status, "pending")
        self.assertEqual(self._paths(), before)
        # Cuando se monta, se reubica.
        Path(self.paths["series"][0]).mkdir(parents=True)
        self.assertEqual(roots.sync().status, "relocated")
        self.assertTrue(self._paths()[0][1].startswith(self.paths["series"][0]))

    @WINDOWS_ONLY
    def test_same_folder_other_alias_is_not_a_move(self):
        roots = LibraryRoots(self.db)
        roots.sync()
        self.paths = {**self.paths, "series": [r"\\192.168.1.101\Multimedia\SERIES"]}
        self.assertEqual(roots.sync().status, "unchanged")

    def test_db_without_snapshot_and_foreign_paths_is_left_alone(self):
        self._move_to_new_roots()
        before = self._paths()
        self.assertEqual(LibraryRoots(self.db).sync().status, "unknown_previous")
        self.assertEqual(self._paths(), before)

    def test_windows_db_relocates_on_any_system(self):
        # La foto de rutas viaja en la BD: una copia hecha en Windows se
        # reubica en Docker (Linux) aunque allí no existan Z: ni las UNC.
        roots = LibraryRoots(self.db)
        conn = self.db.get_connection()
        conn.executemany(
            "INSERT INTO library_roots (category, position, path, aliases_json) VALUES (?, ?, ?, ?)",
            [("series", 0, "Z:/SERIES", '["//192.168.1.101/multimedia/series", "//?/unc/192.168.1.101/multimedia/series", "z:/series"]'),
             ("movies", 0, "Y:/PELICULAS", '["//192.168.1.100/multimedia/peliculas", "y:/peliculas"]'),
             ("movies", 1, "Y:/PELICULAS DE ANIMACION", '["//192.168.1.100/multimedia/peliculas de animacion", "y:/peliculas de animacion"]'),
             ("animated_movies", 0, "Y:/PELICULAS DE ANIMACION", '["//192.168.1.100/multimedia/peliculas de animacion", "y:/peliculas de animacion"]')])
        conn.commit()
        self._move_to_new_roots()
        report = roots.sync()
        self.assertEqual(report.status, "relocated")
        series, episodes = self._paths()
        self.assertEqual(Path(series[1]), self.new_series / "Ahsoka")
        self.assertEqual(Path(series[2]), self.new_series / "Muertos S.L.")
        self.assertEqual(Path(series[4]), self.new_anim / "Coco (2017).mkv")
        self.assertEqual([Path(e) for e in episodes], [self.new_series / "Ahsoka" / "Temporada 1" / "Ahsoka 1x01.mkv"])
        self.assertNotIn("\\", series[1] if os.name != "nt" else "")

    def test_canon(self):
        self.assertEqual(canon("Z:\\SERIES\\"), "z:/series")


if __name__ == "__main__":
    unittest.main()
