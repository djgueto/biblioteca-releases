"""
Tests para la funcionalidad de vista previa de URL en SeriesEditDialog.

Cubre:
- UrlPreviewThread: señales emitidas, manejo de errores, flujo con login
- validate_url: validaciones de formato, credenciales vacías, lanzamiento del thread
- open_url_in_browser: llama a webbrowser.open con la URL correcta
- _on_preview_ready / _on_preview_error: manejo del resultado
"""

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from PyQt6.QtWidgets import QApplication
    _HAS_QT = True
except ImportError:
    _HAS_QT = False


def _make_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _fake_dialog(**kwargs):
    """Objeto simple que simula el diálogo para testear métodos sin instanciar QDialog."""
    d = types.SimpleNamespace()
    d.url_input = MagicMock()
    d.url_status_label = MagicMock()
    d.btn_validate_url = MagicMock()
    d.preview_image_label = MagicMock()
    d.preview_scroll = MagicMock()
    d.preview_scroll.width.return_value = 800
    d.url_preview_thread = None
    d._retained_threads = []
    d.logger = MagicMock()
    d._on_preview_ready = MagicMock()
    d._on_preview_error = MagicMock()
    d._cleanup_preview_thread = MagicMock()
    d._set_preview_busy = MagicMock()
    d._set_preview_status = MagicMock()
    for k, v in kwargs.items():
        setattr(d, k, v)
    return d


def _bound(dialog, method_name):
    import ui.widgets.series_edit_dialog as mod
    return getattr(mod.SeriesEditDialog, method_name).__get__(dialog, type(dialog))


# ---------------------------------------------------------------------------
# Tests para UrlPreviewThread
# ---------------------------------------------------------------------------

class TestUrlPreviewThreadSignals(unittest.TestCase):

    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def setUp(self):
        _make_app()

    def _make_thread(self):
        from ui.widgets.series_edit_dialog import UrlPreviewThread
        thread = UrlPreviewThread.__new__(UrlPreviewThread)
        thread.url = "https://www.hispashare.org/test"
        thread.username = "user"
        thread.password = "pass"
        thread.headless = True
        thread.status_signal = MagicMock()
        thread.preview_ready = MagicMock()
        thread.error_occurred = MagicMock()
        return thread

    @patch("ui.widgets.series_edit_dialog.UrlPreviewThread.isInterruptionRequested", return_value=False)
    @patch("core.auth.hispashare_login.HispashareAuth")
    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_happy_path_emits_preview_ready(self, MockAuth, _):
        """Login OK → screenshot → preview_ready emitido."""
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        mock_driver = MagicMock()
        mock_driver.get_screenshot_as_png.return_value = png_data
        mock_auth = MagicMock()
        mock_auth.driver = mock_driver
        mock_auth.login.return_value = True
        MockAuth.return_value = mock_auth

        thread = self._make_thread()
        with patch("time.sleep"):
            thread.run()

        thread.preview_ready.emit.assert_called_once_with(png_data)
        thread.error_occurred.emit.assert_not_called()

    @patch("ui.widgets.series_edit_dialog.UrlPreviewThread.isInterruptionRequested", return_value=False)
    @patch("core.auth.hispashare_login.HispashareAuth")
    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_login_failure_emits_error(self, MockAuth, _):
        """Login fallido → error_occurred emitido, sin screenshot."""
        mock_auth = MagicMock()
        mock_auth.login.return_value = False
        MockAuth.return_value = mock_auth

        thread = self._make_thread()
        with patch("time.sleep"):
            thread.run()

        thread.error_occurred.emit.assert_called_once()
        thread.preview_ready.emit.assert_not_called()
        self.assertIn("sesión", thread.error_occurred.emit.call_args[0][0].lower())

    @patch("ui.widgets.series_edit_dialog.UrlPreviewThread.isInterruptionRequested", return_value=False)
    @patch("core.auth.hispashare_login.HispashareAuth")
    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_exception_emits_error(self, MockAuth, _):
        """Excepción al crear el driver → error_occurred emitido."""
        MockAuth.side_effect = RuntimeError("ChromeDriver no encontrado")

        thread = self._make_thread()
        with patch("time.sleep"):
            thread.run()

        thread.error_occurred.emit.assert_called_once()
        thread.preview_ready.emit.assert_not_called()

    @patch("ui.widgets.series_edit_dialog.UrlPreviewThread.isInterruptionRequested", return_value=False)
    @patch("core.auth.hispashare_login.HispashareAuth")
    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_driver_quit_called_on_success(self, MockAuth, _):
        """El driver se cierra en el finally tras éxito."""
        mock_driver = MagicMock()
        mock_driver.get_screenshot_as_png.return_value = b"\x89PNG" + b"\x00" * 10
        mock_auth = MagicMock()
        mock_auth.driver = mock_driver
        mock_auth.login.return_value = True
        MockAuth.return_value = mock_auth

        thread = self._make_thread()
        with patch("time.sleep"):
            thread.run()

        mock_driver.quit.assert_called_once()

    @patch("ui.widgets.series_edit_dialog.UrlPreviewThread.isInterruptionRequested", return_value=False)
    @patch("core.auth.hispashare_login.HispashareAuth")
    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_driver_quit_called_on_error(self, MockAuth, _):
        """El driver se cierra en el finally incluso tras error."""
        mock_driver = MagicMock()
        mock_driver.get_screenshot_as_png.side_effect = OSError("fail")
        mock_auth = MagicMock()
        mock_auth.driver = mock_driver
        mock_auth.login.return_value = True
        MockAuth.return_value = mock_auth

        thread = self._make_thread()
        with patch("time.sleep"):
            thread.run()

        mock_driver.quit.assert_called_once()
        thread.error_occurred.emit.assert_called_once()

    @patch("ui.widgets.series_edit_dialog.UrlPreviewThread.isInterruptionRequested", return_value=False)
    @patch("core.auth.hispashare_login.HispashareAuth")
    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_status_signals_emitted_during_flow(self, MockAuth, _):
        """Se emiten al menos 3 señales de estado durante el flujo normal."""
        mock_driver = MagicMock()
        mock_driver.get_screenshot_as_png.return_value = b"\x89PNG" + b"\x00" * 10
        mock_auth = MagicMock()
        mock_auth.driver = mock_driver
        mock_auth.login.return_value = True
        MockAuth.return_value = mock_auth

        thread = self._make_thread()
        with patch("time.sleep"):
            thread.run()

        self.assertGreaterEqual(thread.status_signal.emit.call_count, 3)

    @patch("ui.widgets.series_edit_dialog.UrlPreviewThread.isInterruptionRequested", return_value=False)
    @patch("core.auth.hispashare_login.HispashareAuth")
    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_navigates_to_correct_url(self, MockAuth, _):
        """El driver navega exactamente a la URL indicada."""
        mock_driver = MagicMock()
        mock_driver.get_screenshot_as_png.return_value = b"\x89PNG" + b"\x00" * 10
        mock_auth = MagicMock()
        mock_auth.driver = mock_driver
        mock_auth.login.return_value = True
        MockAuth.return_value = mock_auth

        thread = self._make_thread()
        thread.url = "https://www.hispashare.org/mi-serie"
        with patch("time.sleep"):
            thread.run()

        mock_driver.get.assert_called_once_with("https://www.hispashare.org/mi-serie")


# ---------------------------------------------------------------------------
# Tests para validate_url
# ---------------------------------------------------------------------------

class TestValidateUrl(unittest.TestCase):

    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def setUp(self):
        _make_app()

    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_empty_url_sets_error_status(self):
        d = _fake_dialog()
        d.url_input.text.return_value = ""
        d._set_preview_status = _bound(d, "_set_preview_status")
        _bound(d, "validate_url")()

        d.url_status_label.setText.assert_called()
        self.assertIn("❌", d.url_status_label.setText.call_args[0][0])

    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_invalid_url_format_sets_error_status(self):
        d = _fake_dialog()
        d.url_input.text.return_value = "not-a-url"
        d._set_preview_status = _bound(d, "_set_preview_status")
        _bound(d, "validate_url")()

        self.assertIn("❌", d.url_status_label.setText.call_args[0][0])

    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_missing_credentials_sets_warning(self):
        d = _fake_dialog()
        d.url_input.text.return_value = "https://www.hispashare.org/test"
        d._set_preview_status = _bound(d, "_set_preview_status")

        with patch("ui.widgets.series_edit_dialog.Settings") as S:
            S.HISPASHARE_USER = ""
            S.HISPASHARE_PASS = ""
            _bound(d, "validate_url")()

        self.assertIn("⚠️", d.url_status_label.setText.call_args[0][0])
        self.assertIsNone(d.url_preview_thread)

    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_valid_url_and_credentials_starts_thread(self):
        d = _fake_dialog()
        d.url_input.text.return_value = "https://www.hispashare.org/serie-test"
        mock_thread = MagicMock()

        with patch("ui.widgets.series_edit_dialog.QPixmap"), \
             patch("ui.widgets.series_edit_dialog.UrlPreviewThread", return_value=mock_thread) as MockThread, \
             patch("ui.widgets.series_edit_dialog.Settings") as S:
            S.HISPASHARE_USER = "myuser"
            S.HISPASHARE_PASS = "mypass"
            _bound(d, "validate_url")()

        MockThread.assert_called_once_with(
            url="https://www.hispashare.org/serie-test",
            username="myuser",
            password="mypass",
            headless=True,
        )
        mock_thread.start.assert_called_once()


# ---------------------------------------------------------------------------
# Tests para open_url_in_browser
# ---------------------------------------------------------------------------

class TestOpenUrlInBrowser(unittest.TestCase):

    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def setUp(self):
        _make_app()

    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    @patch("ui.widgets.series_edit_dialog.webbrowser.open")
    def test_opens_correct_url(self, mock_open):
        d = _fake_dialog()
        d.url_input.text.return_value = "https://www.hispashare.org/mi-serie"
        _bound(d, "open_url_in_browser")()
        mock_open.assert_called_once_with("https://www.hispashare.org/mi-serie")

    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    @patch("ui.widgets.series_edit_dialog.webbrowser.open")
    def test_empty_url_shows_warning_not_open(self, mock_open):
        d = _fake_dialog()
        d.url_input.text.return_value = ""
        with patch("ui.widgets.series_edit_dialog.QMessageBox"):
            _bound(d, "open_url_in_browser")()
        mock_open.assert_not_called()


# ---------------------------------------------------------------------------
# Tests para _on_preview_ready y _on_preview_error
# ---------------------------------------------------------------------------

class TestPreviewCallbacks(unittest.TestCase):

    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def setUp(self):
        _make_app()

    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_on_preview_error_sets_error_message(self):
        d = _fake_dialog()
        d._set_preview_status = _bound(d, "_set_preview_status")
        _bound(d, "_on_preview_error")("Timeout al conectar")

        d.preview_image_label.setText.assert_called()
        self.assertIn("Timeout al conectar", d.preview_image_label.setText.call_args[0][0])
        self.assertIn("❌", d.url_status_label.setText.call_args[0][0])

    @unittest.skipUnless(_HAS_QT, "PyQt6 no disponible")
    def test_on_preview_ready_with_null_pixmap_sets_error(self):
        """Si los bytes no forman una imagen válida (QPixmap nulo), muestra error."""
        d = _fake_dialog()
        d._set_preview_status = _bound(d, "_set_preview_status")

        null_pixmap = MagicMock()
        null_pixmap.isNull.return_value = True

        with patch("ui.widgets.series_edit_dialog.QPixmap", return_value=null_pixmap):
            _bound(d, "_on_preview_ready")(b"not-a-valid-image")

        self.assertIn("❌", d.url_status_label.setText.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
