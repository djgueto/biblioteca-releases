from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableWidget, QLabel, QStatusBar,
    QTabWidget, QTextEdit, QProgressBar, QTableWidgetItem,
    QMessageBox, QHeaderView
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QColor

from .widgets.settings_dialog import SettingsWidget
from core.scheduler import SchedulerThread
from core.database.db_manager import DatabaseManager
from config.settings import Settings
import logging

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()
        self.logger = logging.getLogger(__name__)
        self.scheduler_thread = None
        
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
        
        btn_layout.addWidget(self.btn_scan)
        btn_layout.addWidget(self.btn_refresh)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        # Tabla de series
        self.series_table = QTableWidget()
        self.series_table.setColumnCount(7)
        self.series_table.setHorizontalHeaderLabels([
            "Nombre", "Año", "TMDB ID", "Confianza", 
            "Faltantes", "URL Hispashare", "Estado"
        ])
        
        # Ajustar columnas
        header = self.series_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        
        self.series_table.setAlternatingRowColors(True)
        self.series_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        
        layout.addWidget(self.series_table)
        
        # Cargar datos iniciales
        self.refresh_series_list()
        
        return widget
    
    def create_missing_tab(self):
        """Tab con episodios faltantes"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Filtros
        filter_layout = QHBoxLayout()
        self.filter_label = QLabel("Episodios que faltan en tu biblioteca")
        filter_layout.addWidget(self.filter_label)
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
        
        # Aquí iría la lógica de escaneo
        # Por ahora simulamos
        self.update_progress(100, "Escaneo completado")
        self.btn_scan.setEnabled(True)
    
    def refresh_series_list(self):
        """Refrescar lista de series"""
        series_list = self.db.get_all_series()
        
        self.series_table.setRowCount(len(series_list))
        
        for row, series in enumerate(series_list):
            # Nombre
            self.series_table.setItem(row, 0, QTableWidgetItem(series.get('name', '')))
            
            # Año
            self.series_table.setItem(row, 1, QTableWidgetItem(str(series.get('year', ''))))
            
            # TMDB ID
            self.series_table.setItem(row, 2, QTableWidgetItem(str(series.get('tmdb_id', ''))))
            
            # Confianza
            confidence = series.get('confidence_score', 0)
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
            self.series_table.setItem(row, 5, QTableWidgetItem(url))
            
            # Estado
            status = series.get('status', 'Pendiente')
            self.series_table.setItem(row, 6, QTableWidgetItem(status))
    
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
                self.scheduler_thread.stop()
                self.scheduler_thread.wait()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()