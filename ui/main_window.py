import re
import time

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableWidget, QLabel, QStatusBar, QAbstractItemView,
    QTabWidget, QTextEdit, QProgressBar, QTableWidgetItem, QComboBox,
    QMessageBox, QHeaderView, QMenu, QDialog
)
from PyQt6.QtCore import Qt, pyqtSlot, QTimer, QThread, QThreadPool
from PyQt6.QtGui import QColor, QAction, QTextCursor

from .widgets.settings_dialog import SettingsWidget
from .widgets.series_edit_dialog import SeriesEditDialog
from .action_coordinator import ActionCoordinator
from .compat_table_view import CompatTableView
from .cover_manager import CoverManager
from .refresh_coordinator import RefreshCoordinator
from .row_presenters import build_library_row_items, build_series_row_items
from .table_renderers import ChunkedTableRenderer
from .view_updater import ViewUpdater
from .view_state import ViewState
from .workers import (
    AutoMatchTask,
    DownloadsRefreshTask,
    EmuleSendTask,
    LibraryRefreshTask,
    LibrarySnapshotTask,
    ManualScanTask,
    ManualUpdateTask,
    MissingRefreshTask,
    SeriesRefreshTask,
    TMDBRescanTask,
)
from core.scheduler import SchedulerThread
from core.database.db_manager import DatabaseManager
from core.repositories import DecisionLogRepository
from core.services import DiagnosticsService
from core.tmdb.api_client import TMDBClient
from core.tmdb.auto_matcher import TMDBAutoMatcher
from core.tmdb.recalc_thread import TMDBRecalcThread
from config.settings import Settings
import logging
import threading


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()
        self.decision_log_repository = DecisionLogRepository(self.db)
        self.diagnostics_service = DiagnosticsService(self.db, self.decision_log_repository)
        self.logger = logging.getLogger(__name__)
        self.view_state = ViewState()
        self.view_updater = ViewUpdater(self)
        self.action_coordinator = ActionCoordinator(self)
        self.refresh_coordinator = RefreshCoordinator(self)
        self.cover_manager = CoverManager(self)
        self.worker_pool = QThreadPool()
        self.worker_pool.setMaxThreadCount(max(2, QThread.idealThreadCount()))
        self.scheduler_thread = None
        self.tmdb_recalc_thread = None
        self.manual_scan_task = None
        self.manual_update_task = None
        self.library_refresh_task = None
        self.library_snapshot_task = None
        self.series_refresh_task = None
        self.missing_refresh_task = None
        self.downloads_refresh_task = None
        self.emule_send_task = None
        self.tmdb_rescan_task = None
        self.auto_match_task = None
        self.series_renderer = None
        self.library_renderer = None
        self.missing_renderer = None
        self.series_row_index = {}
        self.library_row_index = {}
        self.render_perf = {
            "series": {},
            "library": {},
            "missing": {},
        }
        self.render_chunk_perf = {
            "series": {},
            "library": {},
            "missing": {},
        }
        self.perf_threshold_ms = {
            "render_total": 120.0,
            "render_chunk": 35.0,
            "cover_refresh": 25.0,
            "refresh_handler": 60.0,
            "cover_load": 150.0,
        }
        self.cover_row_height = 84
        self.cover_thumb_height = 80
        self.cover_thumb_width = 60
        self.all_series_list = []
        self.current_series_list = []
        self.all_single_file_list = []
        self.current_single_file_list = []
        self.tabs = None
        self.series_tab = None
        self.missing_tab = None
        self.library_tab = None
        self.missing_list_dirty = True
        self.library_list_dirty = True
        self.scheduler_start_pending = False
        self.log_buffer = []
        self.log_flush_max_messages = 120
        self.log_text_max_blocks = 4000
        self.log_flush_timer = QTimer(self)
        self.log_flush_timer.setInterval(150)
        self.log_flush_timer.timeout.connect(self.flush_log_buffer)
        self.startup_errors = []
        self.libraries_refresh_scheduled = False
        self.pending_library_refresh_requested = False
        self.pending_library_refresh_identification = False
        self.pending_series_refresh = False
        self.pending_missing_refresh = False
        self.pending_downloads_refresh = False
        self.render_requested_signature = {
            "series": None,
            "library": None,
            "missing": None,
        }
        self.last_completed_render_signature = {
            "series": None,
            "library": None,
            "missing": None,
        }
        self.emule_send_context = None
        self.tmdb_rescan_context = None
        self.table_render_chunk_size = 30
        self.is_closing = False
        
        self.setWindowTitle("Series Manager - Hispashare + TMDB")
        self.setMinimumSize(1200, 800)
        
        # Verificar configuración
        self.startup_errors = Settings.validate()
        
        self.init_ui()
        self.init_renderers()
        self.cover_manager.init_lazy_cover_loading()

        if self.startup_errors:
            QTimer.singleShot(300, self.show_startup_configuration_warning)
        QTimer.singleShot(800, self.defer_initial_library_refresh)
        
        # Iniciar scheduler si la configuración es válida
        if not self.startup_errors:
            self.scheduler_start_pending = True
            QTimer.singleShot(2000, self.defer_scheduler_start)

    @property
    def all_series_list(self):
        return self.view_state.series.all_items

    @all_series_list.setter
    def all_series_list(self, value):
        self.view_state.series.set_all_items(value)

    @property
    def current_series_list(self):
        return self.view_state.series.visible_items

    @current_series_list.setter
    def current_series_list(self, value):
        self.view_state.series.set_visible_items(value)

    @property
    def all_single_file_list(self):
        return self.view_state.libraries.all_items

    @all_single_file_list.setter
    def all_single_file_list(self, value):
        self.view_state.libraries.set_all_items(value)

    @property
    def current_single_file_list(self):
        return self.view_state.libraries.visible_items

    @current_single_file_list.setter
    def current_single_file_list(self, value):
        self.view_state.libraries.set_visible_items(value)

    @property
    def missing_list_dirty(self):
        return self.view_state.missing.dirty

    @missing_list_dirty.setter
    def missing_list_dirty(self, value):
        self.view_state.missing.dirty = bool(value)

    @property
    def library_list_dirty(self):
        return self.view_state.libraries.dirty

    @library_list_dirty.setter
    def library_list_dirty(self, value):
        self.view_state.libraries.dirty = bool(value)

    @property
    def library_refresh_task(self):
        return self.refresh_coordinator.library_refresh_task

    @library_refresh_task.setter
    def library_refresh_task(self, value):
        self.refresh_coordinator.library_refresh_task = value

    @property
    def library_snapshot_task(self):
        return self.refresh_coordinator.library_snapshot_task

    @library_snapshot_task.setter
    def library_snapshot_task(self, value):
        self.refresh_coordinator.library_snapshot_task = value

    @property
    def series_refresh_task(self):
        return self.refresh_coordinator.series_refresh_task

    @series_refresh_task.setter
    def series_refresh_task(self, value):
        self.refresh_coordinator.series_refresh_task = value

    @property
    def missing_refresh_task(self):
        return self.refresh_coordinator.missing_refresh_task

    @missing_refresh_task.setter
    def missing_refresh_task(self, value):
        self.refresh_coordinator.missing_refresh_task = value

    @property
    def downloads_refresh_task(self):
        return self.refresh_coordinator.downloads_refresh_task

    @downloads_refresh_task.setter
    def downloads_refresh_task(self, value):
        self.refresh_coordinator.downloads_refresh_task = value

    @property
    def pending_library_refresh_requested(self):
        return self.refresh_coordinator.pending_library_refresh_requested

    @pending_library_refresh_requested.setter
    def pending_library_refresh_requested(self, value):
        self.refresh_coordinator.pending_library_refresh_requested = bool(value)

    @property
    def pending_library_refresh_identification(self):
        return self.refresh_coordinator.pending_library_refresh_identification

    @pending_library_refresh_identification.setter
    def pending_library_refresh_identification(self, value):
        self.refresh_coordinator.pending_library_refresh_identification = bool(value)

    @property
    def pending_series_refresh(self):
        return self.refresh_coordinator.pending_series_refresh

    @pending_series_refresh.setter
    def pending_series_refresh(self, value):
        self.refresh_coordinator.pending_series_refresh = bool(value)

    @property
    def pending_missing_refresh(self):
        return self.refresh_coordinator.pending_missing_refresh

    @pending_missing_refresh.setter
    def pending_missing_refresh(self, value):
        self.refresh_coordinator.pending_missing_refresh = bool(value)

    @property
    def pending_downloads_refresh(self):
        return self.refresh_coordinator.pending_downloads_refresh

    @pending_downloads_refresh.setter
    def pending_downloads_refresh(self, value):
        self.refresh_coordinator.pending_downloads_refresh = bool(value)

    @property
    def manual_update_task(self):
        return self.action_coordinator.manual_update_task

    @manual_update_task.setter
    def manual_update_task(self, value):
        self.action_coordinator.manual_update_task = value

    @property
    def emule_send_task(self):
        return self.action_coordinator.emule_send_task

    @emule_send_task.setter
    def emule_send_task(self, value):
        self.action_coordinator.emule_send_task = value

    @property
    def tmdb_rescan_task(self):
        return self.action_coordinator.tmdb_rescan_task

    @tmdb_rescan_task.setter
    def tmdb_rescan_task(self, value):
        self.action_coordinator.tmdb_rescan_task = value

    @property
    def auto_match_task(self):
        return self.action_coordinator.auto_match_task

    @auto_match_task.setter
    def auto_match_task(self, value):
        self.action_coordinator.auto_match_task = value

    @property
    def emule_send_context(self):
        return self.action_coordinator.emule_send_context

    @emule_send_context.setter
    def emule_send_context(self, value):
        self.action_coordinator.emule_send_context = value

    @property
    def tmdb_rescan_context(self):
        return self.action_coordinator.tmdb_rescan_context

    @tmdb_rescan_context.setter
    def tmdb_rescan_context(self, value):
        self.action_coordinator.tmdb_rescan_context = value

    def show_startup_configuration_warning(self):
        QMessageBox.warning(
            self,
            "Configuración Incompleta",
            "Por favor completa la configuración:\n" + "\n".join(self.startup_errors)
        )

    def show_information(self, title, message):
        QMessageBox.information(self, title, message)

    def show_warning(self, title, message):
        QMessageBox.warning(self, title, message)

    def show_error(self, title, message):
        QMessageBox.critical(self, title, message)

    def defer_initial_library_refresh(self):
        if self.libraries_refresh_scheduled:
            return
        self.libraries_refresh_scheduled = True
        self.library_list_dirty = True

    def defer_scheduler_start(self):
        if self.is_closing or self.startup_errors or not self.scheduler_start_pending:
            return
        # Esperar a que terminen refrescos y renders iniciales: el ciclo del
        # scheduler (Selenium + escaneo + BD) compite por CPU/GIL con la UI.
        coordinator = self.refresh_coordinator
        initial_work_running = (
            self.library_refresh_task is not None
            or coordinator.series_refresh_task is not None
            or coordinator.missing_refresh_task is not None
            or any(self._is_render_active(kind) for kind in ("series", "library", "missing"))
        )
        if initial_work_running:
            QTimer.singleShot(1500, self.defer_scheduler_start)
            return
        self.scheduler_start_pending = False
        self.start_scheduler()
    
    def init_ui(self):
        """Inicializar interfaz de usuario"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_series_tab(), "📺 Series")
        self.tabs.addTab(self.create_libraries_tab(), "📚 Bibliotecas")
        self.tabs.addTab(self.create_missing_tab(), "❌ Episodios Faltantes")
        self.tabs.addTab(self.create_downloads_tab(), "📥 Descargas")
        self.tabs.addTab(self.create_log_tab(), "📋 Logs")
        self.tabs.addTab(self.create_settings_tab(), "⚙️ Configuración")
        self.tabs.currentChanged.connect(self.on_tab_changed)
        
        layout.addWidget(self.tabs)
        
        # Barra de estado
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        # Progress bar
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(300)
        self.status_bar.addPermanentWidget(self.progress)
        
        self.status_label = QLabel("Listo")
        self.status_bar.addWidget(self.status_label)

    def init_renderers(self):
        self.series_renderer = ChunkedTableRenderer(
            self.series_table,
            row_height=self.cover_row_height,
            chunk_size=self.table_render_chunk_size,
            populate_row=self.populate_series_row,
            should_abort=lambda: self.is_closing,
            before_render=self._on_series_render_scheduled,
            before_chunk=self._on_series_render_chunk_start,
            after_chunk=self._on_series_render_chunk_finished,
            parent=self,
        )
        self.library_renderer = ChunkedTableRenderer(
            self.library_table,
            row_height=self.cover_row_height,
            chunk_size=self.table_render_chunk_size,
            populate_row=self.populate_single_file_library_row,
            should_abort=lambda: self.is_closing,
            before_render=self._on_library_render_scheduled,
            before_chunk=self._on_library_render_chunk_start,
            after_chunk=self._on_library_render_chunk_finished,
            parent=self,
        )
        self.missing_renderer = ChunkedTableRenderer(
            self.missing_table,
            row_height=self.missing_table.verticalHeader().defaultSectionSize(),
            chunk_size=self.table_render_chunk_size,
            populate_row=self.populate_missing_row,
            should_abort=lambda: self.is_closing,
            before_render=self._on_missing_render_scheduled,
            before_chunk=self._on_missing_render_chunk_start,
            after_chunk=self._on_missing_render_chunk_finished,
            parent=self,
        )

    def _elapsed_ms(self, started_at):
        return (time.perf_counter() - started_at) * 1000.0

    def _log_slow_operation(self, operation_name, elapsed_ms, threshold_ms, **data):
        if elapsed_ms < threshold_ms:
            return
        details = ", ".join(
            f"{key}={value}"
            for key, value in data.items()
            if value is not None
        )
        if details:
            self.logger.info(f"[PERF] {operation_name}: {elapsed_ms:.1f} ms ({details})")
        else:
            self.logger.info(f"[PERF] {operation_name}: {elapsed_ms:.1f} ms")

    def _start_render_perf(self, table_kind, token, items):
        self.render_perf[table_kind].clear()
        self.render_perf[table_kind][token] = {
            "started_at": time.perf_counter(),
            "items_count": len(items),
        }
        self.render_chunk_perf[table_kind].clear()

    def _start_render_chunk_perf(self, table_kind, token, start):
        self.render_chunk_perf[table_kind][(token, start)] = time.perf_counter()

    def _finish_render_chunk_perf(self, table_kind, token, items, start, end):
        chunk_started_at = self.render_chunk_perf[table_kind].pop((token, start), None)
        if chunk_started_at is not None:
            self._log_slow_operation(
                f"{table_kind}_render_chunk",
                self._elapsed_ms(chunk_started_at),
                self.perf_threshold_ms["render_chunk"],
                token=token,
                start=start,
                end=end,
                chunk_size=end - start,
            )

        if end < len(items):
            return

        render_state = self.render_perf[table_kind].pop(token, None)
        if render_state is None:
            return

        self._log_slow_operation(
            f"{table_kind}_render_total",
            self._elapsed_ms(render_state["started_at"]),
            self.perf_threshold_ms["render_total"],
            token=token,
            items=render_state["items_count"],
        )
        self.last_completed_render_signature[table_kind] = self.render_requested_signature.get(table_kind)

    def _is_render_active(self, table_kind):
        return bool(self.render_perf.get(table_kind))

    def _build_render_signature(self, items):
        # Firma O(1): longitud + muestras de primer/último item.
        # Reemplaza el SHA1 O(n×14) que iteraba todos los items en el hilo UI.
        # Detecta: cambios de count, rotación de items, cambio en primer/último.
        n = len(items) if items else 0
        if n == 0:
            return "empty:0"
        first, last = items[0], items[-1]
        return (
            f"{n}"
            f"|{first.get('id')}|{last.get('id')}"
            f"|{first.get('updated_at','')}|{last.get('updated_at','')}"
            f"|{first.get('missing_count','')}|{last.get('missing_count','')}"
            f"|{first.get('hispashare_url','')}|{last.get('hispashare_url','')}"
        )

    def _get_table_for_kind(self, table_kind):
        if table_kind == "series":
            return self.series_table
        if table_kind == "library":
            return self.library_table
        if table_kind == "missing":
            return self.missing_table
        return None

    def _should_skip_render(self, table_kind, items, signature):
        if self.render_requested_signature.get(table_kind) == signature and self._is_render_active(table_kind):
            return True

        table = self._get_table_for_kind(table_kind)
        if table is None:
            return False

        return (
            self.last_completed_render_signature.get(table_kind) == signature
            and not self._is_render_active(table_kind)
            and table.rowCount() == len(items or [])
        )
    
    def create_series_tab(self):
        """Tab con lista de series"""
        widget = QWidget()
        self.series_tab = widget
        layout = QVBoxLayout(widget)
        
        # Botones superiores
        btn_layout = QHBoxLayout()
        
        self.btn_scan = QPushButton("🔍 Escanear Ahora")
        self.btn_scan.clicked.connect(self.manual_scan)
        
        self.btn_refresh = QPushButton("🔄 Refrescar")
        self.btn_refresh.clicked.connect(self.refresh_series_list)

        self.btn_recalc_tmdb = QPushButton("🎬 Recalcular Confianza TMDB")
        self.btn_recalc_tmdb.clicked.connect(self.recalculate_all_tmdb_confidence)

        self.series_filter_combo = QComboBox()
        self.series_filter_combo.addItem("Todas", None)
        self.series_filter_combo.addItem("Series", "series")
        self.series_filter_combo.addItem("Dibujos", "cartoons")
        self.series_filter_combo.addItem("Documentales Series", "docuseries")
        self.series_filter_combo.currentIndexChanged.connect(self.on_series_filter_changed)

        self.series_filter_label = QLabel("Mostrando todas las categorías episódicas")
        
        btn_layout.addWidget(self.btn_scan)
        btn_layout.addWidget(self.btn_refresh)
        btn_layout.addWidget(self.btn_recalc_tmdb)
        btn_layout.addWidget(QLabel("Categoría:"))
        btn_layout.addWidget(self.series_filter_combo)
        btn_layout.addWidget(self.series_filter_label)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        # Tabla de series
        self.series_table = CompatTableView()
        self.series_table.setColumnCount(9)
        self.series_table.setHorizontalHeaderLabels([
            "Nombre", "Categoría", "Año", "TMDB ID", "Confianza", 
            "Faltantes", "URL Hispashare", "Carátula", "Estado"
        ])
        
        # Ajustar columnas
        header = self.series_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        # Solo medir filas visibles para ResizeToContents (0 = solo visible, -1 = todas)
        header.setResizeContentsPrecision(0)
        self.series_table.verticalHeader().setDefaultSectionSize(self.cover_row_height)
        
        self.series_table.setAlternatingRowColors(True)
        self.series_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.series_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        
        # Conectar eventos para edición
        self.series_table.itemDoubleClicked.connect(self.edit_series)
        self.series_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.series_table.customContextMenuRequested.connect(self.show_series_context_menu)
        
        layout.addWidget(self.series_table)
        
        # Cargar datos iniciales
        self.refresh_series_list()
        
        return widget

    def create_libraries_tab(self):
        """Tab para bibliotecas de contenido de un solo archivo."""
        widget = QWidget()
        self.library_tab = widget
        layout = QVBoxLayout(widget)

        btn_layout = QHBoxLayout()
        self.btn_refresh_libraries = QPushButton("🔄 Refrescar Bibliotecas")
        self.btn_refresh_libraries.clicked.connect(
            lambda _checked=False: self.start_library_refresh(run_identification=True, force=True)
        )
        
        self.library_filter_combo = QComboBox()
        self.library_filter_combo.addItem("Todas", None)
        self.library_filter_combo.addItem("Películas", "movies")
        self.library_filter_combo.addItem("Documentales", "documentaries")
        self.library_filter_combo.addItem("Películas Animación", "animated_movies")
        self.library_filter_combo.addItem("Conciertos", "concerts")
        self.library_filter_combo.addItem("Música", "music")
        self.library_filter_combo.currentIndexChanged.connect(self.on_library_filter_changed)

        self.library_filter_label = QLabel("Mostrando todas las bibliotecas de archivo único")

        btn_layout.addWidget(self.btn_refresh_libraries)
        btn_layout.addWidget(QLabel("Categoría:"))
        btn_layout.addWidget(self.library_filter_combo)
        btn_layout.addWidget(self.library_filter_label)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.library_summary_label = QLabel("Contenido de archivo único ya existente en tus bibliotecas")
        layout.addWidget(self.library_summary_label)

        self.library_table = CompatTableView()
        self.library_table.setColumnCount(9)
        self.library_table.setHorizontalHeaderLabels([
            "Nombre", "Categoría", "Año", "TMDB ID", "Confianza",
            "Vídeos", "URL Hispashare", "Carátula", "Estado"
        ])
        header = self.library_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        # Solo medir filas visibles para ResizeToContents — la tabla tiene 7
        # columnas con este modo; con -1 (defecto) Qt mide TODAS las filas.
        header.setResizeContentsPrecision(0)
        self.library_table.verticalHeader().setDefaultSectionSize(self.cover_row_height)
        self.library_table.setAlternatingRowColors(True)
        self.library_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.library_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.library_table.itemDoubleClicked.connect(self.edit_library)
        self.library_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library_table.customContextMenuRequested.connect(self.show_library_context_menu)
        layout.addWidget(self.library_table)

        return widget

    def recalculate_all_tmdb_confidence(self):
        reply = QMessageBox.question(
            self,
            "Recalcular TMDB",
            "Esto recalculará la confianza TMDB de todas las series.\n\n"
            "Puede tardar y hará peticiones a TMDB.\n\n¿Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self.tmdb_recalc_thread and self.tmdb_recalc_thread.isRunning():
            QMessageBox.information(self, "TMDB", "Ya hay un recálculo en curso")
            return

        self.btn_scan.setEnabled(False)
        self.btn_refresh.setEnabled(False)
        self.btn_recalc_tmdb.setEnabled(False)
        self.append_log("🎬 Iniciando recálculo de confianza TMDB para todas las series...")

        self.tmdb_recalc_thread = TMDBRecalcThread()
        self.tmdb_recalc_thread.progress_signal.connect(self.update_progress)
        self.tmdb_recalc_thread.log_signal.connect(self.append_log)
        self.tmdb_recalc_thread.series_updated_signal.connect(self.refresh_series_row)
        self.tmdb_recalc_thread.finished_signal.connect(self.on_tmdb_recalc_finished)
        self.tmdb_recalc_thread.error_signal.connect(self.on_tmdb_recalc_error)
        self.tmdb_recalc_thread.finished.connect(self.on_tmdb_recalc_thread_stopped)
        self.tmdb_recalc_thread.start()

    def on_tmdb_recalc_finished(self, total, updated, failed):
        self.refresh_series_list()
        self.append_log(f"✅ Recálculo TMDB completado: {updated}/{total} actualizadas, {failed} con fallo")
        self.btn_scan.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.btn_recalc_tmdb.setEnabled(True)

    def on_tmdb_recalc_error(self, message):
        QMessageBox.critical(self, "TMDB", f"Error en recálculo TMDB:\n{message}")
        self.btn_scan.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.btn_recalc_tmdb.setEnabled(True)
    
    def create_missing_tab(self):
        """Tab con episodios faltantes"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Filtros
        filter_layout = QHBoxLayout()
        self.filter_label = QLabel("Episodios que faltan en tu biblioteca")
        filter_layout.addWidget(self.filter_label)
        
        # Contador total de episodios faltantes
        self.total_missing_label = QLabel("Total: 0 episodios")
        self.total_missing_label.setStyleSheet("font-weight: bold; color: #d32f2f;")
        filter_layout.addWidget(self.total_missing_label)
        
        filter_layout.addStretch()
        
        self.btn_refresh_missing = QPushButton("🔄 Refrescar")
        self.btn_refresh_missing.clicked.connect(lambda _checked=False: self.refresh_missing_list(force=True))
        filter_layout.addWidget(self.btn_refresh_missing)
        
        layout.addLayout(filter_layout)
        
        # Tabla de faltantes
        self.missing_table = QTableWidget()
        self.missing_table.setColumnCount(6)
        self.missing_table.setHorizontalHeaderLabels([
            "Serie", "Temporada", "Episodio", "Nombre",
            "Fecha Emisión", "Estado"
        ])
        
        header = self.missing_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        
        self.missing_table.setAlternatingRowColors(True)
        
        layout.addWidget(self.missing_table)
        
        self.missing_tab = widget
        return widget
    
    def create_downloads_tab(self):
        """Tab para gestionar descargas de episodios"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Botones superiores
        btn_layout = QHBoxLayout()
        
        self.btn_refresh_downloads = QPushButton("🔄 Refrescar Enlaces")
        self.btn_refresh_downloads.clicked.connect(self.refresh_downloads)
        
        btn_clear_downloads = QPushButton("🗑️ Limpiar Enlaces")
        btn_clear_downloads.clicked.connect(self.clear_downloads)
        
        btn_copy_all = QPushButton("📋 Copiar Todos")
        btn_copy_all.clicked.connect(self.copy_all_links)
        
        self.btn_send_emule = QPushButton("🔗 Enviar a eMule")
        self.btn_send_emule.clicked.connect(self.send_all_to_emule)
        
        btn_layout.addWidget(self.btn_refresh_downloads)
        btn_layout.addWidget(btn_clear_downloads)
        btn_layout.addWidget(btn_copy_all)
        btn_layout.addWidget(self.btn_send_emule)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        # Área de texto para mostrar enlaces ed2k
        self.downloads_text = QTextEdit()
        self.downloads_text.setReadOnly(True)
        self.downloads_text.setPlaceholderText("Los enlaces ed2k aparecerán aquí después de procesar las descargas...")
        self.downloads_text.setStyleSheet("""
            QTextEdit {
                background-color: #f8f9fa;
                border: 1px solid #dee2e6;
                font-family: 'Courier New', monospace;
                font-size: 9pt;
                padding: 10px;
            }
        """)
        
        layout.addWidget(self.downloads_text)
        
        # Información
        info_label = QLabel("💡 Los enlaces ed2k se generan automáticamente cuando el scheduler procesa las series con URLs de Hispashare")
        info_label.setStyleSheet("color: #6c757d; font-style: italic; padding: 5px;")
        layout.addWidget(info_label)
        
        return widget
    
    def create_log_tab(self):
        """Tab con logs en tiempo real"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.document().setMaximumBlockCount(self.log_text_max_blocks)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: 'Courier New', monospace;
                font-size: 10pt;
            }
        """)
        layout.addWidget(self.log_text)
        
        # Botones
        btn_layout = QHBoxLayout()
        btn_clear = QPushButton("🗑️ Limpiar Logs")
        btn_clear.clicked.connect(self.clear_logs)
        btn_layout.addWidget(btn_clear)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        return widget

    def clear_logs(self):
        self.log_buffer.clear()
        if self.log_flush_timer.isActive():
            self.log_flush_timer.stop()
        if hasattr(self, 'log_text'):
            self.log_text.clear()
    
    def create_settings_tab(self):
        """Tab de configuración"""
        settings_widget = SettingsWidget()
        settings_widget.settings_saved.connect(self.on_settings_saved)
        return settings_widget
    
    def start_scheduler(self):
        """Iniciar scheduler en thread separado"""
        if self.scheduler_thread and self.scheduler_thread.isRunning():
            self.logger.warning("Scheduler ya está ejecutándose")
            return
        
        self.scheduler_thread = SchedulerThread()
        self.scheduler_thread.progress_signal.connect(self.update_progress)
        self.scheduler_thread.log_signal.connect(self.append_log)
        self.scheduler_thread.finished_signal.connect(self.on_cycle_finished)
        self.scheduler_thread.error_signal.connect(self.on_scheduler_error)
        self.scheduler_thread.url_saved_signal.connect(self.on_url_saved)
        self.scheduler_thread.tmdb_matched_signal.connect(self.on_tmdb_matched)
        self.scheduler_thread.series_updated_signal.connect(self.on_series_updated)
        self.scheduler_thread.finished.connect(self.on_scheduler_thread_stopped)
        self.scheduler_thread.start()
        
        self.append_log("✅ Scheduler iniciado correctamente")

    @pyqtSlot()
    def on_scheduler_thread_stopped(self):
        self.logger.info("Thread del scheduler finalizado")
        self.scheduler_thread = None

    @pyqtSlot()
    def on_tmdb_recalc_thread_stopped(self):
        self.logger.info("Thread de recálculo TMDB finalizado")
        self.tmdb_recalc_thread = None

    @pyqtSlot()
    def on_manual_scan_task_done(self):
        self.logger.info("Tarea de escaneo manual finalizada")
        self.manual_scan_task = None

    @pyqtSlot()
    def on_library_refresh_task_done(self):
        self.logger.info("Tarea de refresco de bibliotecas finalizada")
        self.refresh_coordinator.on_library_refresh_task_done()

    @pyqtSlot()
    def on_library_snapshot_task_done(self):
        self.logger.info("Tarea de snapshot rapido de bibliotecas finalizada")
        self.refresh_coordinator.on_library_snapshot_task_done()

    @pyqtSlot()
    def on_series_refresh_task_done(self):
        self.logger.info("Tarea de refresco de series finalizada")
        self.refresh_coordinator.on_series_refresh_task_done()

    @pyqtSlot()
    def on_missing_refresh_task_done(self):
        self.logger.info("Tarea de refresco de faltantes finalizada")
        self.refresh_coordinator.on_missing_refresh_task_done()

    @pyqtSlot()
    def on_downloads_refresh_task_done(self):
        self.logger.info("Tarea de refresco de descargas finalizada")
        self.refresh_coordinator.on_downloads_refresh_task_done()

    @pyqtSlot()
    def on_emule_send_task_done(self):
        self.logger.info("Tarea de envio a eMule finalizada")
        self.action_coordinator.on_emule_send_task_done()
        self._set_downloads_busy(False)

    @pyqtSlot()
    def on_tmdb_rescan_task_done(self):
        self.logger.info("Tarea de reescaneo TMDB finalizada")
        self.action_coordinator.on_tmdb_rescan_task_done()

    @pyqtSlot()
    def on_auto_match_task_done(self):
        self.logger.info("Tarea de auto-match TMDB finalizada")
        self.action_coordinator.on_auto_match_task_done()

    @pyqtSlot()
    def on_search_reactivate_task_done(self):
        self.logger.info("Tarea de reactivacion de busqueda finalizada")
        self.action_coordinator.on_search_reactivate_task_done()

    @pyqtSlot(int)
    def on_cover_url_resolve_task_done(self, series_id):
        self.action_coordinator.on_cover_url_resolve_task_done(series_id)

    def _stop_thread(self, thread, name, timeout_ms=8000):
        if not thread:
            return True
        if not thread.isRunning():
            return True

        self.logger.info(f"Deteniendo hilo: {name}")
        stop_method = getattr(thread, "stop", None)
        if callable(stop_method):
            try:
                stop_method()
            except Exception as e:
                self.logger.warning(f"Error llamando stop() en {name}: {e}")

        quit_method = getattr(thread, "quit", None)
        if callable(quit_method):
            try:
                quit_method()
            except Exception:
                pass

        if thread.wait(timeout_ms):
            self.logger.info(f"Hilo detenido correctamente: {name}")
            return True

        self.logger.warning(f"Timeout esperando hilo: {name}")
        return False

    def _stop_cover_loaders(self, timeout_ms=3000):
        self.cover_manager.cancel_all_loaders()
        return []

    def _stop_pool_task(self, task, name):
        if not task:
            return True
        self.logger.info(f"Solicitando cancelación de tarea: {name}")
        try:
            task.stop()
            return True
        except Exception as e:
            self.logger.warning(f"Error cancelando tarea {name}: {e}")
            return False

    def _stop_background_operations(self):
        managed_refresh = self.refresh_coordinator.cancel_managed_tasks(self._stop_pool_task)
        managed_actions = self.action_coordinator.cancel_managed_tasks(self._stop_pool_task)
        return {
            "manual_scan": self._stop_pool_task(self.manual_scan_task, "manual_scan"),
            "manual_update": managed_actions["manual_update"],
            "library_refresh": managed_refresh["library_refresh"],
            "library_snapshot": managed_refresh["library_snapshot"],
            "series_refresh": managed_refresh["series_refresh"],
            "missing_refresh": managed_refresh["missing_refresh"],
            "downloads_refresh": managed_refresh["downloads_refresh"],
            "emule_send": managed_actions["emule_send"],
            "tmdb_rescan": managed_actions["tmdb_rescan"],
            "auto_match": managed_actions["auto_match"],
        }
    
    @pyqtSlot(int, str)
    def update_progress(self, value, message):
        """Actualizar barra de progreso"""
        self.progress.setValue(value)
        self.status_label.setText(message)
    
    @pyqtSlot(str)
    def append_log(self, message):
        """Agregar mensaje al log"""
        self.log_buffer.append(str(message))
        if not self.log_flush_timer.isActive():
            self.log_flush_timer.start()

    @pyqtSlot()
    def flush_log_buffer(self):
        if not self.log_buffer:
            self.log_flush_timer.stop()
            return
        if not hasattr(self, 'log_text'):
            return

        messages = self.log_buffer[:self.log_flush_max_messages]
        del self.log_buffer[:len(messages)]
        scrollbar = self.log_text.verticalScrollBar()
        should_autoscroll = scrollbar.value() >= max(0, scrollbar.maximum() - 4)
        self.log_text.moveCursor(QTextCursor.MoveOperation.End)
        self.log_text.insertPlainText("\n".join(messages) + "\n")
        if should_autoscroll:
            scrollbar.setValue(scrollbar.maximum())
        if self.log_buffer:
            self.log_flush_timer.start(25)
        else:
            self.log_flush_timer.stop()
    
    @pyqtSlot()
    def on_cycle_finished(self):
        """Ciclo de actualización completado"""
        self.refresh_series_list()
        self.request_library_refresh(run_identification=False)
        self.status_label.setText("✅ Ciclo completado")
    
    @pyqtSlot(str)
    def on_scheduler_error(self, error_msg):
        """Error en el scheduler"""
        QMessageBox.critical(self, "Error", f"Error en el scheduler:\n{error_msg}")
    
    def manual_scan(self):
        """Escaneo manual"""
        if self.manual_scan_task is not None:
            QMessageBox.information(self, "Escaneo", "Ya hay un escaneo manual en curso")
            return

        self.btn_scan.setEnabled(False)
        self.btn_refresh.setEnabled(False)
        self.append_log("🔍 Iniciando escaneo manual...")
        self.status_label.setText("🔍 Escaneando biblioteca episódica...")

        self.manual_scan_task = ManualScanTask()
        self.manual_scan_task.signals.log_signal.connect(self.append_log)
        self.manual_scan_task.signals.finished_signal.connect(self.on_manual_scan_finished)
        self.manual_scan_task.signals.error_signal.connect(self.on_manual_scan_error)
        self.manual_scan_task.signals.done_signal.connect(self.on_manual_scan_task_done)
        self.worker_pool.start(self.manual_scan_task)

    @pyqtSlot(dict)
    def on_manual_scan_finished(self, payload):
        processed = payload.get("processed", 0)
        matched = payload.get("matched", 0)

        self.btn_scan.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.refresh_series_list()
        self.request_library_refresh(run_identification=False)
        self.update_progress(100, "Escaneo completado")
        self.append_log(f"✅ Escaneo completado: {processed} series procesadas")
        if matched:
            self.append_log(f"🎬 Matching TMDB automático: {matched} series vinculadas")

        QMessageBox.information(
            self,
            "Escaneo Completado",
            f"Se procesaron {processed} series correctamente."
        )

    @pyqtSlot(str)
    def on_manual_scan_error(self, message):
        self.btn_scan.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.logger.error(f"Error en escaneo manual: {message}")
        self.append_log(f"❌ Error: {message}")
        self.status_label.setText("❌ Error en escaneo manual")
        QMessageBox.critical(self, "Error", f"Error durante el escaneo:\n{message}")

    def get_selected_episodic_categories(self):
        selected_category = self.series_filter_combo.currentData() if hasattr(self, 'series_filter_combo') else None
        if selected_category:
            return [selected_category]
        return list(self.db.get_episodic_categories())

    def on_series_filter_changed(self):
        self.apply_series_filter(refresh_missing=False)

    def get_selected_single_file_categories(self):
        selected_category = self.library_filter_combo.currentData() if hasattr(self, 'library_filter_combo') else None
        if selected_category:
            return [selected_category]
        return Settings.get_single_file_categories()

    def on_library_filter_changed(self):
        if self.all_single_file_list:
            self.apply_library_filter()
        else:
            self.start_library_refresh(run_identification=False, force=True)

    def is_missing_tab_active(self):
        return self.tabs is not None and self.missing_tab is not None and self.tabs.currentWidget() is self.missing_tab

    def is_series_tab_active(self):
        return self.tabs is not None and self.series_tab is not None and self.tabs.currentWidget() is self.series_tab

    def is_library_tab_active(self):
        return self.tabs is not None and self.library_tab is not None and self.tabs.currentWidget() is self.library_tab

    def on_tab_changed(self, _index):
        if self.is_missing_tab_active() and self.missing_list_dirty:
            self.refresh_missing_list(force=True)
        if self.is_library_tab_active():
            self.schedule_visible_cover_refresh("library", delay_ms=0)
            if not self.all_single_file_list:
                self.start_library_snapshot_refresh(force=True)
            if self.library_list_dirty:
                self.start_library_refresh(run_identification=False, force=True)
            elif self.all_single_file_list:
                self.apply_library_filter()
        else:
            self.schedule_visible_cover_refresh("series", delay_ms=0)

    def _set_library_refresh_busy(self, is_busy):
        if hasattr(self, 'btn_refresh_libraries'):
            self.btn_refresh_libraries.setEnabled(not is_busy)
        if hasattr(self, 'library_filter_combo'):
            self.library_filter_combo.setEnabled(not is_busy)

    def _set_series_refresh_busy(self, is_busy):
        if hasattr(self, 'btn_refresh'):
            self.btn_refresh.setEnabled(not is_busy)
        if hasattr(self, 'series_filter_combo'):
            self.series_filter_combo.setEnabled(not is_busy)

    def _set_missing_refresh_busy(self, is_busy):
        if hasattr(self, 'btn_refresh_missing'):
            self.btn_refresh_missing.setEnabled(not is_busy)

    def _set_downloads_busy(self, is_busy=None):
        busy = self.downloads_refresh_task is not None or self.emule_send_task is not None
        if is_busy is not None:
            busy = busy or is_busy
        if hasattr(self, 'btn_refresh_downloads'):
            self.btn_refresh_downloads.setEnabled(not busy)
        if hasattr(self, 'btn_send_emule'):
            self.btn_send_emule.setEnabled(not busy)

    def request_library_refresh(self, run_identification=False):
        self.refresh_coordinator.request_library_refresh(run_identification=run_identification)

    def start_library_snapshot_refresh(self, force=False):
        self.refresh_coordinator.start_library_snapshot_refresh(force=force)

    def start_library_refresh(self, run_identification=False, force=False):
        self.refresh_coordinator.start_library_refresh(
            run_identification=run_identification,
            force=force,
        )

    @pyqtSlot(dict)
    def on_library_snapshot_finished(self, payload):
        items = payload.get("items", [])
        if not self.all_single_file_list and items:
            self.sync_cover_url_cache(items)
            self.view_state.libraries.set_all_items(items)
            self.apply_library_filter()
            if hasattr(self, 'library_summary_label'):
                self.library_summary_label.setText(
                    f"Bibliotecas cargadas desde BD: {len(items)} títulos. Refresco completo en segundo plano..."
                )

    @pyqtSlot(str)
    def on_library_snapshot_error(self, message):
        self.logger.error(f"Error cargando snapshot de bibliotecas: {message}")
        self.append_log(f"❌ Error cargando snapshot de bibliotecas: {message}")

    @pyqtSlot(dict)
    def on_library_refresh_finished(self, payload):
        started_at = time.perf_counter()
        categories = payload.get('categories', [])
        merged_items = payload.get('items', [])
        total_titles = payload.get('total_titles', 0)
        total_items = payload.get('total_items', 0)
        matched = payload.get('matched', 0)
        self.sync_cover_url_cache(merged_items)
        self.view_state.libraries.set_all_items(merged_items, mark_clean=True)
        self.library_list_dirty = False
        self.apply_library_filter()

        if hasattr(self, 'library_summary_label'):
            self.library_summary_label.setText(
                f"Bibliotecas cargadas: {total_titles} títulos y {total_items} archivos detectados"
            )

        if matched:
            self.append_log(f"🎬 Bibliotecas: {matched} elementos de archivo unico vinculados con TMDB")

        self._set_library_refresh_busy(False)
        self.status_label.setText("📚 Bibliotecas actualizadas")
        self._log_slow_operation(
            "library_refresh_handler",
            self._elapsed_ms(started_at),
            self.perf_threshold_ms["refresh_handler"],
            items=len(merged_items),
            categories=len(categories),
            matched=matched,
        )

    @pyqtSlot(str)
    def on_library_refresh_error(self, message):
        self._set_library_refresh_busy(False)
        self.pending_library_refresh_requested = False
        self.pending_library_refresh_identification = False
        self.library_list_dirty = True
        self.logger.error(f"Error refrescando bibliotecas: {message}")
        self.append_log(f"❌ Error refrescando bibliotecas: {message}")
        self.status_label.setText("❌ Error refrescando bibliotecas")

    def _set_manual_update_busy(self, busy, message=None):
        if message:
            self.status_label.setText(message)

    def start_manual_update(self, item_data, changes, *, is_library=False):
        self.action_coordinator.start_manual_update(
            item_data,
            changes,
            is_library=is_library,
        )

    def start_search_reactivation(self, item_data, *, is_library=False):
        self.action_coordinator.start_search_reactivation(item_data, is_library=is_library)

    def start_immediate_hispashare_search(self, item_data, *, is_library=False, force_recheck=False):
        return self.action_coordinator.start_immediate_hispashare_search(
            item_data,
            is_library=is_library,
            force_recheck=force_recheck,
        )

    def start_cover_url_resolve(self, series_id, hispashare_url):
        self.action_coordinator.start_cover_url_resolve(series_id, hispashare_url)

    @pyqtSlot(dict)
    def on_manual_update_finished(self, payload):
        series_id = payload.get("series_id")
        updated_item = payload.get("updated_item") or {}
        changes = payload.get("changes") or {}
        is_library = payload.get("is_library", False)
        reset_hispashare_url = bool(payload.get("reset_hispashare_url"))
        recheck_hispashare_url = bool(payload.get("recheck_hispashare_url"))
        item_name = updated_item.get("name", "Elemento")
        self.sync_cover_url_cache([updated_item])
        should_run_immediate_hispashare_search = (
            bool(changes.get("tmdb_id"))
            and not (changes.get("hispashare_url") or "").strip()
        )

        if is_library:
            self.refresh_library_row(series_id, updated_item)
            self.append_log(f"✅ Biblioteca '{item_name}' actualizada manualmente")
            if reset_hispashare_url:
                self.append_log("   🔄 URL Hispashare anterior invalidada por cambio manual de TMDB")
            elif recheck_hispashare_url:
                self.append_log("   🔁 URL Hispashare marcada para revalidación con el nuevo TMDB")
            self.status_label.setText("✅ Biblioteca actualizada")
            if should_run_immediate_hispashare_search:
                self.start_immediate_hispashare_search(
                    updated_item,
                    is_library=True,
                    force_recheck=True,
                )
            return

        self.refresh_series_row(series_id, updated_item)
        if payload.get("recalculated_missing"):
            missing_count = payload.get("missing_count", 0)
            if self.is_missing_tab_active():
                self.refresh_missing_list(force=True)
            else:
                self.missing_list_dirty = True
            self.append_log(f"✅ Serie '{item_name}' actualizada manualmente")
            self.append_log(f"   📊 Episodios faltantes recalculados: {missing_count}")
        else:
            self.append_log(f"✅ Serie '{item_name}' actualizada manualmente")
        if reset_hispashare_url:
            self.append_log("   🔄 URL Hispashare anterior invalidada por cambio manual de TMDB")
        elif recheck_hispashare_url:
            self.append_log("   🔁 URL Hispashare marcada para revalidación con el nuevo TMDB")
        self.status_label.setText("✅ Serie actualizada")
        if should_run_immediate_hispashare_search:
            self.start_immediate_hispashare_search(
                updated_item,
                is_library=False,
                force_recheck=True,
            )

    @pyqtSlot(str)
    def on_manual_update_error(self, message):
        self.logger.error(f"Error en actualización manual: {message}")
        self.append_log(f"❌ Error en actualización manual: {message}")
        self.status_label.setText("❌ Error en actualización manual")
        self.show_error("Error", f"Error al actualizar:\n{message}")

    @pyqtSlot()
    def on_manual_update_done(self):
        self.action_coordinator.on_manual_update_done()
        self._set_manual_update_busy(False)

    def refresh_series_list(self):
        """Refrescar lista de series en segundo plano."""
        self.refresh_coordinator.refresh_series_list()

    @pyqtSlot(dict)
    def on_series_refresh_finished(self, payload):
        started_at = time.perf_counter()
        items = payload.get("items", [])
        self.sync_cover_url_cache(items)
        self.view_state.series.set_all_items(items)
        self.apply_series_filter(refresh_missing=self.is_missing_tab_active())
        self._set_series_refresh_busy(False)
        if hasattr(self, 'status_label'):
            self.status_label.setText("📺 Series actualizadas")
        self._log_slow_operation(
            "series_refresh_handler",
            self._elapsed_ms(started_at),
            self.perf_threshold_ms["refresh_handler"],
            items=len(items),
        )

    @pyqtSlot(str)
    def on_series_refresh_error(self, message):
        self._set_series_refresh_busy(False)
        self.logger.error(f"Error refrescando series: {message}")
        self.append_log(f"❌ Error refrescando series: {message}")
        if hasattr(self, 'status_label'):
            self.status_label.setText("❌ Error refrescando series")

    def apply_series_filter(self, refresh_missing=False):
        selected_categories = self.get_selected_episodic_categories()
        series_list = self.view_state.series.filter_by_values(selected_categories)

        if hasattr(self, 'series_filter_label'):
            if len(selected_categories) == 1:
                category_label = Settings.get_category_label(selected_categories[0])
                self.series_filter_label.setText(f"Mostrando solo: {category_label}")
            else:
                self.series_filter_label.setText("Mostrando todas las categorías episódicas")

        self.schedule_series_render(series_list)

        if hasattr(self, 'missing_table'):
            if refresh_missing:
                self.refresh_missing_list(force=True)
            else:
                self.missing_list_dirty = True
    
    def refresh_missing_list(self, force=False):
        """Refrescar lista de episodios faltantes en segundo plano."""
        self.refresh_coordinator.refresh_missing_list(force=force)

    @pyqtSlot(dict)
    def on_missing_refresh_finished(self, payload):
        started_at = time.perf_counter()
        missing_episodes = payload.get("items", [])
        self.view_state.missing.set_all_items(missing_episodes, mark_clean=True)
        self.view_state.missing.set_visible_items(missing_episodes)
        self.missing_list_dirty = False
        self.schedule_missing_render(missing_episodes)
        self._update_missing_total_label(len(missing_episodes))
        self._set_missing_refresh_busy(False)
        if hasattr(self, 'status_label'):
            self.status_label.setText("❌ Episodios faltantes actualizados")
        self._log_slow_operation(
            "missing_refresh_handler",
            self._elapsed_ms(started_at),
            self.perf_threshold_ms["refresh_handler"],
            items=len(missing_episodes),
        )

    @pyqtSlot(str)
    def on_missing_refresh_error(self, message):
        self._set_missing_refresh_busy(False)
        self.missing_list_dirty = True
        self.logger.error(f"Error refrescando faltantes: {message}")
        self.append_log(f"❌ Error refrescando episodios faltantes: {message}")
        if hasattr(self, 'status_label'):
            self.status_label.setText("❌ Error refrescando faltantes")

    def refresh_library_categories(self, run_identification=False):
        """Solicitar refresco asíncrono de bibliotecas."""
        self.start_library_refresh(run_identification=run_identification, force=True)

    def populate_single_file_library_table(self, items):
        self.schedule_library_render(items)

    def apply_library_filter(self):
        selected_categories = self.get_selected_single_file_categories()
        items = self.view_state.libraries.filter_by_values(selected_categories)
        self.populate_single_file_library_table(items)

        if hasattr(self, 'library_filter_label'):
            if len(selected_categories) == 1:
                selected_label = Settings.get_category_label(selected_categories[0])
                self.library_filter_label.setText(f"Mostrando solo: {selected_label}")
            else:
                self.library_filter_label.setText("Mostrando todas las bibliotecas de archivo único")

    def schedule_series_render(self, items):
        signature = self._build_render_signature(items)
        if self._should_skip_render("series", items, signature):
            self.schedule_visible_cover_refresh("series")
            return
        self.render_requested_signature["series"] = signature
        self.series_renderer.render(items)

    def schedule_library_render(self, items):
        signature = self._build_render_signature(items)
        if self._should_skip_render("library", items, signature):
            self.schedule_visible_cover_refresh("library")
            return
        self.render_requested_signature["library"] = signature
        self.library_renderer.render(items)

    def schedule_missing_render(self, items):
        signature = self._build_render_signature(items)
        if self._should_skip_render("missing", items, signature):
            return
        self.render_requested_signature["missing"] = signature
        self.missing_renderer.render(items)

    def populate_missing_row(self, row, episode):
        table = self.missing_table
        table.setItem(row, 0, QTableWidgetItem(episode.get('series_name', '')))
        table.setItem(row, 1, QTableWidgetItem(f"T{episode.get('season', 0):02d}"))
        table.setItem(row, 2, QTableWidgetItem(f"E{episode.get('episode', 0):02d}"))
        table.setItem(row, 3, QTableWidgetItem(episode.get('name', '')))
        table.setItem(row, 4, QTableWidgetItem(episode.get('air_date', '')))
        table.setItem(row, 5, QTableWidgetItem(episode.get('status', 'Faltante')))

    def _on_series_render_scheduled(self, items, token):
        self.reset_row_index_cache("series")
        self._start_render_perf("series", token, items)

    def _on_library_render_scheduled(self, items, token):
        self.reset_row_index_cache("library")
        self._start_render_perf("library", token, items)

    def _on_missing_render_scheduled(self, items, token):
        self._start_render_perf("missing", token, items)

    def _on_series_render_chunk_start(self, token, items, start):
        self._start_render_chunk_perf("series", token, start)

    def _on_library_render_chunk_start(self, token, items, start):
        self._start_render_chunk_perf("library", token, start)

    def _on_missing_render_chunk_start(self, token, items, start):
        self._start_render_chunk_perf("missing", token, start)

    def _on_library_render_chunk_finished(self, token, items, start, end):
        self._finish_render_chunk_perf("library", token, items, start, end)
        self.schedule_visible_cover_refresh("library")

    def _on_series_render_chunk_finished(self, token, items, start, end):
        self._finish_render_chunk_perf("series", token, items, start, end)
        self.schedule_visible_cover_refresh("series")

    def _on_missing_render_chunk_finished(self, token, items, start, end):
        self._finish_render_chunk_perf("missing", token, items, start, end)

    def schedule_visible_cover_refresh(self, table_kind, delay_ms=80):
        self.cover_manager.schedule_visible_cover_refresh(table_kind, delay_ms=delay_ms)

    def _update_missing_total_label(self, total_count):
        if not hasattr(self, 'total_missing_label'):
            return
        if total_count == 0:
            self.total_missing_label.setText("Total: 0 episodios")
            self.total_missing_label.setStyleSheet("font-weight: bold; color: #4caf50;")
        elif total_count == 1:
            self.total_missing_label.setText("Total: 1 episodio")
            self.total_missing_label.setStyleSheet("font-weight: bold; color: #d32f2f;")
        else:
            self.total_missing_label.setText(f"Total: {total_count} episodios")
            self.total_missing_label.setStyleSheet("font-weight: bold; color: #d32f2f;")
    
    @pyqtSlot()
    def on_settings_saved(self):
        """Configuración guardada"""
        QMessageBox.information(self, "Éxito", "Configuración guardada correctamente")
        self.request_library_refresh(run_identification=False)
        
        # Reiniciar scheduler si está corriendo
        if self.scheduler_thread and self.scheduler_thread.isRunning():
            if self._stop_thread(self.scheduler_thread, "scheduler"):
                self.scheduler_thread = None
                self.start_scheduler()
            else:
                QMessageBox.warning(
                    self,
                    "Scheduler ocupado",
                    "No se pudo reiniciar el scheduler porque sigue cerrándose. Inténtalo de nuevo en unos segundos."
                )
    
    def closeEvent(self, event):
        """Cerrar aplicación"""
        self.is_closing = True
        if self.scheduler_thread and self.scheduler_thread.isRunning():
            reply = QMessageBox.question(
                self,
                "Confirmar Salida",
                "El scheduler está ejecutándose. ¿Deseas salir?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self.logger.info("Cerrando aplicación - deteniendo scheduler...")
                scheduler_ok = self._stop_thread(self.scheduler_thread, "scheduler", timeout_ms=10000)
                tmdb_ok = self._stop_thread(self.tmdb_recalc_thread, "tmdb_recalc", timeout_ms=5000)
                task_results = self._stop_background_operations()
                pending_loaders = self._stop_cover_loaders()
                pool_ok = self.worker_pool.waitForDone(5000)

                if (
                    scheduler_ok and tmdb_ok
                    and all(task_results.values())
                    and pool_ok and not pending_loaders
                ):
                    self.logger.info("Hilos detenidos - cerrando aplicación")
                    self.scheduler_thread = None
                    event.accept()
                else:
                    self.logger.warning("Cancelando cierre porque aun hay hilos activos")
                    QMessageBox.warning(
                        self,
                        "Cierre cancelado",
                        "Todavía hay tareas en segundo plano cerrándose.\nEspera unos segundos y vuelve a intentarlo."
                    )
                    self.is_closing = False
                    event.ignore()
            else:
                self.is_closing = False
                event.ignore()
        else:
            tmdb_ok = self._stop_thread(self.tmdb_recalc_thread, "tmdb_recalc", timeout_ms=5000)
            task_results = self._stop_background_operations()
            pending_loaders = self._stop_cover_loaders()
            pool_ok = self.worker_pool.waitForDone(5000)
            if (
                tmdb_ok
                and all(task_results.values())
                and pool_ok and not pending_loaders
            ):
                self.logger.info("Cerrando aplicación")
                event.accept()
            else:
                QMessageBox.warning(
                    self,
                    "Cierre cancelado",
                    "Todavía hay tareas en segundo plano cerrándose.\nEspera unos segundos y vuelve a intentarlo."
                )
                self.is_closing = False
                event.ignore()
    
    def get_selected_series_data(self):
        """Obtener datos de la serie seleccionada"""
        return self.view_state.series.get_item_by_row(self.series_table.currentRow())

    def get_selected_library_data(self):
        """Obtener datos del elemento seleccionado en Bibliotecas."""
        return self.view_state.libraries.get_item_by_row(self.library_table.currentRow())

    def get_cached_item_by_id(self, series_id):
        cached_item = self.view_state.series.get_item_by_id(series_id)
        if cached_item:
            return cached_item
        return self.view_state.libraries.get_item_by_id(series_id)

    def sync_cover_url_cache(self, items):
        self.cover_manager.sync_cover_url_cache(items)

    def reset_row_index_cache(self, table_kind):
        if table_kind == "series":
            self.series_row_index = {}
        elif table_kind == "library":
            self.library_row_index = {}

    def remember_row_index(self, table_kind, series_id, row):
        if series_id is None:
            return
        if table_kind == "series":
            self.series_row_index[series_id] = row
        elif table_kind == "library":
            self.library_row_index[series_id] = row

    def get_indexed_row(self, table_kind, series_id):
        if series_id is None:
            return None
        if table_kind == "series":
            return self.series_row_index.get(series_id)
        if table_kind == "library":
            return self.library_row_index.get(series_id)
        return None
    
    def edit_series(self, item=None):
        """Abrir diálogo de edición de serie"""
        series_data = self.get_selected_series_data()
        if not series_data:
            QMessageBox.warning(self, "Advertencia", "Por favor selecciona una serie para editar")
            return
        
        # Abrir diálogo de edición con búsqueda automática siempre activada
        dialog = SeriesEditDialog(series_data, self, auto_search=True)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            changes = dialog.get_changes()
            if changes:
                self.start_manual_update(series_data, changes, is_library=False)

    def edit_library(self, item=None):
        """Abrir diálogo de edición para un elemento de Bibliotecas."""
        library_data = self.get_selected_library_data()
        if not library_data:
            QMessageBox.warning(self, "Advertencia", "Por favor selecciona un elemento para editar")
            return

        dialog = SeriesEditDialog(library_data, self, auto_search=True)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            changes = dialog.get_changes()
            if changes:
                self.start_manual_update(library_data, changes, is_library=True)
    
    def show_series_context_menu(self, position):
        """Mostrar menú contextual para series"""
        item = self.series_table.itemAt(position)
        if item is None:
            return

        if not self.series_table.selectionModel().isRowSelected(item.row()):
            self.series_table.setCurrentCell(item.row(), item.column())
        selected = self._selected_items(self.series_table, self.view_state.series)
        if len(selected) > 1:
            multi_menu = QMenu(self)
            multi_menu.addAction(self._build_monitor_action(selected, is_library=False))
            multi_menu.exec(self.series_table.mapToGlobal(position))
            return
        series_data = self.get_selected_series_data()
        if not series_data:
            return
        
        menu = QMenu(self)
        
        # Acción de editar
        edit_action = QAction("✏️ Editar Serie", self)
        edit_action.triggered.connect(self.edit_series)
        menu.addAction(edit_action)
        
        # Acción de reescanear TMDB
        rescan_action = QAction("🔄 Reescanear TMDB", self)
        rescan_action.triggered.connect(self.rescan_tmdb_series)
        menu.addAction(rescan_action)

        diagnostics_action = QAction("🩺 Ver Diagnostico", self)
        diagnostics_action.triggered.connect(self.show_selected_series_diagnostics)
        menu.addAction(diagnostics_action)
        
        menu.addSeparator()
        
        # Acción de copiar URL
        copy_url_action = QAction("📋 Copiar URL Hispashare", self)
        copy_url_action.triggered.connect(self.copy_hispashare_url)
        menu.addAction(copy_url_action)
        
        # Acción de enviar enlaces a eMule (solo si tiene URL de Hispashare)
        if series_data.get('hispashare_url'):
            send_emule_action = QAction("🔗 Enviar Enlaces a eMule", self)
            send_emule_action.triggered.connect(self.send_series_to_emule)
            menu.addAction(send_emule_action)
        
        # Acción para reactivar búsqueda (solo si está deshabilitada)
        if series_data.get('search_disabled', 0) == 1:
            menu.addSeparator()
            reactivate_action = QAction("🔍 Reactivar Búsqueda Hispashare", self)
            reactivate_action.triggered.connect(self.reactivate_series_search)
            menu.addAction(reactivate_action)
        
        menu.addSeparator()
        menu.addAction(self._build_monitor_action([series_data], is_library=False))

        # Mostrar menú
        menu.exec(self.series_table.mapToGlobal(position))

    def show_library_context_menu(self, position):
        """Mostrar menú contextual para Bibliotecas."""
        item = self.library_table.itemAt(position)
        if item is None:
            return

        if not self.library_table.selectionModel().isRowSelected(item.row()):
            self.library_table.setCurrentCell(item.row(), item.column())
        selected = self._selected_items(self.library_table, self.view_state.libraries)
        if len(selected) > 1:
            multi_menu = QMenu(self)
            multi_menu.addAction(self._build_monitor_action(selected, is_library=True))
            multi_menu.exec(self.library_table.mapToGlobal(position))
            return
        library_data = self.get_selected_library_data()
        if not library_data:
            return

        menu = QMenu(self)

        edit_action = QAction("✏️ Editar Biblioteca", self)
        edit_action.triggered.connect(self.edit_library)
        menu.addAction(edit_action)

        rescan_action = QAction("🔄 Reescanear TMDB", self)
        rescan_action.triggered.connect(self.rescan_tmdb_library)
        menu.addAction(rescan_action)

        diagnostics_action = QAction("🩺 Ver Diagnostico", self)
        diagnostics_action.triggered.connect(self.show_selected_library_diagnostics)
        menu.addAction(diagnostics_action)

        menu.addSeparator()

        copy_url_action = QAction("📋 Copiar URL Hispashare", self)
        copy_url_action.triggered.connect(self.copy_library_hispashare_url)
        menu.addAction(copy_url_action)

        if library_data.get('search_disabled', 0) == 1:
            menu.addSeparator()
            reactivate_action = QAction("🔍 Reactivar Búsqueda Hispashare", self)
            reactivate_action.triggered.connect(self.reactivate_library_search)
            menu.addAction(reactivate_action)

        menu.addSeparator()
        menu.addAction(self._build_monitor_action([library_data], is_library=True))

        menu.exec(self.library_table.mapToGlobal(position))

    def _selected_items(self, table, state):
        """Elementos de todas las filas seleccionadas de una tabla."""
        rows = sorted({index.row() for index in table.selectionModel().selectedRows()})
        items = [state.get_item_by_row(row) for row in rows]
        return [item for item in items if item]

    def _build_monitor_action(self, items, *, is_library):
        """Acción de (des)monitorizar sobre uno o varios elementos."""
        make_monitored = not all(item.get('monitored', 1) != 0 for item in items)
        count = len(items)
        suffix = f" ({count})" if count > 1 else ""
        if make_monitored:
            action = QAction(f"👁 Volver a monitorizar{suffix}", self)
        else:
            action = QAction(f"🚫 Dejar de monitorizar{suffix}", self)
        targets = [item for item in items if (item.get('monitored', 1) != 0) != make_monitored]
        action.triggered.connect(
            lambda: self.action_coordinator.start_set_monitored(
                targets, make_monitored, is_library=is_library
            )
        )
        return action

    def rescan_tmdb_series(self):
        """Reescanear serie en TMDB"""
        series_data = self.get_selected_series_data()
        if not series_data:
            return
        
        reply = QMessageBox.question(
            self,
            "Confirmar reescaneo",
            f"¿Deseas reescanear '{series_data['name']}' en TMDB?\nEsto sobrescribirá la vinculación actual.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.start_tmdb_rescan(series_data, is_library=False)

    def show_selected_series_diagnostics(self):
        series_data = self.get_selected_series_data()
        if not series_data:
            QMessageBox.information(self, "Diagnostico", "Selecciona una serie primero.")
            return
        self.show_item_diagnostics(series_data, is_library=False)

    def rescan_tmdb_library(self):
        """Reescanear un elemento de Bibliotecas en TMDB."""
        library_data = self.get_selected_library_data()
        if not library_data:
            return

        reply = QMessageBox.question(
            self,
            "Confirmar reescaneo",
            f"¿Deseas reescanear '{library_data['name']}' en TMDB?\nEsto sobrescribirá la vinculación actual.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self.start_tmdb_rescan(library_data, is_library=True)

    def show_selected_library_diagnostics(self):
        library_data = self.get_selected_library_data()
        if not library_data:
            QMessageBox.information(self, "Diagnostico", "Selecciona un elemento primero.")
            return
        self.show_item_diagnostics(library_data, is_library=True)

    def show_item_diagnostics(self, item_data, *, is_library=False):
        series_id = (item_data or {}).get("id")
        if not series_id:
            QMessageBox.information(self, "Diagnostico", "El elemento seleccionado no tiene ID valido.")
            return

        category_label = "Biblioteca" if is_library else "Serie"
        item_name = item_data.get("tmdb_name") or item_data.get("name") or category_label

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Diagnostico: {item_name}")
        dialog.resize(860, 640)

        layout = QVBoxLayout(dialog)
        info_label = QLabel(f"<b>{category_label}:</b> {item_name}")
        layout.addWidget(info_label)

        text_edit = QTextEdit(dialog)
        text_edit.setReadOnly(True)
        text_edit.setPlainText("Cargando diagnóstico...")
        layout.addWidget(text_edit)

        button_row = QHBoxLayout()
        close_button = QPushButton("Cerrar", dialog)
        close_button.clicked.connect(dialog.accept)
        button_row.addStretch()
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        # Cargar el texto en un hilo secundario para no bloquear el hilo UI
        dialog_alive = [True]
        dialog.finished.connect(lambda _: dialog_alive.__setitem__(0, False))

        def _fetch_diagnostics():
            text = self.diagnostics_service.build_item_diagnostics_text(series_id, limit=60)
            if dialog_alive[0]:
                QTimer.singleShot(0, lambda: text_edit.setPlainText(text) if dialog_alive[0] else None)

        threading.Thread(target=_fetch_diagnostics, daemon=True).start()

        dialog.exec()
    
    def copy_hispashare_url(self):
        """Copiar URL de Hispashare al portapapeles"""
        series_data = self.get_selected_series_data()
        if not series_data:
            return
        
        url = series_data.get('hispashare_url', '')
        if url:
            from PyQt6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            clipboard.setText(url)
            self.append_log(f"📋 URL copiada al portapapeles: {url}")
        else:
            QMessageBox.information(self, "Información", "Esta serie no tiene URL de Hispashare")

    def copy_library_hispashare_url(self):
        """Copiar URL de Hispashare del elemento seleccionado en Bibliotecas."""
        library_data = self.get_selected_library_data()
        if not library_data:
            return

        url = library_data.get('hispashare_url', '')
        if url:
            from PyQt6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            clipboard.setText(url)
            self.append_log(f"📋 URL copiada al portapapeles: {url}")
        else:
            QMessageBox.information(self, "Información", "Este elemento no tiene URL de Hispashare")
    
    def reactivate_series_search(self):
        """Reactivar búsqueda de Hispashare para una serie"""
        series_data = self.get_selected_series_data()
        if not series_data:
            return
        
        reply = QMessageBox.question(
            self,
            "Reactivar Búsqueda",
            f"¿Deseas reactivar la búsqueda de Hispashare para '{series_data['name']}'?\n\nEsto permitirá que la serie sea buscada nuevamente en los próximos escaneos automáticos.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.start_search_reactivation(series_data, is_library=False)

    def reactivate_library_search(self):
        """Reactivar búsqueda de Hispashare para un elemento de Bibliotecas."""
        library_data = self.get_selected_library_data()
        if not library_data:
            return

        reply = QMessageBox.question(
            self,
            "Reactivar Búsqueda",
            f"¿Deseas reactivar la búsqueda de Hispashare para '{library_data['name']}'?\n\nEsto permitirá que el elemento sea buscado nuevamente en los próximos escaneos automáticos.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self.start_search_reactivation(library_data, is_library=True)
    
    @pyqtSlot(int, str)
    def on_url_saved(self, series_id, url):
        """Actualizar tabla cuando se guarde una URL"""
        self.logger.info(f"Señal recibida: URL guardada para serie ID {series_id}: {url}")
        series_data = self.get_cached_item_by_id(series_id) or {'id': series_id}
        series_data['hispashare_url'] = url
        cached_cover_url = self.cover_manager.cover_url_cache.get(series_id)
        if cached_cover_url:
            series_data['hispashare_cover_url'] = cached_cover_url
        self.view_state.series.update_item(series_id, series_data)
        self.view_state.libraries.update_item(series_id, series_data)
        if not series_data.get('hispashare_cover_url'):
            self.start_cover_url_resolve(series_id, url)
        if series_data and Settings.get_category_content_mode(series_data.get('content_category', 'series')) == 'single_file':
            self.update_single_file_item_cache(series_id, series_data)
            return
        self.update_series_url_in_table(series_id, url)
    
    def update_series_url_in_table(self, series_id, url):
        """Actualizar URL de una serie específica en la tabla"""
        try:
            self.logger.info(f"Actualizando tabla para serie ID {series_id}")
            # Buscar la fila correspondiente al series_id usando los datos almacenados en la tabla
            for row in range(self.series_table.rowCount()):
                # Obtener el ID de la serie desde la tabla (almacenado como userData en la primera columna)
                name_item = self.series_table.item(row, 0)
                if name_item and hasattr(name_item, 'series_id') and name_item.series_id == series_id:
                    # Obtener el item existente o crear uno nuevo
                    url_item = self.series_table.item(row, 6)
                    if url_item is None:
                        url_item = QTableWidgetItem()
                        self.series_table.setItem(row, 6, url_item)
                    
                    # Guardar el color original antes de aplicar el resaltado
                    original_color = url_item.background()
                    
                    # Actualizar el texto de la URL
                    url_item.setText(url)

                    # Añadir efecto visual temporal
                    url_item.setBackground(QColor(144, 238, 144))  # Verde claro
                    
                    self.logger.info(f"URL actualizada en fila {row} para serie ID {series_id}")
                    
                    # Programar para quitar el efecto después de 3 segundos, restaurando el color original
                    QTimer.singleShot(3000, lambda r=row, c=6, orig=original_color: self.restore_original_color(r, c, orig))
                    return
            
            self.logger.warning(f"No se encontró la fila para serie ID {series_id}")
        except Exception as e:
            self.logger.error(f"Error actualizando URL en tabla: {e}")
    
    def get_series_data_by_row(self, row):
        """Obtener datos de serie por fila de la tabla"""
        try:
            series_list = self.current_series_list or self.all_series_list
            if row < len(series_list):
                return series_list[row]
        except Exception as e:
            self.logger.error(f"Error obteniendo datos de serie por fila: {e}")
        return None
    
    def restore_original_color(self, row, col, original_color):
        """Restaurar el color original de una celda"""
        try:
            if row < self.series_table.rowCount():
                item = self.series_table.item(row, col)
                if item:
                    item.setBackground(original_color)
        except Exception as e:
            self.logger.error(f"Error restaurando color original: {e}")
    
    def remove_highlight(self, row, col):
        """Quitar resaltado de una celda"""
        try:
            if row < self.series_table.rowCount():
                item = self.series_table.item(row, col)
                if item:
                    # Usar el color por defecto del sistema en lugar de negro
                    from PyQt6.QtGui import QPalette
                    default_color = self.palette().color(QPalette.ColorRole.Base)
                    item.setBackground(default_color)
        except Exception as e:
            self.logger.error(f"Error quitando resaltado: {e}")
    
    def refresh_downloads(self):
        """Refrescar la lista de enlaces ed2k desde archivos en segundo plano."""
        self.refresh_coordinator.refresh_downloads()

    @pyqtSlot(dict)
    def on_downloads_refresh_finished(self, payload):
        text = payload.get("text", "")
        count = payload.get("count", 0)
        self.downloads_text.setPlainText(text)
        if count:
            self.append_log(f"✅ {count} archivos de descarga cargados")
        self._set_downloads_busy(False)
        if hasattr(self, 'status_label'):
            self.status_label.setText("📥 Descargas actualizadas")

    @pyqtSlot(str)
    def on_downloads_refresh_error(self, message):
        self._set_downloads_busy(False)
        self.logger.error(f"Error refrescando descargas: {message}")
        self.append_log(f"❌ Error cargando descargas: {message}")
        if hasattr(self, 'status_label'):
            self.status_label.setText("❌ Error cargando descargas")
        QMessageBox.warning(self, "Error", f"Error al cargar descargas: {message}")
    
    def clear_downloads(self):
        """Limpiar el área de descargas"""
        reply = QMessageBox.question(
            self,
            "Confirmar limpieza",
            "¿Deseas limpiar todos los enlaces mostrados?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.downloads_text.clear()
            self.append_log("🗑️ Enlaces de descarga limpiados")
    
    def copy_all_links(self):
        """Copiar todos los enlaces al portapapeles"""
        try:
            from PyQt6.QtWidgets import QApplication
            
            text = self.downloads_text.toPlainText()
            if text.strip():
                clipboard = QApplication.clipboard()
                clipboard.setText(text)
                self.append_log("📋 Enlaces copiados al portapapeles")
                QMessageBox.information(self, "Copiado", "Enlaces copiados al portapapeles")
            else:
                QMessageBox.information(self, "Sin enlaces", "No hay enlaces para copiar")
                
        except Exception as e:
            self.logger.error(f"Error copiando enlaces: {e}")
            QMessageBox.warning(self, "Error", f"Error al copiar: {str(e)}")
    
    def send_all_to_emule(self):
        """Enviar todos los enlaces mostrados a eMule en segundo plano."""
        text = self.downloads_text.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Sin enlaces", "No hay enlaces para enviar")
            return

        ed2k_links = re.findall(r'ed2k://\|file\|[^|]+\|\d+\|[A-Fa-f0-9]{32}\|(?:/)?', text)
        if not ed2k_links:
            QMessageBox.information(self, "Sin enlaces válidos", "No se encontraron enlaces ed2k válidos")
            return

        reply = QMessageBox.question(
            self,
            "Confirmar envío",
            f"¿Deseas enviar {len(ed2k_links)} enlaces a eMule?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.start_emule_send(text=text)
    
    def send_series_to_emule(self):
        """Enviar enlaces de una serie específica a eMule en segundo plano."""
        series_data = self.get_selected_series_data()
        if not series_data:
            return

        self.start_emule_send(series_id=series_data['id'], series_name=series_data['name'])

    def start_emule_send(self, *, text=None, series_id=None, series_name=None):
        self.action_coordinator.start_emule_send(
            text=text,
            series_id=series_id,
            series_name=series_name,
        )

    @pyqtSlot(dict)
    def on_emule_send_finished(self, payload):
        context = self.emule_send_context or {}
        mode = payload.get("mode")
        added = payload.get("added", 0)
        total = payload.get("total", 0)
        message = payload.get("message") or payload.get("error") or "Error desconocido"

        if payload.get("success"):
            if mode == "series":
                series_name = context.get("series_name") or "la serie seleccionada"
                self.append_log(f"🔗 Enlaces de '{series_name}' enviados a eMule: {added}/{total}")
                self.show_information("Éxito", f"✅ {added}/{total} enlaces de '{series_name}' enviados a eMule")
            else:
                self.append_log(f"🔗 {added}/{total} enlaces enviados a eMule")
                self.show_information("Éxito", f"✅ {added}/{total} enlaces enviados a eMule")
            if hasattr(self, 'status_label'):
                self.status_label.setText("🔗 Envío a eMule completado")
            return

        if mode == "series":
            series_name = context.get("series_name") or "la serie seleccionada"
            self.show_warning("Error", f"❌ Error enviando enlaces de '{series_name}':\n{message}")
        else:
            self.show_warning("Error", f"❌ Error enviando enlaces: {message}")
        self.append_log(f"❌ Error enviando enlaces a eMule: {message}")
        if hasattr(self, 'status_label'):
            self.status_label.setText("❌ Error enviando a eMule")

    @pyqtSlot(str)
    def on_emule_send_error(self, message):
        self.logger.error(f"Error enviando enlaces a eMule: {message}")
        self.append_log(f"❌ Error enviando enlaces a eMule: {message}")
        if hasattr(self, 'status_label'):
            self.status_label.setText("❌ Error enviando a eMule")
        self.show_error("Error", f"Error enviando enlaces a eMule: {message}")

    def start_tmdb_rescan(self, item_data, *, is_library=False):
        self.action_coordinator.start_tmdb_rescan(item_data, is_library=is_library)

    @pyqtSlot(dict)
    def on_tmdb_rescan_finished(self, payload):
        context = self.tmdb_rescan_context or {}
        item_name = context.get("name") or "Elemento"
        is_library = payload.get("is_library", False)
        updated_item = payload.get("updated_item") or {}
        match_result = payload.get("match_result") or {}
        series_id = payload.get("series_id")
        recheck_hispashare_url = bool(payload.get("recheck_hispashare_url"))
        reset_hispashare_url = bool(payload.get("reset_hispashare_url"))
        should_run_immediate_hispashare_search = (
            payload.get("matched")
            and bool(match_result.get("tmdb_id"))
            and (
                recheck_hispashare_url
                or not (updated_item.get("hispashare_url") or "").strip()
            )
        )

        if payload.get("matched") and match_result:
            self.append_log(
                f"✅ TMDB actualizado: {match_result['name']} ({match_result.get('year', '')}) - "
                f"Confianza: {match_result['confidence_score']:.1%}"
            )
            if is_library:
                self.update_single_file_item_cache(series_id, updated_item)
            else:
                self.refresh_series_row(series_id, updated_item)
                if payload.get("recalculated_missing"):
                    missing_count = payload.get("missing_count", 0)
                    if self.is_missing_tab_active():
                        self.refresh_missing_list(force=True)
                    else:
                        self.missing_list_dirty = True
                    self.append_log(f"   📊 Episodios faltantes recalculados: {missing_count}")

            if reset_hispashare_url:
                self.append_log("   🔄 URL Hispashare anterior invalidada por cambio de TMDB")
            elif recheck_hispashare_url:
                self.append_log("   🔁 URL Hispashare marcada para revalidación con el nuevo TMDB")
            self.show_information(
                "TMDB",
                f"TMDB actualizado.\n\n{match_result['name']} ({match_result.get('year', '')})\n"
                f"Confianza: {match_result['confidence_score']:.1%}"
            )
            if hasattr(self, 'status_label'):
                self.status_label.setText("🎬 TMDB actualizado")
            if should_run_immediate_hispashare_search:
                self.start_immediate_hispashare_search(
                    updated_item,
                    is_library=is_library,
                    force_recheck=recheck_hispashare_url,
                )
            return

        self.append_log(f"❌ No se encontró match TMDB para '{item_name}'")
        if hasattr(self, 'status_label'):
            self.status_label.setText("❌ Sin match TMDB")
        self.show_information("TMDB", "No se encontró un match adecuado en TMDB")

    @pyqtSlot(str)
    def on_tmdb_rescan_error(self, message):
        self.logger.error(f"Error reescaneando TMDB: {message}")
        self.append_log(f"❌ Error reescaneando TMDB: {message}")
        if hasattr(self, 'status_label'):
            self.status_label.setText("❌ Error reescaneando TMDB")
        self.show_error("Error", f"Error reescaneando TMDB:\n{message}")
    
    def auto_match_new_series(self):
        """Vincular automáticamente series sin TMDB ID en segundo plano."""
        self.action_coordinator.auto_match_new_series()

    @pyqtSlot(dict)
    def on_auto_match_finished(self, payload):
        total = payload.get("total", 0)
        matched = payload.get("matched", 0)
        if total == 0:
            self.append_log("ℹ️ No hay series pendientes de auto-match TMDB")
        else:
            self.append_log(f"✅ {matched}/{total} series vinculadas con TMDB")
            self.refresh_series_list()
        if hasattr(self, 'status_label'):
            self.status_label.setText("🎬 Auto-match TMDB completado")

    @pyqtSlot(str)
    def on_auto_match_error(self, message):
        self.logger.error(f"Error en auto_match_new_series: {message}")
        self.append_log(f"❌ Error vinculando con TMDB: {message}")
        if hasattr(self, 'status_label'):
            self.status_label.setText("❌ Error en auto-match TMDB")

    @pyqtSlot(dict)
    def on_search_reactivate_finished(self, payload):
        updated_item = payload.get("updated_item") or {}
        is_library = payload.get("is_library", False)
        series_id = payload.get("series_id")
        item_name = updated_item.get("name", "Elemento")

        if is_library:
            self.update_single_file_item_cache(series_id, updated_item)
        else:
            self.refresh_series_row(series_id, updated_item)

        self.append_log(f"🔍 Búsqueda reactivada para '{item_name}'")
        if hasattr(self, "status_label"):
            self.status_label.setText("🔍 Búsqueda reactivada")
        self.show_information(
            "Búsqueda Reactivada",
            f"La búsqueda de Hispashare ha sido reactivada para '{item_name}'.\n\n"
            "El elemento será incluido en los próximos escaneos automáticos."
        )

    @pyqtSlot(dict)
    def on_set_monitored_finished(self, payload):
        updated_items = payload.get("updated_items") or []
        for updated_item in updated_items:
            series_id = updated_item.get("id")
            if payload.get("is_library"):
                self.update_single_file_item_cache(series_id, updated_item)
            else:
                self.refresh_series_row(series_id, updated_item)
        state = "monitorizadas" if payload.get("monitored") else "NO monitorizadas"
        self.append_log(f"👁 {len(updated_items)} elemento(s) ahora {state}")

    @pyqtSlot(str)
    def on_set_monitored_error(self, message):
        self.logger.error(f"Error cambiando monitorización: {message}")
        self.append_log(f"❌ Error cambiando monitorización: {message}")
        self.show_error("Error", f"Error al cambiar la monitorización:\n{message}")

    @pyqtSlot(str)
    def on_search_reactivate_error(self, message):
        self.logger.error(f"Error reactivando búsqueda: {message}")
        self.append_log(f"❌ Error reactivando búsqueda: {message}")
        if hasattr(self, "status_label"):
            self.status_label.setText("❌ Error reactivando búsqueda")
        self.show_error("Error", f"Error al reactivar la búsqueda:\n{message}")

    @pyqtSlot(dict)
    def on_immediate_hispashare_search_finished(self, payload):
        series_id = payload.get("series_id")
        updated_item = payload.get("updated_item") or {}
        is_library = payload.get("is_library", False)
        found = bool(payload.get("found"))
        skipped = bool(payload.get("skipped"))
        url = (payload.get("url") or "").strip()
        matched_title = (payload.get("matched_title") or "").strip()
        kept_existing = bool(payload.get("kept_existing"))
        validated_existing = bool(payload.get("validated_existing"))
        replaced_existing = bool(payload.get("replaced_existing"))
        item_name = updated_item.get("tmdb_name") or updated_item.get("name", "Elemento")
        if not series_id or not updated_item:
            return

        self.sync_cover_url_cache([updated_item])

        if found and url:
            self.on_url_saved(series_id, url)
            if replaced_existing:
                self.append_log(f"✅ URL de Hispashare reemplazada para '{item_name}'")
            elif validated_existing:
                self.append_log(f"✅ URL de Hispashare confirmada para '{item_name}'")
            else:
                self.append_log(f"✅ URL de Hispashare encontrada al instante para '{item_name}'")
            if matched_title:
                self.append_log(f"   🎯 Mejor resultado Hispashare: {matched_title}")
            if hasattr(self, "status_label"):
                if replaced_existing:
                    self.status_label.setText("✅ URL de Hispashare actualizada")
                elif validated_existing:
                    self.status_label.setText("✅ URL de Hispashare confirmada")
                else:
                    self.status_label.setText("✅ URL de Hispashare encontrada")
            return

        if is_library:
            self.update_single_file_item_cache(series_id, updated_item)
        else:
            self.refresh_series_row(series_id, updated_item)

        if skipped:
            self.append_log(f"ℹ️ '{item_name}' ya tenía URL de Hispashare")
            if hasattr(self, "status_label"):
                self.status_label.setText("ℹ️ La URL ya existía")
            return

        if kept_existing:
            self.append_log(f"ℹ️ Se mantiene la URL actual de Hispashare para '{item_name}'")
            if matched_title:
                self.append_log(f"   🎯 Mejor resultado Hispashare: {matched_title}")
            if hasattr(self, "status_label"):
                self.status_label.setText("ℹ️ Se mantiene la URL existente")
            return

        self.append_log(f"ℹ️ No se encontró URL inmediata de Hispashare para '{item_name}'")
        if hasattr(self, "status_label"):
            self.status_label.setText("ℹ️ Búsqueda inmediata sin resultado")

    @pyqtSlot(str)
    def on_immediate_hispashare_search_error(self, message):
        self.logger.error(f"Error en búsqueda inmediata de Hispashare: {message}")
        self.append_log(f"❌ Error en búsqueda inmediata de Hispashare: {message}")
        if hasattr(self, "status_label"):
            self.status_label.setText("❌ Error buscando URL inmediata")

    @pyqtSlot()
    def on_immediate_hispashare_search_done(self):
        self.action_coordinator.on_immediate_hispashare_search_done()

    @pyqtSlot(dict)
    def on_cover_url_resolve_finished(self, payload):
        series_id = payload.get("series_id")
        updated_item = payload.get("updated_item") or {}
        if not series_id or not updated_item:
            return
        self.sync_cover_url_cache([updated_item])

        if Settings.get_category_content_mode(updated_item.get("content_category", "series")) == "single_file":
            self.update_single_file_item_cache(series_id, updated_item)
            if self.is_library_tab_active():
                self.schedule_visible_cover_refresh("library", delay_ms=0)
        else:
            self.refresh_series_row(series_id, updated_item)
            if self.is_series_tab_active():
                self.schedule_visible_cover_refresh("series", delay_ms=0)

    @pyqtSlot(str)
    def on_cover_url_resolve_error(self, message):
        self.logger.debug(f"Error resolviendo cover_url en segundo plano: {message}")
    
    def on_tmdb_matched(self, series_id, match_data):
        """Manejar cuando una serie es vinculada con TMDB en tiempo real"""
        try:
            series_data = self.get_cached_item_by_id(series_id)
            if series_data and Settings.get_category_content_mode(series_data.get('content_category', 'series')) == 'single_file':
                updated_item = dict(series_data)
                updated_item.update(match_data or {})
                self.update_single_file_item_cache(series_id, updated_item)
                return
            if series_data:
                updated_item = dict(series_data)
                updated_item.update(match_data or {})
                self.refresh_series_row(series_id, updated_item)
            else:
                self.refresh_series_row(series_id)
        except Exception as e:
            self.logger.error(f"Error en on_tmdb_matched: {e}")
    
    def on_series_updated(self, series_id):
        """Manejar cuando una serie es actualizada en tiempo real"""
        try:
            series_data = self.db.get_series_by_id(series_id) or self.get_cached_item_by_id(series_id)
            if series_data and Settings.get_category_content_mode(series_data.get('content_category', 'series')) == 'single_file':
                self.update_single_file_item_cache(series_id, series_data)
                return
            if series_data:
                self.refresh_series_row(series_id, series_data)
            else:
                self.refresh_series_row(series_id)
        except Exception as e:
            self.logger.error(f"Error en on_series_updated: {e}")

    def update_single_file_item_cache(self, series_id, item_data=None):
        self.view_updater.update_single_file_item_cache(series_id, item_data)
    
    def update_series_tmdb_in_table(self, series_id, match_data):
        """Actualizar datos TMDB de una serie en la tabla en tiempo real"""
        self.view_updater.update_series_tmdb_in_table(series_id, match_data)
    
    def refresh_series_row(self, series_id, series_data=None):
        """Refrescar una fila específica de la tabla de series"""
        self.view_updater.refresh_series_row(series_id, series_data)

    def refresh_library_row(self, series_id, item_data=None):
        """Refrescar una fila específica de la tabla de bibliotecas."""
        self.view_updater.refresh_library_row(series_id, item_data)
    
    def populate_series_row(self, row, series_data):
        """Llenar una fila específica de la tabla con datos de serie"""
        try:
            table = self.series_table
            self.remember_row_index("series", series_data.get("id"), row)
            row_items = build_series_row_items(series_data)
            for column, item in enumerate(row_items):
                if column == 7:
                    continue
                table.setItem(row, column, item)
            self.cover_manager.set_cover_placeholder(table, row, "series")
        except Exception as e:
            self.logger.error(f"Error llenando fila de serie: {e}")

    def populate_single_file_library_row(self, row, item):
        """Llenar una fila específica de la tabla de bibliotecas."""
        try:
            table = self.library_table
            self.remember_row_index("library", item.get("id"), row)
            row_items = build_library_row_items(item)
            for column, table_item in enumerate(row_items):
                if column == 7:
                    continue
                table.setItem(row, column, table_item)
            self.cover_manager.set_cover_placeholder(table, row, "library")
        except Exception as e:
            self.logger.error(f"Error llenando fila de biblioteca: {e}")

    def on_cover_loaded(self, series_id, image_bytes):
        # Slot real de Qt: ImageLoaderTask corre en worker_pool y solo Qt
        # garantiza entrega en el hilo de UI porque MainWindow es un QObject.
        # No mover esta conexión a un método de CoverManager (no es QObject).
        self.cover_manager.handle_cover_loaded(series_id, image_bytes)

    def on_cover_failed(self, series_id):
        self.cover_manager.handle_cover_failed(series_id)

    def on_cover_loader_finished(self, series_id):
        self.cover_manager.handle_cover_loader_finished(series_id)

    def restore_row_background(self, row, columns, original_bg):
        """Restaurar el color de fondo original de ciertas columnas"""
        try:
            table = self.series_table
            for col in columns:
                if col < table.columnCount():
                    item = table.item(row, col)
                    if item:
                        item.setBackground(original_bg)
        except Exception as e:
            self.logger.error(f"Error restaurando color de fondo: {e}")
