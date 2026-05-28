from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableWidget, QLabel, QStatusBar,
    QTabWidget, QTextEdit, QProgressBar, QTableWidgetItem,
    QMessageBox, QHeaderView, QMenu, QDialog
)
from PyQt6.QtCore import Qt, pyqtSlot, QTimer
from PyQt6.QtGui import QColor, QAction, QPixmap

from .widgets.settings_dialog import SettingsWidget
from .widgets.series_edit_dialog import SeriesEditDialog
from .widgets.image_loader import ImageLoaderThread
from core.scheduler import SchedulerThread
from core.database.db_manager import DatabaseManager
from core.tmdb.api_client import TMDBClient
from core.tmdb.auto_matcher import TMDBAutoMatcher
from core.tmdb.recalc_thread import TMDBRecalcThread
from config.settings import Settings
import logging

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()
        self.logger = logging.getLogger(__name__)
        self.scheduler_thread = None
        self.tmdb_recalc_thread = None
        self.cover_loaders = {}
        self.cover_labels = {}
        self.cover_row_height = 84
        self.cover_thumb_height = 80
        self.cover_thumb_width = 60
        
        self.setWindowTitle("Series Manager - Hispashare + TMDB")
        self.setMinimumSize(1200, 800)
        
        # Verificar configuración
        errors = Settings.validate()
        if errors:
            QMessageBox.warning(
                self,
                "Configuración Incompleta",
                "Por favor completa la configuración:\n" + "\n".join(errors)
            )
        
        self.init_ui()
        
        # Iniciar scheduler si la configuración es válida
        if not errors:
            self.start_scheduler()
    
    def init_ui(self):
        """Inicializar interfaz de usuario"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # Tabs
        tabs = QTabWidget()
        tabs.addTab(self.create_series_tab(), "📺 Series")
        tabs.addTab(self.create_missing_tab(), "❌ Episodios Faltantes")
        tabs.addTab(self.create_downloads_tab(), "📥 Descargas")
        tabs.addTab(self.create_log_tab(), "📋 Logs")
        tabs.addTab(self.create_settings_tab(), "⚙️ Configuración")
        
        layout.addWidget(tabs)
        
        # Barra de estado
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        # Progress bar
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(300)
        self.status_bar.addPermanentWidget(self.progress)
        
        self.status_label = QLabel("Listo")
        self.status_bar.addWidget(self.status_label)
    
    def create_series_tab(self):
        """Tab con lista de series"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Botones superiores
        btn_layout = QHBoxLayout()
        
        self.btn_scan = QPushButton("🔍 Escanear Ahora")
        self.btn_scan.clicked.connect(self.manual_scan)
        
        self.btn_refresh = QPushButton("🔄 Refrescar")
        self.btn_refresh.clicked.connect(self.refresh_series_list)

        self.btn_recalc_tmdb = QPushButton("🎬 Recalcular Confianza TMDB")
        self.btn_recalc_tmdb.clicked.connect(self.recalculate_all_tmdb_confidence)
        
        btn_layout.addWidget(self.btn_scan)
        btn_layout.addWidget(self.btn_refresh)
        btn_layout.addWidget(self.btn_recalc_tmdb)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        # Tabla de series
        self.series_table = QTableWidget()
        self.series_table.setColumnCount(8)
        self.series_table.setHorizontalHeaderLabels([
            "Nombre", "Año", "TMDB ID", "Confianza", 
            "Faltantes", "URL Hispashare", "Carátula", "Estado"
        ])
        
        # Ajustar columnas
        header = self.series_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.series_table.verticalHeader().setDefaultSectionSize(self.cover_row_height)
        
        self.series_table.setAlternatingRowColors(True)
        self.series_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        
        # Conectar eventos para edición
        self.series_table.itemDoubleClicked.connect(self.edit_series)
        self.series_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.series_table.customContextMenuRequested.connect(self.show_series_context_menu)
        
        layout.addWidget(self.series_table)
        
        # Cargar datos iniciales
        self.refresh_series_list()
        
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
        
        btn_refresh_missing = QPushButton("🔄 Refrescar")
        btn_refresh_missing.clicked.connect(self.refresh_missing_list)
        filter_layout.addWidget(btn_refresh_missing)
        
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
        
        # Cargar datos iniciales
        self.refresh_missing_list()
        
        return widget
    
    def create_downloads_tab(self):
        """Tab para gestionar descargas de episodios"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Botones superiores
        btn_layout = QHBoxLayout()
        
        btn_refresh_downloads = QPushButton("🔄 Refrescar Enlaces")
        btn_refresh_downloads.clicked.connect(self.refresh_downloads)
        
        btn_clear_downloads = QPushButton("🗑️ Limpiar Enlaces")
        btn_clear_downloads.clicked.connect(self.clear_downloads)
        
        btn_copy_all = QPushButton("📋 Copiar Todos")
        btn_copy_all.clicked.connect(self.copy_all_links)
        
        btn_send_emule = QPushButton("🔗 Enviar a eMule")
        btn_send_emule.clicked.connect(self.send_all_to_emule)
        
        btn_layout.addWidget(btn_refresh_downloads)
        btn_layout.addWidget(btn_clear_downloads)
        btn_layout.addWidget(btn_copy_all)
        btn_layout.addWidget(btn_send_emule)
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
        btn_clear.clicked.connect(self.log_text.clear)
        btn_layout.addWidget(btn_clear)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        return widget
    
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
        self.scheduler_thread.start()
        
        self.append_log("✅ Scheduler iniciado correctamente")
    
    @pyqtSlot(int, str)
    def update_progress(self, value, message):
        """Actualizar barra de progreso"""
        self.progress.setValue(value)
        self.status_label.setText(message)
    
    @pyqtSlot(str)
    def append_log(self, message):
        """Agregar mensaje al log"""
        self.log_text.append(message)
        # Auto-scroll al final
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    @pyqtSlot()
    def on_cycle_finished(self):
        """Ciclo de actualización completado"""
        self.refresh_series_list()
        self.refresh_missing_list()
        self.status_label.setText("✅ Ciclo completado")
    
    @pyqtSlot(str)
    def on_scheduler_error(self, error_msg):
        """Error en el scheduler"""
        QMessageBox.critical(self, "Error", f"Error en el scheduler:\n{error_msg}")
    
    def manual_scan(self):
        """Escaneo manual"""
        self.btn_scan.setEnabled(False)
        self.append_log("🔍 Iniciando escaneo manual...")
        
        try:
            from core.library.scanner import LibraryScanner
            from config.settings import Settings
            
            # Verificar que la ruta de series esté configurada
            if not Settings.SERIES_PATH:
                QMessageBox.warning(
                    self, 
                    "Configuración Incompleta", 
                    "Por favor configura la ruta de series en Configuración"
                )
                return
            
            # Ejecutar escaneo
            scanner = LibraryScanner(db_manager=self.db)
            series_list = scanner.scan()
            
            # Guardar en base de datos
            series_count = 0
            for series in series_list:
                self.db.insert_or_update_series(series)
                series_count += 1
            
            self.append_log(f"✅ Escaneo completado: {series_count} series procesadas")
            self.update_progress(100, "Escaneo completado")
            
            # Refrescar la lista de series
            self.refresh_series_list()
            
            # Vincular automáticamente con TMDB las series nuevas
            self.auto_match_new_series()
            
            QMessageBox.information(
                self, 
                "Escaneo Completado", 
                f"Se procesaron {series_count} series correctamente."
            )
            
        except FileNotFoundError as e:
            error_msg = f"Ruta no encontrada: {e}"
            self.append_log(f"❌ Error: {error_msg}")
            QMessageBox.critical(self, "Error", error_msg)
            
        except Exception as e:
            error_msg = f"Error durante el escaneo: {str(e)}"
            self.append_log(f"❌ Error: {error_msg}")
            self.logger.error(f"Error en escaneo manual: {e}")
            QMessageBox.critical(self, "Error", error_msg)
            
        finally:
            self.btn_scan.setEnabled(True)
    
    def refresh_series_list(self):
        """Refrescar lista de series"""
        series_list = self.db.get_all_series()
        
        self.series_table.setRowCount(len(series_list))
        
        for row, series in enumerate(series_list):
            self.series_table.setRowHeight(row, self.cover_row_height)
            # Nombre - Almacenar el ID de la serie como atributo del item
            name_item = QTableWidgetItem(series.get('name', ''))
            name_item.series_id = series.get('id')  # Almacenar ID para referencia
            self.series_table.setItem(row, 0, name_item)
            
            # Año
            self.series_table.setItem(row, 1, QTableWidgetItem(str(series.get('year', ''))))
            
            # TMDB ID
            self.series_table.setItem(row, 2, QTableWidgetItem(str(series.get('tmdb_id', ''))))
            
            # Confianza
            confidence = series.get('confidence_score', 0)
            if confidence is None:
                confidence = 0
            conf_item = QTableWidgetItem(f"{confidence:.2%}")
            if confidence >= 0.85:
                conf_item.setForeground(QColor('green'))
            elif confidence >= 0.70:
                conf_item.setForeground(QColor('orange'))
            else:
                conf_item.setForeground(QColor('red'))
            self.series_table.setItem(row, 3, conf_item)
            
            # Faltantes
            missing_count = series.get('missing_count', 0)
            self.series_table.setItem(row, 4, QTableWidgetItem(str(missing_count)))
            
            # URL Hispashare
            url = series.get('hispashare_url', '')
            url_item = QTableWidgetItem(url)
            
            # Si la serie tiene búsqueda deshabilitada, mostrar indicación visual
            if series.get('search_disabled', 0) == 1:
                if not url:  # Solo si no tiene URL
                    url_item.setText("🚫 Búsqueda deshabilitada")
                    url_item.setForeground(QColor('gray'))
            
            self.series_table.setItem(row, 5, url_item)

            series_id = series.get('id')
            cover_url = series.get('hispashare_cover_url', '')
            cover_label = QLabel()
            cover_label.setFixedSize(self.cover_thumb_width, self.cover_thumb_height)
            cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.series_table.setCellWidget(row, 6, cover_label)
            if series_id is not None:
                self.cover_labels[series_id] = cover_label
                if cover_url:
                    self.load_cover_for_series(series_id, cover_url)
            
            # Estado
            status = series.get('status', 'Pendiente')
            status_item = QTableWidgetItem(status)
            
            # Indicar visualmente si la búsqueda está deshabilitada
            if series.get('search_disabled', 0) == 1:
                status_item.setForeground(QColor('gray'))
            
            self.series_table.setItem(row, 7, status_item)
        
        # También refrescar la lista de episodios faltantes si existe
        if hasattr(self, 'missing_table'):
            self.refresh_missing_list()
    
    def refresh_missing_list(self):
        """Refrescar lista de episodios faltantes"""
        missing_episodes = self.db.get_missing_episodes()
        
        self.missing_table.setRowCount(len(missing_episodes))
        
        for row, episode in enumerate(missing_episodes):
            self.missing_table.setItem(row, 0, QTableWidgetItem(episode.get('series_name', '')))
            self.missing_table.setItem(row, 1, QTableWidgetItem(f"T{episode.get('season', 0):02d}"))
            self.missing_table.setItem(row, 2, QTableWidgetItem(f"E{episode.get('episode', 0):02d}"))
            self.missing_table.setItem(row, 3, QTableWidgetItem(episode.get('name', '')))
            self.missing_table.setItem(row, 4, QTableWidgetItem(episode.get('air_date', '')))
            self.missing_table.setItem(row, 5, QTableWidgetItem(episode.get('status', 'Faltante')))
        
        # Actualizar contador total
        total_count = len(missing_episodes)
        if hasattr(self, 'total_missing_label'):
            if total_count == 0:
                self.total_missing_label.setText("Total: 0 episodios")
                self.total_missing_label.setStyleSheet("font-weight: bold; color: #4caf50;")  # Verde para 0
            elif total_count == 1:
                self.total_missing_label.setText("Total: 1 episodio")
                self.total_missing_label.setStyleSheet("font-weight: bold; color: #d32f2f;")  # Rojo para faltantes
            else:
                self.total_missing_label.setText(f"Total: {total_count} episodios")
                self.total_missing_label.setStyleSheet("font-weight: bold; color: #d32f2f;")  # Rojo para faltantes
    
    @pyqtSlot()
    def on_settings_saved(self):
        """Configuración guardada"""
        QMessageBox.information(self, "Éxito", "Configuración guardada correctamente")
        
        # Reiniciar scheduler si está corriendo
        if self.scheduler_thread and self.scheduler_thread.isRunning():
            self.scheduler_thread.stop()
            self.scheduler_thread.wait()
            self.start_scheduler()
    
    def closeEvent(self, event):
        """Cerrar aplicación"""
        if self.scheduler_thread and self.scheduler_thread.isRunning():
            reply = QMessageBox.question(
                self,
                "Confirmar Salida",
                "El scheduler está ejecutándose. ¿Deseas salir?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self.logger.info("Cerrando aplicación - deteniendo scheduler...")
                self.scheduler_thread.stop()
                
                # Esperar con timeout para evitar bloqueos
                if not self.scheduler_thread.wait(5000):  # 5 segundos timeout
                    self.logger.warning("Timeout esperando scheduler - forzando cierre")
                    self.scheduler_thread.terminate()
                    self.scheduler_thread.wait(2000)  # Esperar 2 segundos más
                
                self.logger.info("Scheduler detenido - cerrando aplicación")
                event.accept()
            else:
                event.ignore()
        else:
            self.logger.info("Cerrando aplicación")
            event.accept()
    
    def get_selected_series_data(self):
        """Obtener datos de la serie seleccionada"""
        current_row = self.series_table.currentRow()
        if current_row < 0:
            return None
        
        # Obtener todas las series para encontrar la correcta
        series_list = self.db.get_all_series()
        if current_row >= len(series_list):
            return None
        
        return series_list[current_row]
    
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
                try:
                    # Actualizar en base de datos
                    result = self.db.update_series_manual(series_data['id'], changes)
                    
                    # Refrescar tabla
                    self.refresh_series_list()
                    
                    # Log del cambio
                    self.append_log(f"✅ Serie '{series_data['name']}' actualizada manualmente")
                    
                    # Si se recalcularon episodios faltantes, mostrar información adicional
                    if result and result.get('recalculated_missing'):
                        missing_count = result.get('missing_count', 0)
                        self.append_log(f"   📊 Episodios faltantes recalculados: {missing_count}")
                        QMessageBox.information(
                            self, 
                            "Éxito", 
                            f"Serie actualizada correctamente.\n\nSe recalcularon {missing_count} episodios faltantes."
                        )
                    else:
                        QMessageBox.information(self, "Éxito", "Serie actualizada correctamente")
                    
                except Exception as e:
                    self.logger.error(f"Error al actualizar serie: {e}")
                    QMessageBox.critical(self, "Error", f"Error al actualizar serie:\n{str(e)}")
    
    def show_series_context_menu(self, position):
        """Mostrar menú contextual para series"""
        if self.series_table.itemAt(position) is None:
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
        
        # Mostrar menú
        menu.exec(self.series_table.mapToGlobal(position))
    
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
            self.append_log(f"🔄 Reescaneando '{series_data['name']}' en TMDB...")
            
            try:
                if not Settings.TMDB_API_KEY:
                    QMessageBox.warning(self, "Configuración", "TMDB_API_KEY no configurada")
                    return
                
                local_episodes = self.db.get_local_episodes(series_data['id'])
                seasons = {}
                for ep in local_episodes:
                    season = ep.get('season')
                    if season is None:
                        continue
                    seasons[season] = seasons.get(season, 0) + 1
                
                local_series = {
                    'id': series_data['id'],
                    'name': series_data['name'],
                    'folder_path': series_data.get('folder_path', ''),
                    'seasons': seasons,
                    'episodes': local_episodes
                }
                
                tmdb_client = TMDBClient(Settings.TMDB_API_KEY)
                matcher = TMDBAutoMatcher(tmdb_client)
                
                match_result = matcher.match_series(local_series)
                if not match_result:
                    self.append_log(f"❌ No se encontró match TMDB para '{series_data['name']}'")
                    QMessageBox.information(self, "TMDB", "No se encontró un match adecuado en TMDB")
                    return
                
                self.db.update_tmdb_match(series_data['id'], match_result)
                self.append_log(
                    f"✅ TMDB actualizado: {match_result['name']} ({match_result.get('year', '')}) - "
                    f"Confianza: {match_result['confidence_score']:.1%}"
                )
                
                self.refresh_series_row(series_data['id'])
                QMessageBox.information(
                    self,
                    "TMDB",
                    f"TMDB actualizado.\n\n{match_result['name']} ({match_result.get('year', '')})\n"
                    f"Confianza: {match_result['confidence_score']:.1%}"
                )
                
            except Exception as e:
                self.logger.error(f"Error reescaneando TMDB: {e}")
                QMessageBox.critical(self, "Error", f"Error reescaneando TMDB:\n{str(e)}")
    
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
            try:
                # Reactivar búsqueda en la base de datos
                self.db.enable_series_search(series_data['id'])
                
                # Refrescar la tabla para mostrar el cambio
                self.refresh_series_list()
                
                # Log del cambio
                self.append_log(f"🔍 Búsqueda reactivada para '{series_data['name']}'")
                
                QMessageBox.information(
                    self, 
                    "Búsqueda Reactivada", 
                    f"La búsqueda de Hispashare ha sido reactivada para '{series_data['name']}'.\n\nLa serie será incluida en los próximos escaneos automáticos."
                )
                
            except Exception as e:
                self.logger.error(f"Error reactivando búsqueda para '{series_data['name']}': {e}")
                QMessageBox.critical(
                    self, 
                    "Error", 
                    f"Error al reactivar la búsqueda:\n{str(e)}"
                )
    
    @pyqtSlot(int, str)
    def on_url_saved(self, series_id, url):
        """Actualizar tabla cuando se guarde una URL"""
        self.logger.info(f"Señal recibida: URL guardada para serie ID {series_id}: {url}")
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
                    url_item = self.series_table.item(row, 5)
                    if url_item is None:
                        url_item = QTableWidgetItem()
                        self.series_table.setItem(row, 5, url_item)
                    
                    # Guardar el color original antes de aplicar el resaltado
                    original_color = url_item.background()
                    
                    # Actualizar el texto de la URL
                    url_item.setText(url)

                    cover_url = self.db.derive_hispashare_cover_url(url)
                    if cover_url:
                        try:
                            self.db.update_hispashare_cover_url(series_id, cover_url)
                        except Exception:
                            pass
                        self.load_cover_for_series(series_id, cover_url)
                    
                    # Añadir efecto visual temporal
                    url_item.setBackground(QColor(144, 238, 144))  # Verde claro
                    
                    self.logger.info(f"URL actualizada en fila {row} para serie ID {series_id}")
                    
                    # Programar para quitar el efecto después de 3 segundos, restaurando el color original
                    QTimer.singleShot(3000, lambda r=row, c=5, orig=original_color: self.restore_original_color(r, c, orig))
                    return
            
            self.logger.warning(f"No se encontró la fila para serie ID {series_id}")
        except Exception as e:
            self.logger.error(f"Error actualizando URL en tabla: {e}")
    
    def get_series_data_by_row(self, row):
        """Obtener datos de serie por fila de la tabla"""
        try:
            series_list = self.db.get_all_series()
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
        """Refrescar la lista de enlaces ed2k desde archivos"""
        try:
            import os
            downloads_dir = "downloads"
            
            if not os.path.exists(downloads_dir):
                self.downloads_text.setPlainText("No hay archivos de descarga disponibles.")
                return
            
            all_links = []
            
            # Leer todos los archivos .txt en el directorio downloads
            for filename in os.listdir(downloads_dir):
                if filename.endswith('.txt'):
                    filepath = os.path.join(downloads_dir, filename)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                            if content:
                                all_links.append(f"# {filename}\n{content}\n")
                    except Exception as e:
                        self.logger.error(f"Error leyendo {filepath}: {e}")
            
            if all_links:
                self.downloads_text.setPlainText('\n'.join(all_links))
                self.append_log(f"✅ {len(all_links)} archivos de descarga cargados")
            else:
                self.downloads_text.setPlainText("No se encontraron enlaces ed2k.")
                
        except Exception as e:
            self.logger.error(f"Error refrescando descargas: {e}")
            QMessageBox.warning(self, "Error", f"Error al cargar descargas: {str(e)}")
    
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
        """Enviar todos los enlaces mostrados a eMule"""
        try:
            from core.emule.client import EmuleClient
            
            text = self.downloads_text.toPlainText()
            if not text.strip():
                QMessageBox.information(self, "Sin enlaces", "No hay enlaces para enviar")
                return
            
            # Crear cliente eMule
            emule_client = EmuleClient()
            
            if not emule_client.is_configured():
                QMessageBox.warning(
                    self,
                    "eMule no configurado",
                    "Por favor configura eMule en la pestaña de Configuración antes de enviar enlaces"
                )
                return
            
            # Extraer enlaces ed2k del texto
            ed2k_links = emule_client.extract_ed2k_links_from_text(text)
            
            if not ed2k_links:
                QMessageBox.information(self, "Sin enlaces válidos", "No se encontraron enlaces ed2k válidos")
                return
            
            # Confirmar envío
            reply = QMessageBox.question(
                self,
                "Confirmar envío",
                f"¿Deseas enviar {len(ed2k_links)} enlaces a eMule?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                # Enviar enlaces
                result = emule_client.add_multiple_ed2k_links(ed2k_links)
                
                if result['success']:
                    QMessageBox.information(
                        self,
                        "Éxito",
                        f"✅ {result['added']}/{result['total']} enlaces enviados a eMule"
                    )
                    self.append_log(f"🔗 {result['added']}/{result['total']} enlaces enviados a eMule")
                else:
                    QMessageBox.warning(
                        self,
                        "Error",
                        f"❌ Error enviando enlaces: {result.get('message', 'Error desconocido')}"
                    )
                    
        except Exception as e:
            self.logger.error(f"Error enviando enlaces a eMule: {e}")
            QMessageBox.critical(self, "Error", f"Error enviando enlaces a eMule: {e}")
    
    def send_series_to_emule(self):
        """Enviar enlaces de una serie específica a eMule"""
        try:
            from core.hispashare.downloader import HispashareDownloader
            
            series_data = self.get_selected_series_data()
            if not series_data:
                return
            
            # Crear downloader temporal
            downloader = HispashareDownloader(None, self.db)
            
            # Enviar enlaces de la serie a eMule
            result = downloader.send_links_to_emule(series_data['id'])
            
            if result['success']:
                QMessageBox.information(
                    self,
                    "Éxito",
                    f"✅ {result['added']}/{result['total']} enlaces de '{series_data['name']}' enviados a eMule"
                )
                self.append_log(f"🔗 Enlaces de '{series_data['name']}' enviados a eMule: {result['added']}/{result['total']}")
            else:
                QMessageBox.warning(
                    self,
                    "Error",
                    f"❌ Error enviando enlaces de '{series_data['name']}':\n{result.get('error', 'Error desconocido')}"
                )
                
        except Exception as e:
            self.logger.error(f"Error enviando serie a eMule: {e}")
            QMessageBox.critical(self, "Error", f"Error enviando serie a eMule: {e}")
    
    def auto_match_new_series(self):
        """Vincular automáticamente series sin TMDB ID"""
        try:
            # Obtener series sin TMDB ID
            series_list = self.db.get_all_series()
            unmatched_series = [s for s in series_list if not s.get('tmdb_id')]
            
            if not unmatched_series:
                return
            
            self.append_log(f"🎬 Vinculando {len(unmatched_series)} series con TMDB...")
            
            tmdb_client = TMDBClient(Settings.TMDB_API_KEY)
            matcher = TMDBAutoMatcher(tmdb_client)
            
            matched = 0
            for series in unmatched_series:
                self.append_log(f"   🔗 Matching: {series['name']}...")
                
                match_result = matcher.match_series(series)
                
                if match_result:
                    series['tmdb_id'] = match_result['tmdb_id']
                    series['year'] = match_result['year']
                    series['confidence_score'] = match_result['confidence_score']
                    series['needs_review'] = match_result['needs_review']
                    
                    self.db.update_tmdb_match(series['id'], match_result)
                    matched += 1
                    
                    confidence_emoji = "✅" if match_result['confidence_score'] >= 0.85 else "⚠️"
                    self.append_log(
                        f"      {confidence_emoji} {match_result['name']} "
                        f"({match_result['year']}) - "
                        f"Confianza: {match_result['confidence_score']:.1%}"
                    )
                else:
                    self.append_log(f"      ❌ No se encontró match para {series['name']}")
            
            self.append_log(f"✅ {matched} series vinculadas con TMDB")
            
            # Refrescar la lista después de vincular
            self.refresh_series_list()
            
        except Exception as e:
            self.logger.error(f"Error en auto_match_new_series: {e}")
            self.append_log(f"❌ Error vinculando con TMDB: {e}")
    
    def on_tmdb_matched(self, series_id, match_data):
        """Manejar cuando una serie es vinculada con TMDB en tiempo real"""
        try:
            self.update_series_tmdb_in_table(series_id, match_data)
        except Exception as e:
            self.logger.error(f"Error en on_tmdb_matched: {e}")
    
    def on_series_updated(self, series_id):
        """Manejar cuando una serie es actualizada en tiempo real"""
        try:
            self.refresh_series_row(series_id)
        except Exception as e:
            self.logger.error(f"Error en on_series_updated: {e}")
    
    def update_series_tmdb_in_table(self, series_id, match_data):
        """Actualizar datos TMDB de una serie en la tabla en tiempo real"""
        try:
            table = self.series_table
            
            # Buscar la fila correspondiente a la serie
            for row in range(table.rowCount()):
                item = table.item(row, 0)  # Columna Nombre (con atributo series_id)
                if item and hasattr(item, 'series_id') and item.series_id == series_id:
                    # Actualizar año (columna 2)
                    year_item = QTableWidgetItem(str(match_data.get('year', '')))
                    table.setItem(row, 2, year_item)
                    
                    # Actualizar TMDB ID (columna 3)
                    tmdb_item = QTableWidgetItem(str(match_data.get('tmdb_id', '')))
                    table.setItem(row, 3, tmdb_item)
                    
                    # Actualizar confianza (columna 4)
                    confidence = match_data.get('confidence_score', 0)
                    confidence_item = QTableWidgetItem(f"{confidence:.1%}")
                    
                    # Color según confianza
                    if confidence >= 0.85:
                        confidence_item.setBackground(QColor(144, 238, 144))  # Verde claro
                    elif confidence >= 0.7:
                        confidence_item.setBackground(QColor(255, 255, 224))  # Amarillo claro
                    else:
                        confidence_item.setBackground(QColor(255, 182, 193))  # Rosa claro
                    
                    table.setItem(row, 4, confidence_item)
                    
                    # Efecto visual temporal
                    original_bg = year_item.background()
                    highlight_color = QColor(173, 216, 230)  # Azul claro
                    
                    year_item.setBackground(highlight_color)
                    tmdb_item.setBackground(highlight_color)
                    
                    # Restaurar color original después de 3 segundos
                    QTimer.singleShot(3000, lambda: self.restore_row_background(row, [1, 2, 3], original_bg))
                    break
                    
        except Exception as e:
            self.logger.error(f"Error actualizando TMDB en tabla: {e}")
    
    def refresh_series_row(self, series_id):
        """Refrescar una fila específica de la tabla de series"""
        try:
            # Obtener datos actualizados de la serie
            series_data = self.db.get_series_by_id(series_id)
            if not series_data:
                return
            
            table = self.series_table
            
            # Buscar la fila correspondiente
            for row in range(table.rowCount()):
                item = table.item(row, 0)  # Columna Nombre (con atributo series_id)
                if item and hasattr(item, 'series_id') and item.series_id == series_id:
                    # Actualizar todas las columnas con los datos más recientes
                    self.populate_series_row(row, series_data)
                    break
                    
        except Exception as e:
            self.logger.error(f"Error refrescando fila de serie: {e}")
    
    def populate_series_row(self, row, series_data):
        """Llenar una fila específica de la tabla con datos de serie"""
        try:
            table = self.series_table
            
            # Nombre - Almacenar el ID de la serie como atributo del item
            name_item = QTableWidgetItem(series_data.get('name', ''))
            name_item.series_id = series_data.get('id')  # Almacenar ID para referencia
            table.setItem(row, 0, name_item)
            
            # Año
            table.setItem(row, 1, QTableWidgetItem(str(series_data.get('year', ''))))
            
            # TMDB ID
            table.setItem(row, 2, QTableWidgetItem(str(series_data.get('tmdb_id', ''))))
            
            # Confianza
            confidence = series_data.get('confidence_score', 0)
            if confidence is None:
                confidence = 0
            conf_item = QTableWidgetItem(f"{confidence:.2%}")
            if confidence >= 0.85:
                conf_item.setForeground(QColor('green'))
            elif confidence >= 0.70:
                conf_item.setForeground(QColor('orange'))
            else:
                conf_item.setForeground(QColor('red'))
            table.setItem(row, 3, conf_item)
            
            # Faltantes
            missing_count = series_data.get('missing_count', 0)
            table.setItem(row, 4, QTableWidgetItem(str(missing_count)))
            
            # URL Hispashare
            url = series_data.get('hispashare_url', '')
            url_item = QTableWidgetItem(url)
            
            # Si la serie tiene búsqueda deshabilitada, mostrar indicación visual
            if series_data.get('search_disabled', 0) == 1:
                if not url:  # Solo si no tiene URL
                    url_item.setText("🚫 Búsqueda deshabilitada")
                    url_item.setForeground(QColor('gray'))
            
            table.setItem(row, 5, url_item)

            series_id = series_data.get('id')
            cover_url = series_data.get('hispashare_cover_url', '')
            cover_label = QLabel()
            cover_label.setFixedSize(self.cover_thumb_width, self.cover_thumb_height)
            cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setCellWidget(row, 6, cover_label)
            if series_id is not None:
                self.cover_labels[series_id] = cover_label
                if cover_url:
                    self.load_cover_for_series(series_id, cover_url)
            
            # Estado
            status = series_data.get('status', 'Pendiente')
            status_item = QTableWidgetItem(status)
            
            # Indicar visualmente si la búsqueda está deshabilitada
            if series_data.get('search_disabled', 0) == 1:
                status_item.setForeground(QColor('gray'))
            
            table.setItem(row, 7, status_item)
            
        except Exception as e:
            self.logger.error(f"Error llenando fila de serie: {e}")

    def load_cover_for_series(self, series_id, cover_url):
        if not series_id or not cover_url:
            return
        if series_id in self.cover_loaders:
            return
        loader = ImageLoaderThread(series_id, cover_url)
        loader.loaded.connect(self.on_cover_loaded)
        loader.failed.connect(self.on_cover_failed)
        self.cover_loaders[series_id] = loader
        loader.start()

    def on_cover_loaded(self, series_id, image_bytes):
        label = self.cover_labels.get(series_id)
        loader = self.cover_loaders.pop(series_id, None)
        if loader:
            loader.deleteLater()
        if not label:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(image_bytes):
            return
        label.setPixmap(pixmap.scaledToHeight(self.cover_thumb_height, Qt.TransformationMode.SmoothTransformation))

    def on_cover_failed(self, series_id):
        loader = self.cover_loaders.pop(series_id, None)
        if loader:
            loader.deleteLater()
    
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
