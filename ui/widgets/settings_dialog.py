from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QFileDialog, QCheckBox, QSpinBox,
    QHBoxLayout, QMessageBox, QGroupBox
)
from PyQt6.QtCore import pyqtSignal
from config.settings import Settings
import logging

class SettingsWidget(QWidget):
    settings_saved = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.init_ui()
        self.load_settings()
    
    def init_ui(self):
        """Inicializar interfaz"""
        layout = QVBoxLayout(self)
        
        # Grupo TMDB
        tmdb_group = QGroupBox("🎬 TMDB API")
        tmdb_layout = QFormLayout()
        
        self.tmdb_key = QLineEdit()
        self.tmdb_key.setPlaceholderText("Introduce tu API Key de TMDB")
        self.tmdb_key.setEchoMode(QLineEdit.EchoMode.Password)
        tmdb_layout.addRow("API Key:", self.tmdb_key)
        
        tmdb_group.setLayout(tmdb_layout)
        layout.addWidget(tmdb_group)
        
        # Grupo Hispashare
        hispa_group = QGroupBox("🔐 Hispashare Credenciales")
        hispa_layout = QFormLayout()
        
        self.hispashare_user = QLineEdit()
        self.hispashare_user.setPlaceholderText("Usuario")
        hispa_layout.addRow("Usuario:", self.hispashare_user)
        
        self.hispashare_pass = QLineEdit()
        self.hispashare_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.hispashare_pass.setPlaceholderText("Contraseña")
        hispa_layout.addRow("Contraseña:", self.hispashare_pass)
        
        hispa_group.setLayout(hispa_layout)
        layout.addWidget(hispa_group)
        
        # Grupo Rutas
        paths_group = QGroupBox("📁 Rutas")
        paths_layout = QFormLayout()
        
        path_layout = QHBoxLayout()
        self.series_path = QLineEdit()
        self.series_path.setPlaceholderText("/ruta/a/tus/Series")
        btn_browse = QPushButton("Buscar...")
        btn_browse.clicked.connect(self.browse_folder)
        path_layout.addWidget(self.series_path)
        path_layout.addWidget(btn_browse)
        paths_layout.addRow("Carpeta Series:", path_layout)
        
        paths_group.setLayout(paths_layout)
        layout.addWidget(paths_group)
        
        # Grupo Opciones
        options_group = QGroupBox("⚙️ Opciones")
        options_layout = QFormLayout()
        
        self.interval = QSpinBox()
        self.interval.setMinimum(1)
        self.interval.setMaximum(24)
        self.interval.setValue(4)
        self.interval.setSuffix(" horas")
        options_layout.addRow("Intervalo ciclo:", self.interval)
        
        self.headless = QCheckBox("Ejecutar sin ventana del navegador")
        options_layout.addRow("Modo headless:", self.headless)
        
        options_group.setLayout(options_layout)
        layout.addWidget(options_group)
        
        # Botones
        btn_layout = QHBoxLayout()
        
        btn_save = QPushButton("💾 Guardar")
        btn_save.clicked.connect(self.save_settings)
        btn_save.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; padding: 8px; }")
        
        btn_test = QPushButton("🧪 Probar Conexión")
        btn_test.clicked.connect(self.test_connection)
        
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_test)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        layout.addStretch()
    
    def browse_folder(self):
        """Buscar carpeta de series"""
        folder = QFileDialog.getExistingDirectory(
            self, 
            "Seleccionar carpeta de Series",
            self.series_path.text()
        )
        if folder:
            self.series_path.setText(folder)
    
    def load_settings(self):
        """Cargar configuración actual"""
        self.tmdb_key.setText(Settings.TMDB_API_KEY)
        self.hispashare_user.setText(Settings.HISPASHARE_USER)
        self.hispashare_pass.setText(Settings.HISPASHARE_PASS)
        self.series_path.setText(Settings.SERIES_PATH)
        self.interval.setValue(Settings.CHECK_INTERVAL_HOURS)
        self.headless.setChecked(Settings.SELENIUM_HEADLESS)
    
    def save_settings(self):
        """Guardar configuración"""
        settings_dict = {
            'TMDB_API_KEY': self.tmdb_key.text(),
            'HISPASHARE_USER': self.hispashare_user.text(),
            'HISPASHARE_PASS': self.hispashare_pass.text(),
            'SERIES_PATH': self.series_path.text(),
            'CHECK_INTERVAL_HOURS': str(self.interval.value()),
            'SELENIUM_HEADLESS': str(self.headless.isChecked())
        }
        
        try:
            Settings.save_settings(settings_dict)
            
            # Actualizar valores en memoria
            Settings.TMDB_API_KEY = settings_dict['TMDB_API_KEY']
            Settings.HISPASHARE_USER = settings_dict['HISPASHARE_USER']
            Settings.HISPASHARE_PASS = settings_dict['HISPASHARE_PASS']
            Settings.SERIES_PATH = settings_dict['SERIES_PATH']
            Settings.CHECK_INTERVAL_HOURS = int(settings_dict['CHECK_INTERVAL_HOURS'])
            Settings.SELENIUM_HEADLESS = settings_dict['SELENIUM_HEADLESS'] == 'True'
            
            self.settings_saved.emit()
            self.logger.info("Configuración guardada correctamente")
            
        except Exception as e:
            self.logger.error(f"Error al guardar configuración: {e}")
            QMessageBox.critical(self, "Error", f"No se pudo guardar la configuración:\n{str(e)}")
    
    def test_connection(self):
        """Probar conexiones TMDB y Hispashare"""
        from core.tmdb.api_client import TMDBClient
        from core.auth.hispashare_login import HispashareAuth
        
        errors = []
        
        # Probar TMDB
        try:
            tmdb = TMDBClient(self.tmdb_key.text())
            result = tmdb.search_series("Breaking Bad")
            if result:
                self.logger.info("✅ Conexión TMDB correcta")
            else:
                errors.append("TMDB: No se obtuvieron resultados")
        except Exception as e:
            errors.append(f"TMDB: {str(e)}")
        
        # Probar Hispashare (esto puede tomar tiempo)
        try:
            auth = HispashareAuth(
                self.hispashare_user.text(),
                self.hispashare_pass.text(),
                headless=True
            )
            if auth.login():
                self.logger.info("✅ Login Hispashare correcto")
                auth.close()
            else:
                errors.append("Hispashare: Login fallido")
        except Exception as e:
            errors.append(f"Hispashare: {str(e)}")
        
        if errors:
            QMessageBox.warning(
                self,
                "Errores de Conexión",
                "\n".join(errors)
            )
        else:
            QMessageBox.information(
                self,
                "Éxito",
                "✅ Todas las conexiones son correctas"
            )