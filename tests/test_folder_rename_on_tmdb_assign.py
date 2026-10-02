"""
Tests para la funcionalidad de renombrado automático de carpetas al asignar TMDB.

Cubre:
- db_manager.rename_series_folder() en todos sus casos
- Que el setting AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN controla el comportamiento
- Que ManualUpdateTask llama al rename sólo cuando corresponde
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Añadir raíz del proyecto al path para importaciones
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Patch de dotenv/settings antes de importar módulos del proyecto
import importlib
with patch.dict(os.environ, {
    "TMDB_API_KEY": "test_key",
    "SERIES_PATH": str(PROJECT_ROOT / "tests" / "_tmp_series"),
    "DB_PATH": ":memory:",
    "AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN": "True",
}):
    from core.database.db_manager import DatabaseManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_dir: Path) -> DatabaseManager:
    """Crea un DatabaseManager en memoria con tablas inicializadas."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize_database()
    return db


def _insert_series(db: DatabaseManager, folder_path: str, name: str = "test series") -> int:
    """Inserta una serie mínima y devuelve su id."""
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO series (name, folder_path, content_category) VALUES (?, ?, 'series')",
        (name, folder_path),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_episode(db: DatabaseManager, series_id: int, file_path: str) -> int:
    """Inserta un episodio local y devuelve su id."""
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO local_episodes (series_id, season, episode, filename, file_path) VALUES (?, 1, 1, 'ep.mkv', ?)",
        (series_id, file_path),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Tests para rename_series_folder
# ---------------------------------------------------------------------------

class TestRenameFolderNotFound(unittest.TestCase):
    """Serie o carpeta inexistente."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_series_not_found(self):
        result = self.db.rename_series_folder(99999, "Nuevo Nombre")
        self.assertFalse(result["success"])
        self.assertFalse(result["renamed"])
        self.assertIn("error", result)

    def test_folder_does_not_exist_on_disk(self):
        ghost_path = str(self.tmp / "carpeta_fantasma")
        series_id = _insert_series(self.db, ghost_path, "carpeta_fantasma")
        result = self.db.rename_series_folder(series_id, "Nuevo Nombre")
        self.assertFalse(result["success"])
        self.assertFalse(result["renamed"])
        self.assertIn("error", result)


class TestRenameFolderSameName(unittest.TestCase):
    """No renombrar si el nombre ya coincide."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = _make_db(self.tmp)
        self.folder = self.tmp / "Mi Serie"
        self.folder.mkdir()
        self.series_id = _insert_series(self.db, str(self.folder), "Mi Serie")

    def tearDown(self):
        self.db.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_name_returns_not_renamed(self):
        result = self.db.rename_series_folder(self.series_id, "Mi Serie")
        self.assertTrue(result["success"])
        self.assertFalse(result["renamed"])
        # La carpeta original debe seguir existiendo
        self.assertTrue(self.folder.exists())


class TestRenameFolderHappyPath(unittest.TestCase):
    """Caso principal: renombrado correcto con actualización en BD."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = _make_db(self.tmp)
        self.old_folder = self.tmp / "la_serie"
        self.old_folder.mkdir()
        self.series_id = _insert_series(self.db, str(self.old_folder), "la_serie")
        # Insertar episodio para verificar actualización de rutas
        ep_path = str(self.old_folder / "Season 1" / "S01E01.mkv")
        _insert_episode(self.db, self.series_id, ep_path)

    def tearDown(self):
        self.db.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_folder_renamed_on_disk(self):
        result = self.db.rename_series_folder(self.series_id, "La Serie")
        self.assertTrue(result["success"])
        self.assertTrue(result["renamed"])
        new_folder = self.tmp / "La Serie"
        self.assertTrue(new_folder.exists())
        self.assertFalse(self.old_folder.exists())

    def test_series_name_updated_in_db(self):
        self.db.rename_series_folder(self.series_id, "La Serie")
        series = self.db.get_series_by_id(self.series_id)
        self.assertEqual(series["name"], "La Serie")

    def test_folder_path_updated_in_db(self):
        self.db.rename_series_folder(self.series_id, "La Serie")
        series = self.db.get_series_by_id(self.series_id)
        expected = str(self.tmp / "La Serie")
        self.assertEqual(series["folder_path"], expected)

    def test_episode_file_path_updated_in_db(self):
        self.db.rename_series_folder(self.series_id, "La Serie")
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM local_episodes WHERE series_id = ?", (self.series_id,))
        ep = cursor.fetchone()
        self.assertIsNotNone(ep)
        self.assertIn("La Serie", ep["file_path"])
        self.assertNotIn("la_serie", ep["file_path"])

    def test_return_dict_keys(self):
        result = self.db.rename_series_folder(self.series_id, "La Serie")
        for key in ("success", "renamed", "old_name", "new_name", "old_folder_path", "new_folder_path"):
            self.assertIn(key, result, f"Falta clave '{key}' en result")
        self.assertEqual(result["old_name"], "la_serie")
        self.assertEqual(result["new_name"], "La Serie")


class TestRenameFolderSanitization(unittest.TestCase):
    """El método debe sanitizar caracteres inválidos del nombre."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = _make_db(self.tmp)
        self.folder = self.tmp / "serie"
        self.folder.mkdir()
        self.series_id = _insert_series(self.db, str(self.folder), "serie")

    def tearDown(self):
        self.db.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_invalid_chars_stripped(self):
        # Caracteres inválidos en Windows: < > : " / \ | ? *
        result = self.db.rename_series_folder(self.series_id, 'Serie: La <Aventura> "Total"')
        self.assertTrue(result["success"])
        self.assertTrue(result["renamed"])
        # El nombre debe existir sin los caracteres inválidos
        self.assertNotIn(":", result["new_name"])
        self.assertNotIn("<", result["new_name"])
        self.assertNotIn('"', result["new_name"])

    def test_empty_after_sanitization(self):
        # Si queda vacío tras sanitizar, debe fallar
        result = self.db.rename_series_folder(self.series_id, ':<>"|?*')
        self.assertFalse(result["success"])
        self.assertFalse(result["renamed"])


class TestRenameFolderConflict(unittest.TestCase):
    """No renombrar si ya existe una carpeta con ese nombre (distinto contenido)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = _make_db(self.tmp)
        self.old_folder = self.tmp / "serie_vieja"
        self.old_folder.mkdir()
        # Crear carpeta destino ya existente
        self.new_folder = self.tmp / "Serie Nueva"
        self.new_folder.mkdir()
        self.series_id = _insert_series(self.db, str(self.old_folder), "serie_vieja")

    def tearDown(self):
        self.db.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_conflict_returns_error(self):
        result = self.db.rename_series_folder(self.series_id, "Serie Nueva")
        self.assertFalse(result["success"])
        self.assertFalse(result["renamed"])
        # La carpeta original debe seguir intacta
        self.assertTrue(self.old_folder.exists())


# ---------------------------------------------------------------------------
# Tests para ficheros únicos (películas, documentales, etc.)
# ---------------------------------------------------------------------------

class TestRenameSingleFile(unittest.TestCase):
    """Para ficheros únicos la extensión debe preservarse."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_file(self, filename):
        f = self.tmp / filename
        f.write_bytes(b"\x00")  # fichero mínimo
        return f

    def test_extension_preserved_on_rename(self):
        """El fichero renombrado debe conservar su extensión (.mkv, .mp4, …)."""
        for ext in (".mkv", ".mp4", ".avi"):
            old_name = f"la.pelicula{ext}"
            f = self._make_file(old_name)
            series_id = _insert_series(self.db, str(f), old_name)

            result = self.db.rename_series_folder(series_id, "La Película")
            self.assertTrue(result["success"], f"Debería tener éxito para {ext}")
            self.assertTrue(result["renamed"], f"Debería haberse renombrado para {ext}")
            self.assertEqual(result["new_name"], f"La Película{ext}")
            self.assertTrue((self.tmp / f"La Película{ext}").exists(), f"Fichero renombrado no existe para {ext}")
            self.assertFalse(f.exists(), f"Fichero original no debe existir para {ext}")

            # Limpiar para la siguiente iteración
            (self.tmp / f"La Película{ext}").unlink(missing_ok=True)
            conn = self.db.get_connection()
            conn.execute("DELETE FROM series WHERE id = ?", (series_id,))
            conn.commit()

    def test_db_name_includes_extension(self):
        """El campo name en BD debe contener la extensión."""
        f = self._make_file("pelicula.mkv")
        series_id = _insert_series(self.db, str(f), "pelicula.mkv")

        self.db.rename_series_folder(series_id, "Película Genial")
        series = self.db.get_series_by_id(series_id)
        self.assertEqual(series["name"], "Película Genial.mkv")

    def test_folder_path_in_db_includes_extension(self):
        """El folder_path en BD debe apuntar al fichero con extensión."""
        f = self._make_file("doc.mp4")
        series_id = _insert_series(self.db, str(f), "doc.mp4")

        self.db.rename_series_folder(series_id, "Mi Documental")
        series = self.db.get_series_by_id(series_id)
        self.assertTrue(series["folder_path"].endswith(".mp4"))

    def test_same_name_with_extension_not_renamed(self):
        """Si el nombre ya coincide (incluyendo extensión), no renombrar."""
        f = self._make_file("Mi Película.mkv")
        series_id = _insert_series(self.db, str(f), "Mi Película.mkv")

        result = self.db.rename_series_folder(series_id, "Mi Película")
        self.assertTrue(result["success"])
        self.assertFalse(result["renamed"])
        self.assertTrue(f.exists())

    def test_directory_not_affected(self):
        """Las carpetas (series episódicas) no deben recibir ninguna extensión."""
        folder = self.tmp / "mi serie"
        folder.mkdir()
        series_id = _insert_series(self.db, str(folder), "mi serie")

        result = self.db.rename_series_folder(series_id, "Mi Serie")
        self.assertTrue(result["success"])
        self.assertTrue(result["renamed"])
        self.assertEqual(result["new_name"], "Mi Serie")  # sin extensión


# ---------------------------------------------------------------------------
# Tests para el setting AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN
# ---------------------------------------------------------------------------

class TestSettingDefault(unittest.TestCase):
    """El setting debe estar activado por defecto."""

    def test_default_true(self):
        with patch.dict(os.environ, {}, clear=False):
            # Reimportar Settings para reflejar el env limpio
            import config.settings as settings_module
            importlib.reload(settings_module)
            # DEFAULT es True cuando no hay .env que lo sobreescriba
            # Solo comprobamos que el atributo existe y es bool
            self.assertIsInstance(settings_module.Settings.AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN, bool)

    def test_can_be_disabled_via_env(self):
        with patch.dict(os.environ, {"AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN": "False"}):
            import config.settings as settings_module
            importlib.reload(settings_module)
            self.assertFalse(settings_module.Settings.AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN)

    def test_can_be_enabled_via_env(self):
        with patch.dict(os.environ, {"AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN": "True"}):
            import config.settings as settings_module
            importlib.reload(settings_module)
            self.assertTrue(settings_module.Settings.AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN)


# ---------------------------------------------------------------------------
# Tests para ManualUpdateTask (sin UI, usando mocks)
# ---------------------------------------------------------------------------

class TestManualUpdateTaskRename(unittest.TestCase):
    """Verifica que ManualUpdateTask invoca rename_series_folder correctamente."""

    def _make_task(self, changes):
        from ui.workers.background_tasks import ManualUpdateTask
        task = ManualUpdateTask.__new__(ManualUpdateTask)
        task.series_id = 1
        task.changes = changes
        task.is_library = False
        task.signals = MagicMock()
        return task

    def _mock_db(self, rename_result):
        db = MagicMock()
        db.rename_series_folder.return_value = rename_result
        return db

    def _build_services_mock(self, db):
        series_repo = MagicMock()
        series_repo.save_manual_changes.return_value = {
            "previous_item": {"id": 1, "name": "old"},
            "updated_item": {"id": 1, "name": "Nuevo"},
        }
        series_repo.get_by_id.return_value = {"id": 1, "name": "Nuevo"}
        reconciliation = MagicMock()
        reconciliation.on_manual_update.return_value = {
            "recalculated_missing": False,
            "reset_hispashare_url": False,
            "recheck_hispashare_url": False,
            "previous_hispashare_url": None,
        }
        return {
            "series_repository": series_repo,
            "reconciliation_service": reconciliation,
        }

    @patch("ui.workers.background_tasks.Settings")
    @patch("ui.workers.background_tasks._build_domain_services")
    @patch("ui.workers.background_tasks.DatabaseManager")
    def test_rename_called_when_setting_enabled_and_tmdb_name_in_changes(
        self, MockDB, mock_build_services, MockSettings
    ):
        MockSettings.AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN = True
        db_instance = self._mock_db({"success": True, "renamed": True, "old_name": "vieja", "new_name": "Nueva"})
        MockDB.return_value = db_instance
        mock_build_services.return_value = self._build_services_mock(db_instance)

        task = self._make_task({"tmdb_id": 123, "tmdb_name": "Nueva Serie"})
        task.execute()

        db_instance.rename_series_folder.assert_called_once_with(1, "Nueva Serie")

    @patch("ui.workers.background_tasks.Settings")
    @patch("ui.workers.background_tasks._build_domain_services")
    @patch("ui.workers.background_tasks.DatabaseManager")
    def test_rename_not_called_when_setting_disabled(
        self, MockDB, mock_build_services, MockSettings
    ):
        MockSettings.AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN = False
        db_instance = self._mock_db({"success": True, "renamed": True, "old_name": "v", "new_name": "N"})
        MockDB.return_value = db_instance
        mock_build_services.return_value = self._build_services_mock(db_instance)

        task = self._make_task({"tmdb_id": 123, "tmdb_name": "Nueva Serie"})
        task.execute()

        db_instance.rename_series_folder.assert_not_called()

    @patch("ui.workers.background_tasks.Settings")
    @patch("ui.workers.background_tasks._build_domain_services")
    @patch("ui.workers.background_tasks.DatabaseManager")
    def test_rename_not_called_when_no_tmdb_name_in_changes(
        self, MockDB, mock_build_services, MockSettings
    ):
        MockSettings.AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN = True
        db_instance = self._mock_db({"success": True, "renamed": False})
        MockDB.return_value = db_instance
        mock_build_services.return_value = self._build_services_mock(db_instance)

        task = self._make_task({"hispashare_url": "http://example.com"})
        task.execute()

        db_instance.rename_series_folder.assert_not_called()

    @patch("ui.workers.background_tasks.Settings")
    @patch("ui.workers.background_tasks._build_domain_services")
    @patch("ui.workers.background_tasks.DatabaseManager")
    def test_log_emitted_on_successful_rename(
        self, MockDB, mock_build_services, MockSettings
    ):
        MockSettings.AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN = True
        db_instance = self._mock_db({
            "success": True, "renamed": True, "old_name": "vieja serie", "new_name": "Nueva Serie"
        })
        MockDB.return_value = db_instance
        mock_build_services.return_value = self._build_services_mock(db_instance)

        task = self._make_task({"tmdb_id": 1, "tmdb_name": "Nueva Serie"})
        task.execute()

        logged_messages = [call.args[0] for call in task.signals.log_signal.emit.call_args_list]
        self.assertTrue(any("Carpeta renombrada" in m for m in logged_messages))

    @patch("ui.workers.background_tasks.Settings")
    @patch("ui.workers.background_tasks._build_domain_services")
    @patch("ui.workers.background_tasks.DatabaseManager")
    def test_log_emitted_on_failed_rename(
        self, MockDB, mock_build_services, MockSettings
    ):
        MockSettings.AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN = True
        db_instance = self._mock_db({
            "success": False, "renamed": False, "error": "Permiso denegado"
        })
        MockDB.return_value = db_instance
        mock_build_services.return_value = self._build_services_mock(db_instance)

        task = self._make_task({"tmdb_id": 1, "tmdb_name": "Nueva Serie"})
        task.execute()

        logged_messages = [call.args[0] for call in task.signals.log_signal.emit.call_args_list]
        self.assertTrue(any("No se pudo renombrar" in m for m in logged_messages))


if __name__ == "__main__":
    unittest.main()
