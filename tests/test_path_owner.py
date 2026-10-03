"""
Una carpeta configurada en dos bibliotecas solo se escanea en una.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings


ENV = {
    "DOCUMENTARIES_PATH": "Y:/DOCUMENTALES",
    "DOCUMENTARIES_PATHS": "Y:/DOCUMENTALES (series)",
    "DOCUSERIES_PATH": "Y:\\DOCUMENTALES (series)\\",
    "DOCUSERIES_PATHS": "",
    "CONCERTS_PATH": "Y:/CONCIERTOS",
    "MUSIC_PATH": "Y:/CONCIERTOS",
}


class PathOwnerTest(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(Settings, cfg[key], ENV.get(cfg[key], ""), create=True)
                        for cfg in Settings.LIBRARY_CATEGORIES.values()
                        for key in ("primary_key", "additional_key")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_primary_path_wins_over_additional(self):
        self.assertEqual(Settings.get_paths_for_category("documentaries"), ["Y:/DOCUMENTALES"])
        self.assertEqual(Settings.get_paths_for_category("docuseries"), ["Y:\\DOCUMENTALES (series)\\"])

    def test_tie_goes_to_first_library(self):
        self.assertEqual(Settings.get_paths_for_category("concerts"), ["Y:/CONCIERTOS"])
        self.assertEqual(Settings.get_paths_for_category("music"), [])

    def test_duplicates_are_reported(self):
        dup = Settings.get_duplicated_paths()
        self.assertEqual(sorted(map(sorted, dup.values())),
                         [["concerts", "music"], ["documentaries", "docuseries"]])


if __name__ == "__main__":
    unittest.main()
