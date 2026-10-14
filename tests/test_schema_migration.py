"""
Una BD creada con una versión anterior del esquema recibe las columnas
nuevas al arrancar (si no se sube SCHEMA_VERSION, initialize_database se las
salta: pasó con series.tmdb_media_type).
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager


class TestSchemaMigration(unittest.TestCase):
    def test_db_at_version_2_gets_tmdb_media_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            # BD como la dejaba la versión 2: sin la columna nueva.
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE series (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                         "folder_path TEXT UNIQUE NOT NULL, content_category TEXT DEFAULT 'series', "
                         "tmdb_id INTEGER, tmdb_name TEXT, year INTEGER, confidence_score REAL)")
            conn.execute("PRAGMA user_version = 2")
            conn.commit()
            conn.close()

            db = DatabaseManager(path)
            db.initialize_database()
            columns = {r[1] for r in db.get_connection().execute("PRAGMA table_info(series)")}
            version = db.get_connection().execute("PRAGMA user_version").fetchone()[0]
            db.get_connection().close()

        self.assertIn("tmdb_media_type", columns)
        self.assertEqual(version, DatabaseManager.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
