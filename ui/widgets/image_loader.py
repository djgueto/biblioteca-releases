from pathlib import Path

import requests
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal
from config.settings import Settings


class ImageLoaderSignals(QObject):
    loaded = pyqtSignal(int, bytes)  # series_id, image_bytes
    failed = pyqtSignal(int)  # series_id
    done = pyqtSignal(int)  # series_id, emitted always after loaded/failed


class ImageLoaderTask(QRunnable):
    """Descarga/lee una carátula en el QThreadPool compartido.

    Antes esto era un QThread por carátula: con miles de filas, cada scroll
    creaba y destruía hilos de sistema operativo (y fue la causa raíz del
    crash documentado en debug-qthread-destroyed.md). QRunnable reutiliza
    los hilos del pool en vez de crear uno nuevo por imagen.
    """

    def __init__(self, series_id: int, url: str, cache_path: str | None = None):
        super().__init__()
        self.series_id = series_id
        self.url = url
        self.cache_path = Path(cache_path) if cache_path else None
        self.signals = ImageLoaderSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    @staticmethod
    def ensure_cache_dir():
        cache_dir = Path(Settings.COVER_CACHE_DIR)
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                self.signals.failed.emit(self.series_id)
                return

            if self.cache_path and self.cache_path.exists():
                cached_bytes = self.cache_path.read_bytes()
                if cached_bytes:
                    self.signals.loaded.emit(self.series_id, cached_bytes)
                    return

            if not self.url:
                self.signals.failed.emit(self.series_id)
                return

            r = requests.get(self.url, timeout=15)
            if self._cancelled:
                self.signals.failed.emit(self.series_id)
                return

            if r.status_code != 200 or not r.content:
                self.signals.failed.emit(self.series_id)
                return

            if self.cache_path:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_bytes(r.content)

            self.signals.loaded.emit(self.series_id, r.content)
        except Exception:
            self.signals.failed.emit(self.series_id)
        finally:
            self.signals.done.emit(self.series_id)
