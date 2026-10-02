from __future__ import annotations

import hashlib
import time

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QLabel, QTableWidgetItem

from .widgets.image_loader import ImageLoaderTask


class CoverManager:
    """Gestiona carga, caché en disco/memoria y ciclo de vida de las carátulas.

    A propósito NO es un QObject: las señales de ImageLoaderTask se emiten
    desde un hilo del QThreadPool, y Qt solo garantiza entrega en el hilo de
    UI cuando el receptor es un QObject con esa afinidad de hilo. Por eso
    load_cover_for_series()/_load_cover_for_visible_row() conectan siempre a
    los slots on_cover_loaded/on_cover_failed/on_cover_loader_finished de
    MainWindow (que sí es QObject), y estos delegan aquí en una segunda
    llamada ya dentro del hilo de UI. No conectar señales directamente a
    métodos de esta clase.
    """

    def __init__(self, window):
        self.window = window
        self.cover_cache_dir = ImageLoaderTask.ensure_cache_dir()
        self.series_cover_widget_rows = set()
        self.library_cover_widget_rows = set()
        self.cover_loaders = {}
        self.cover_labels = {}
        self.cover_pixmap_cache = {}
        self.cover_pixmap_cache_keys = {}
        self.cover_url_cache = {}
        self.cover_load_started_at = {}
        self.pending_cover_refresh = {
            "series": False,
            "library": False,
        }
        self.series_cover_refresh_timer = None
        self.library_cover_refresh_timer = None

    def init_lazy_cover_loading(self):
        window = self.window
        self.series_cover_refresh_timer = QTimer(window)
        self.series_cover_refresh_timer.setSingleShot(True)
        self.series_cover_refresh_timer.setInterval(150)
        self.series_cover_refresh_timer.timeout.connect(self.refresh_visible_series_covers)

        self.library_cover_refresh_timer = QTimer(window)
        self.library_cover_refresh_timer.setSingleShot(True)
        self.library_cover_refresh_timer.setInterval(150)
        self.library_cover_refresh_timer.timeout.connect(self.refresh_visible_library_covers)

        window.series_table.verticalScrollBar().valueChanged.connect(
            lambda _value: self.schedule_visible_cover_refresh("series")
        )
        window.library_table.verticalScrollBar().valueChanged.connect(
            lambda _value: self.schedule_visible_cover_refresh("library")
        )

    def register_cover_label(self, series_id, label):
        if series_id is None or label is None:
            return
        self.prune_cover_labels(series_id)
        labels = self.cover_labels.setdefault(series_id, [])
        if label not in labels:
            labels.append(label)
            label.destroyed.connect(lambda _=None, sid=series_id: self.prune_cover_labels(sid))

    def prune_cover_labels(self, series_id):
        labels = self.cover_labels.get(series_id, [])
        alive_labels = []
        for label in labels:
            try:
                if label is not None and not sip.isdeleted(label):
                    alive_labels.append(label)
            except RuntimeError:
                continue

        if alive_labels:
            self.cover_labels[series_id] = alive_labels
        elif series_id in self.cover_labels:
            del self.cover_labels[series_id]

    def _consume_pending_cover_refresh(self, table_kind):
        if table_kind not in self.pending_cover_refresh:
            return False
        pending = self.pending_cover_refresh[table_kind]
        self.pending_cover_refresh[table_kind] = False
        return pending

    def schedule_visible_cover_refresh(self, table_kind, delay_ms=80):
        window = self.window
        if window._is_render_active(table_kind):
            self.pending_cover_refresh[table_kind] = True
            return

        timer = self.series_cover_refresh_timer if table_kind == "series" else self.library_cover_refresh_timer
        if timer is None:
            return
        self._consume_pending_cover_refresh(table_kind)
        timer.start(max(0, delay_ms))

    def refresh_visible_series_covers(self):
        window = self.window
        if window._is_render_active("series"):
            self.pending_cover_refresh["series"] = True
            return
        started_at = time.perf_counter()
        visible_rows = self._refresh_visible_covers(
            window.series_table,
            window.current_series_list,
            is_library=False,
            table_kind="series",
        )
        window._log_slow_operation(
            "series_visible_covers",
            window._elapsed_ms(started_at),
            window.perf_threshold_ms["cover_refresh"],
            visible_rows=visible_rows,
            items=len(window.current_series_list),
        )

    def refresh_visible_library_covers(self):
        window = self.window
        if window._is_render_active("library"):
            self.pending_cover_refresh["library"] = True
            return
        started_at = time.perf_counter()
        visible_rows = self._refresh_visible_covers(
            window.library_table,
            window.current_single_file_list,
            is_library=True,
            table_kind="library",
        )
        window._log_slow_operation(
            "library_visible_covers",
            window._elapsed_ms(started_at),
            window.perf_threshold_ms["cover_refresh"],
            visible_rows=visible_rows,
            items=len(window.current_single_file_list),
        )

    def _refresh_visible_covers(self, table, items, *, is_library, table_kind):
        if self.window.is_closing or not items:
            return 0

        bounds = self._get_visible_row_bounds(table, len(items))
        if bounds is None:
            return 0

        start, end = bounds
        self._cleanup_offscreen_cover_widgets(table, start, end, table_kind)
        for row in range(start, end + 1):
            self._load_cover_for_visible_row(table, row, items[row], is_library=is_library, table_kind=table_kind)
        return end - start + 1

    def _get_visible_row_bounds(self, table, total_rows, preload_rows=4):
        if total_rows <= 0:
            return None

        first_row = table.rowAt(0)
        if first_row < 0:
            first_row = 0

        last_row = table.rowAt(max(0, table.viewport().height() - 1))
        if last_row < 0:
            row_height = max(1, table.verticalHeader().defaultSectionSize())
            approx_rows = max(1, table.viewport().height() // row_height)
            last_row = min(total_rows - 1, first_row + approx_rows)

        first_row = max(0, first_row - preload_rows)
        last_row = min(total_rows - 1, last_row + preload_rows)
        return first_row, last_row

    def _load_cover_for_visible_row(self, table, row, item, *, is_library, table_kind):
        window = self.window
        series_id = item.get('id')
        if series_id is None:
            return

        cover_path = item.get('cover_path') if is_library else None
        if cover_path:
            cover_label = self._ensure_cover_label(table, row, table_kind)
            # Comprobar caché en memoria primero (sin I/O)
            cached_pixmap = self.cover_pixmap_cache.get(series_id)
            if cached_pixmap is not None:
                self.register_cover_label(series_id, cover_label)
                self.apply_cover_pixmap(series_id, cached_pixmap)
                return
            # Leer del disco en hilo secundario para no bloquear el hilo UI
            if series_id not in self.cover_loaders:
                self.register_cover_label(series_id, cover_label)
                loader = ImageLoaderTask(series_id, '', cover_path)
                loader.signals.done.connect(window.on_cover_loader_finished)
                loader.signals.loaded.connect(window.on_cover_loaded)
                loader.signals.failed.connect(window.on_cover_failed)
                self.cover_loaders[series_id] = loader
                self.cover_load_started_at[series_id] = time.perf_counter()
                window.worker_pool.start(loader)
            return

        cover_url = self.ensure_cover_url_cached(item)
        if cover_url:
            cover_label = self._ensure_cover_label(table, row, table_kind)
            self.register_cover_label(series_id, cover_label)
            self.load_cover_for_series(series_id, cover_url)
        else:
            self.set_cover_placeholder(table, row, table_kind, "Sin carátula")

    def _get_cover_row_registry(self, table_kind):
        return self.series_cover_widget_rows if table_kind == "series" else self.library_cover_widget_rows

    def set_cover_placeholder(self, table, row, table_kind, text="..."):
        existing_widget = table.cellWidget(row, 7)
        if existing_widget is not None:
            table.removeCellWidget(row, 7)
            existing_widget.deleteLater()
        self._get_cover_row_registry(table_kind).discard(row)
        placeholder_item = QTableWidgetItem(text)
        placeholder_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(row, 7, placeholder_item)

    def _ensure_cover_label(self, table, row, table_kind):
        cover_label = table.cellWidget(row, 7)
        if cover_label is not None:
            return cover_label

        cover_label = QLabel()
        cover_label.setFixedSize(self.window.cover_thumb_width, self.window.cover_thumb_height)
        cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_label.setText("...")
        table.setCellWidget(row, 7, cover_label)
        self._get_cover_row_registry(table_kind).add(row)
        return cover_label

    def _cleanup_offscreen_cover_widgets(self, table, start, end, table_kind):
        active_rows = list(self._get_cover_row_registry(table_kind))
        for row in active_rows:
            if start <= row <= end and row < table.rowCount():
                continue
            self.set_cover_placeholder(table, row, table_kind)

    def sync_cover_url_cache(self, items):
        for item in items or []:
            item_id = item.get("id")
            if item_id is None:
                continue
            cover_url = (item.get("hispashare_cover_url") or "").strip()
            if cover_url:
                self.cover_url_cache[item_id] = cover_url
            elif item_id in self.cover_url_cache and not item.get("hispashare_url"):
                self.cover_url_cache.pop(item_id, None)

    def remember_cover_url(self, series_id, cover_url):
        if not series_id:
            return
        cover_url = (cover_url or "").strip()
        if cover_url:
            self.cover_url_cache[series_id] = cover_url
        else:
            self.cover_url_cache.pop(series_id, None)

    def ensure_cover_url_cached(self, item):
        item_id = item.get('id')
        cover_url = (item.get('hispashare_cover_url') or '').strip()
        if cover_url:
            self.remember_cover_url(item_id, cover_url)
            return cover_url

        cached_cover_url = self.cover_url_cache.get(item_id)
        if cached_cover_url:
            item['hispashare_cover_url'] = cached_cover_url
            return cached_cover_url

        hispashare_url = (item.get('hispashare_url') or '').strip()
        if not hispashare_url:
            return ''

        cover_url = self.window.db.derive_hispashare_cover_url(hispashare_url) or ''
        if not cover_url:
            return ''

        item['hispashare_cover_url'] = cover_url
        self.remember_cover_url(item_id, cover_url)
        if item_id is not None:
            self.window.start_cover_url_resolve(item_id, hispashare_url)
        return cover_url

    def load_cover_for_series(self, series_id, cover_url):
        window = self.window
        if not series_id or not cover_url:
            return
        cache_path = self.get_cover_cache_path(series_id, cover_url)
        cache_key = str(cache_path)
        previous_cache_key = self.cover_pixmap_cache_keys.get(series_id)
        if previous_cache_key and previous_cache_key != cache_key:
            self.cover_pixmap_cache.pop(series_id, None)
            # Solo listar el directorio cuando la URL cambió: un glob sobre
            # cache/covers (unidad de red, miles de archivos) cuesta ~150 ms
            # y esta función corre por cada fila visible en cada scroll.
            self.prune_cover_cache(series_id, keep_path=cache_path)
        self.cover_pixmap_cache_keys[series_id] = cache_key

        cached_pixmap = self.cover_pixmap_cache.get(series_id)
        if cached_pixmap is not None:
            self.apply_cover_pixmap(series_id, cached_pixmap)
            return

        if series_id in self.cover_loaders:
            return

        # ImageLoaderTask comprueba cache_path en su run() y lee del disco
        # en segundo plano; no bloqueamos el hilo UI con QPixmap(cache_path).
        loader = ImageLoaderTask(series_id, cover_url, str(cache_path))
        loader.signals.done.connect(window.on_cover_loader_finished)
        loader.signals.loaded.connect(window.on_cover_loaded)
        loader.signals.failed.connect(window.on_cover_failed)
        self.cover_loaders[series_id] = loader
        self.cover_load_started_at[series_id] = time.perf_counter()
        window.worker_pool.start(loader)

    def handle_cover_loaded(self, series_id, image_bytes):
        window = self.window
        started_at = self.cover_load_started_at.pop(series_id, None)
        self.prune_cover_labels(series_id)
        pixmap = QPixmap()
        if not pixmap.loadFromData(image_bytes):
            return
        self.cover_pixmap_cache[series_id] = pixmap
        self.apply_cover_pixmap(series_id, pixmap)
        if started_at is not None:
            window._log_slow_operation(
                "cover_load_async",
                window._elapsed_ms(started_at),
                window.perf_threshold_ms["cover_load"],
                series_id=series_id,
            )

    def get_cover_cache_path(self, series_id, cover_url):
        url_hash = hashlib.sha1(cover_url.encode('utf-8')).hexdigest()[:12]
        return self.cover_cache_dir / f"{series_id}_{url_hash}.jpg"

    def prune_cover_cache(self, series_id, keep_path=None):
        try:
            files = list(self.cover_cache_dir.glob(f"{series_id}_*.jpg"))
        except Exception:
            return
        for cache_file in files:
            if keep_path is not None and cache_file == keep_path:
                continue
            try:
                cache_file.unlink()
            except OSError:
                continue

    def apply_cover_pixmap(self, series_id, pixmap):
        labels = self.cover_labels.get(series_id, [])
        if not labels:
            return

        scaled = pixmap.scaledToHeight(self.window.cover_thumb_height, Qt.TransformationMode.SmoothTransformation)
        alive_labels = []
        for label in labels:
            try:
                if sip.isdeleted(label):
                    continue
                label.setPixmap(scaled)
                alive_labels.append(label)
            except RuntimeError:
                continue

        if alive_labels:
            self.cover_labels[series_id] = alive_labels
        elif series_id in self.cover_labels:
            del self.cover_labels[series_id]

    def handle_cover_failed(self, series_id):
        self.cover_load_started_at.pop(series_id, None)

    def handle_cover_loader_finished(self, series_id):
        self.cover_load_started_at.pop(series_id, None)
        # QRunnable con setAutoDelete(True) se destruye solo al terminar run();
        # aquí solo soltamos la referencia que llevamos para poder cancelarlo.
        self.cover_loaders.pop(series_id, None)

    def cancel_all_loaders(self):
        # Los loaders corren como QRunnable en worker_pool: no se pueden
        # esperar individualmente, así que solo marcamos cancelación aquí.
        # closeEvent() espera al pool completo con worker_pool.waitForDone().
        for loader in list(self.cover_loaders.values()):
            loader.cancel()
        self.cover_loaders.clear()
