import requests
from PyQt6.QtCore import QThread, pyqtSignal


class ImageLoaderThread(QThread):
    loaded = pyqtSignal(int, bytes)  # series_id, image_bytes
    failed = pyqtSignal(int)  # series_id

    def __init__(self, series_id: int, url: str):
        super().__init__()
        self.series_id = series_id
        self.url = url

    def run(self):
        try:
            if not self.url:
                self.failed.emit(self.series_id)
                return

            r = requests.get(self.url, timeout=15)
            if r.status_code != 200 or not r.content:
                self.failed.emit(self.series_id)
                return

            self.loaded.emit(self.series_id, r.content)
        except Exception:
            self.failed.emit(self.series_id)
