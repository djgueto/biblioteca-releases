from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QFileDialog, QCheckBox, QSpinBox,
    QHBoxLayout, QMessageBox, QGroupBox, QListWidget,
    QListWidgetItem
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
        
        # Grupo Rutas de Series
        paths_group = QGroupBox("📁 Rutas de Series")
        paths_layout = QVBoxLayout()
        
        # Ruta principal (compatibilidad hacia atrás)
        primary_path_layout = QHBoxLayout()
        self.series_path = QLineEdit()
        self.series_path.setPlaceholderText("/ruta/principal/Series")
        btn_browse = QPushButton("Buscar...")
        btn_browse.clicked.connect(self.browse_folder)
        primary_path_layout.addWidget(self.series_path)
        primary_path_layout.addWidget(btn_browse)
        
        paths_form = QFormLayout()
        paths_form.addRow("Ruta Principal:", primary_path_layout)
        paths_layout.addLayout(paths_form)
        
        # Lista de rutas adicionales
        additional_paths_layout = QVBoxLayout()
        additional_paths_layout.addWidget(QWidget())  # Espaciador
        
        self.paths_list = QListWidget()
        self.paths_list.setMaximumHeight(120)
        self.paths_list.setToolTip("Rutas adicionales de series. Haz doble clic para editar.")
        additional_paths_layout.addWidget(self.paths_list)
        
        # Botones para gestionar rutas adicionales
        paths_buttons_layout = QHBoxLayout()
        
        btn_add_path = QPushButton("➕ Agregar Ruta")
        btn_add_path.clicked.connect(self.add_series_path)
        
        btn_remove_path = QPushButton("➖ Eliminar Ruta")
        btn_remove_path.clicked.connect(self.remove_series_path)
        
        paths_buttons_layout.addWidget(btn_add_path)
        paths_buttons_layout.addWidget(btn_remove_path)
        paths_buttons_layout.addStretch()
        
        additional_paths_layout.addLayout(paths_buttons_layout)
        
        paths_form_additional = QFormLayout()
        paths_form_additional.addRow("Rutas Adicionales:", additional_paths_layout)
        paths_layout.addLayout(paths_form_additional)
        
        paths_group.setLayout(paths_layout)
        layout.addWidget(paths_group)
        
        # Grupo Rutas de Películas
        movies_group = QGroupBox("🎬 Rutas de Películas")
        movies_layout = QVBoxLayout()
        
        # Ruta principal de películas
        movies_primary_layout = QHBoxLayout()
        self.movies_path = QLineEdit()
        self.movies_path.setPlaceholderText("/ruta/principal/Películas")
        btn_browse_movies = QPushButton("Buscar...")
        btn_browse_movies.clicked.connect(lambda: self.browse_folder_for_type('movies'))
        movies_primary_layout.addWidget(self.movies_path)
        movies_primary_layout.addWidget(btn_browse_movies)
        
        movies_form = QFormLayout()
        movies_form.addRow("Ruta Principal:", movies_primary_layout)
        movies_layout.addLayout(movies_form)
        
        # Lista de rutas adicionales de películas
        movies_additional_layout = QVBoxLayout()
        
        self.movies_paths_list = QListWidget()
        self.movies_paths_list.setMaximumHeight(100)
        self.movies_paths_list.setToolTip("Rutas adicionales de películas. Haz doble clic para editar.")
        movies_additional_layout.addWidget(self.movies_paths_list)
        
        # Botones para gestionar rutas de películas
        movies_buttons_layout = QHBoxLayout()
        
        btn_add_movies_path = QPushButton("➕ Agregar")
        btn_add_movies_path.clicked.connect(lambda: self.add_path_for_type('movies'))
        
        btn_remove_movies_path = QPushButton("➖ Eliminar")
        btn_remove_movies_path.clicked.connect(lambda: self.remove_path_for_type('movies'))
        
        movies_buttons_layout.addWidget(btn_add_movies_path)
        movies_buttons_layout.addWidget(btn_remove_movies_path)
        movies_buttons_layout.addStretch()
        
        movies_additional_layout.addLayout(movies_buttons_layout)
        
        movies_form_additional = QFormLayout()
        movies_form_additional.addRow("Rutas Adicionales:", movies_additional_layout)
        movies_layout.addLayout(movies_form_additional)
        
        movies_group.setLayout(movies_layout)
        layout.addWidget(movies_group)
        
        # Grupo Rutas de Documentales
        docs_group = QGroupBox("📚 Rutas de Documentales")
        docs_layout = QVBoxLayout()
        
        # Ruta principal de documentales
        docs_primary_layout = QHBoxLayout()
        self.documentaries_path = QLineEdit()
        self.documentaries_path.setPlaceholderText("/ruta/principal/Documentales")
        btn_browse_docs = QPushButton("Buscar...")
        btn_browse_docs.clicked.connect(lambda: self.browse_folder_for_type('documentaries'))
        docs_primary_layout.addWidget(self.documentaries_path)
        docs_primary_layout.addWidget(btn_browse_docs)
        
        docs_form = QFormLayout()
        docs_form.addRow("Ruta Principal:", docs_primary_layout)
        docs_layout.addLayout(docs_form)
        
        # Lista de rutas adicionales de documentales
        docs_additional_layout = QVBoxLayout()
        
        self.documentaries_paths_list = QListWidget()
        self.documentaries_paths_list.setMaximumHeight(100)
        self.documentaries_paths_list.setToolTip("Rutas adicionales de documentales. Haz doble clic para editar.")
        docs_additional_layout.addWidget(self.documentaries_paths_list)
        
        # Botones para gestionar rutas de documentales
        docs_buttons_layout = QHBoxLayout()
        
        btn_add_docs_path = QPushButton("➕ Agregar")
        btn_add_docs_path.clicked.connect(lambda: self.add_path_for_type('documentaries'))
        
        btn_remove_docs_path = QPushButton("➖ Eliminar")
        btn_remove_docs_path.clicked.connect(lambda: self.remove_path_for_type('documentaries'))
        
        docs_buttons_layout.addWidget(btn_add_docs_path)
        docs_buttons_layout.addWidget(btn_remove_docs_path)
        docs_buttons_layout.addStretch()
        
        docs_additional_layout.addLayout(docs_buttons_layout)
        
        docs_form_additional = QFormLayout()
        docs_form_additional.addRow("Rutas Adicionales:", docs_additional_layout)
        docs_layout.addLayout(docs_form_additional)
        
        docs_group.setLayout(docs_layout)
        layout.addWidget(docs_group)
        
        # Grupo Rutas de Música
        music_group = QGroupBox("🎵 Rutas de Música")
        music_layout = QVBoxLayout()
        
        # Ruta principal de música
        music_primary_layout = QHBoxLayout()
        self.music_path = QLineEdit()
        self.music_path.setPlaceholderText("/ruta/principal/Música")
        btn_browse_music = QPushButton("Buscar...")
        btn_browse_music.clicked.connect(lambda: self.browse_folder_for_type('music'))
        music_primary_layout.addWidget(self.music_path)
        music_primary_layout.addWidget(btn_browse_music)
        
        music_form = QFormLayout()
        music_form.addRow("Ruta Principal:", music_primary_layout)
        music_layout.addLayout(music_form)
        
        # Lista de rutas adicionales de música
        music_additional_layout = QVBoxLayout()
        
        self.music_paths_list = QListWidget()
        self.music_paths_list.setMaximumHeight(100)
        self.music_paths_list.setToolTip("Rutas adicionales de música. Haz doble clic para editar.")
        music_additional_layout.addWidget(self.music_paths_list)
        
        # Botones para gestionar rutas de música
        music_buttons_layout = QHBoxLayout()
        
        btn_add_music_path = QPushButton("➕ Agregar")
        btn_add_music_path.clicked.connect(lambda: self.add_path_for_type('music'))
        
        btn_remove_music_path = QPushButton("➖ Eliminar")
        btn_remove_music_path.clicked.connect(lambda: self.remove_path_for_type('music'))
        
        music_buttons_layout.addWidget(btn_add_music_path)
        music_buttons_layout.addWidget(btn_remove_music_path)
        music_buttons_layout.addStretch()
        
        music_additional_layout.addLayout(music_buttons_layout)
        
        music_form_additional = QFormLayout()
        music_form_additional.addRow("Rutas Adicionales:", music_additional_layout)
        music_layout.addLayout(music_form_additional)
        
        music_group.setLayout(music_layout)
        layout.addWidget(music_group)
        
        # Grupo Opciones
        options_group = QGroupBox("⚙️ Opciones")
        options_layout = QFormLayout()
        
        self.interval = QSpinBox()
        self.interval.setMinimum(1)
        self.interval.setMaximum(24)
        self.interval.setValue(4)
        self.interval.setSuffix(" horas")
        options_layout.addRow("Intervalo ciclo:", self.interval)
        
        self.discovery_pages = QSpinBox()
        self.discovery_pages.setMinimum(1)
        self.discovery_pages.setMaximum(10)
        self.discovery_pages.setValue(3)
        self.discovery_pages.setSuffix(" páginas")
        self.discovery_pages.setToolTip("Número de páginas a buscar en el descubrimiento de contenido")
        options_layout.addRow("Páginas descubrimiento:", self.discovery_pages)
        
        self.minimum_movie_size = QSpinBox()
        self.minimum_movie_size.setMinimum(1)
        self.minimum_movie_size.setMaximum(50)
        self.minimum_movie_size.setValue(4)
        self.minimum_movie_size.setSuffix(" GB")
        self.minimum_movie_size.setToolTip("Tamaño mínimo para descargar películas (filtro de calidad)")
        options_layout.addRow("Tamaño mínimo películas:", self.minimum_movie_size)
        
        self.headless = QCheckBox("Ejecutar sin ventana del navegador")
        options_layout.addRow("Modo headless:", self.headless)
        
        options_group.setLayout(options_layout)
        layout.addWidget(options_group)
        
        # Grupo eMule
        emule_group = QGroupBox("🔗 eMule Integración")
        emule_layout = QFormLayout()
        
        self.emule_url = QLineEdit()
        self.emule_url.setPlaceholderText("http://localhost:4711")
        emule_layout.addRow("URL Servidor:", self.emule_url)
        
        self.emule_username = QLineEdit()
        self.emule_username.setPlaceholderText("admin")
        emule_layout.addRow("Usuario:", self.emule_username)
        
        self.emule_password = QLineEdit()
        self.emule_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.emule_password.setPlaceholderText("Contraseña del servidor web")
        emule_layout.addRow("Contraseña:", self.emule_password)
        
        self.emule_auto_send = QCheckBox("Enviar enlaces automáticamente")
        emule_layout.addRow("Auto-envío:", self.emule_auto_send)
        
        emule_group.setLayout(emule_layout)
        layout.addWidget(emule_group)
        
        # Botones
        btn_layout = QHBoxLayout()
        
        btn_save = QPushButton("💾 Guardar")
        btn_save.clicked.connect(self.save_settings)
        btn_save.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; padding: 8px; }")
        
        btn_test = QPushButton("🧪 Probar Conexión")
        btn_test.clicked.connect(self.test_connection)
        
        btn_test_emule = QPushButton("🔗 Probar eMule")
        btn_test_emule.clicked.connect(self.test_emule_connection)
        
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_test)
        btn_layout.addWidget(btn_test_emule)
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
    
    def browse_folder_for_type(self, content_type):
        """Buscar carpeta para un tipo específico de contenido"""
        type_names = {
            'movies': 'Películas',
            'documentaries': 'Documentales', 
            'music': 'Música'
        }
        
        type_widgets = {
            'movies': self.movies_path,
            'documentaries': self.documentaries_path,
            'music': self.music_path
        }
        
        folder = QFileDialog.getExistingDirectory(
            self,
            f"Seleccionar carpeta de {type_names[content_type]}",
            type_widgets[content_type].text()
        )
        if folder:
            type_widgets[content_type].setText(folder)
    
    def load_settings(self):
        """Cargar configuración actual"""
        self.tmdb_key.setText(Settings.TMDB_API_KEY)
        self.hispashare_user.setText(Settings.HISPASHARE_USER)
        self.hispashare_pass.setText(Settings.HISPASHARE_PASS)
        self.series_path.setText(Settings.SERIES_PATH)
        self.interval.setValue(Settings.CHECK_INTERVAL_HOURS)
        self.discovery_pages.setValue(Settings.CONTENT_DISCOVERY_PAGES)
        self.minimum_movie_size.setValue(int(Settings.MINIMUM_MOVIE_SIZE_GB))
        self.headless.setChecked(Settings.SELENIUM_HEADLESS)
        
        # Cargar configuración de eMule
        self.emule_url.setText(Settings.EMULE_URL)
        self.emule_username.setText(Settings.EMULE_USERNAME)
        self.emule_password.setText(Settings.EMULE_PASSWORD)
        self.emule_auto_send.setChecked(Settings.EMULE_AUTO_SEND)
        
        # Cargar rutas de series
        self.load_additional_paths()
        
        # Cargar rutas de otros tipos de contenido
        self.load_paths_for_type('movies')
        self.load_paths_for_type('documentaries')
        self.load_paths_for_type('music')
    
    def load_paths_for_type(self, content_type):
        """Cargar rutas para un tipo específico de contenido"""
        type_settings = {
            'movies': (Settings.MOVIES_PATH, Settings.get_movies_paths, self.movies_path, self.movies_paths_list),
            'documentaries': (Settings.DOCUMENTARIES_PATH, Settings.get_documentaries_paths, self.documentaries_path, self.documentaries_paths_list),
            'music': (Settings.MUSIC_PATH, Settings.get_music_paths, self.music_path, self.music_paths_list)
        }
        
        primary_path, get_paths_func, primary_widget, list_widget = type_settings[content_type]
        
        # Cargar ruta principal
        primary_widget.setText(primary_path)
        
        # Cargar rutas adicionales
        list_widget.clear()
        all_paths = get_paths_func()
        
        # Agregar rutas adicionales (excluyendo la principal)
        if len(all_paths) > 1:
            for path in all_paths[1:]:  # Saltar la primera (principal)
                item = QListWidgetItem(path)
                item.setToolTip(f"Ruta: {path}")
                list_widget.addItem(item)

    def load_additional_paths(self):
        """Cargar rutas adicionales en la lista"""
        self.paths_list.clear()
        all_paths = Settings.get_series_paths()
        
        # Agregar rutas adicionales (excluyendo la principal)
        if len(all_paths) > 1:
            for path in all_paths[1:]:  # Saltar la primera (principal)
                item = QListWidgetItem(path)
                item.setToolTip(f"Ruta: {path}")
                self.paths_list.addItem(item)
    
    def add_path_for_type(self, content_type):
        """Agregar nueva ruta para un tipo específico de contenido"""
        type_names = {
            'movies': 'Películas',
            'documentaries': 'Documentales',
            'music': 'Música'
        }
        
        type_widgets = {
            'movies': self.movies_paths_list,
            'documentaries': self.documentaries_paths_list,
            'music': self.music_paths_list
        }
        
        folder = QFileDialog.getExistingDirectory(
            self,
            f"Seleccionar carpeta adicional de {type_names[content_type]}",
            ""
        )
        if folder:
            # Verificar que no esté duplicada
            all_current_paths = self.get_all_current_paths_for_type(content_type)
            if folder not in all_current_paths:
                item = QListWidgetItem(folder)
                item.setToolTip(f"Ruta: {folder}")
                type_widgets[content_type].addItem(item)
            else:
                QMessageBox.information(
                    self,
                    "Ruta Duplicada",
                    "Esta ruta ya está configurada."
                )
    
    def remove_path_for_type(self, content_type):
        """Eliminar ruta seleccionada para un tipo específico de contenido"""
        type_widgets = {
            'movies': self.movies_paths_list,
            'documentaries': self.documentaries_paths_list,
            'music': self.music_paths_list
        }
        
        list_widget = type_widgets[content_type]
        current_item = list_widget.currentItem()
        if current_item:
            reply = QMessageBox.question(
                self,
                "Confirmar Eliminación",
                f"¿Eliminar la ruta:\n{current_item.text()}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                list_widget.takeItem(list_widget.row(current_item))
        else:
            QMessageBox.information(
                self,
                "Selección Requerida",
                "Por favor selecciona una ruta para eliminar."
            )
    
    def add_series_path(self):
        """Agregar nueva ruta de series"""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Seleccionar carpeta adicional de Series",
            ""
        )
        if folder:
            # Verificar que no esté duplicada
            all_current_paths = self.get_all_current_paths()
            if folder not in all_current_paths:
                item = QListWidgetItem(folder)
                item.setToolTip(f"Ruta: {folder}")
                self.paths_list.addItem(item)
            else:
                QMessageBox.information(
                    self,
                    "Ruta Duplicada",
                    "Esta ruta ya está configurada."
                )
    
    def remove_series_path(self):
        """Eliminar ruta de series seleccionada"""
        current_item = self.paths_list.currentItem()
        if current_item:
            reply = QMessageBox.question(
                self,
                "Confirmar Eliminación",
                f"¿Eliminar la ruta:\n{current_item.text()}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.paths_list.takeItem(self.paths_list.row(current_item))
        else:
            QMessageBox.information(
                self,
                "Selección Requerida",
                "Por favor selecciona una ruta para eliminar."
            )
    
    def get_all_current_paths_for_type(self, content_type):
        """Obtener todas las rutas actuales para un tipo específico de contenido"""
        type_widgets = {
            'movies': (self.movies_path, self.movies_paths_list),
            'documentaries': (self.documentaries_path, self.documentaries_paths_list),
            'music': (self.music_path, self.music_paths_list)
        }
        
        paths = []
        primary_widget, list_widget = type_widgets[content_type]
        
        # Ruta principal
        if primary_widget.text().strip():
            paths.append(primary_widget.text().strip())
        
        # Rutas adicionales
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item and item.text().strip():
                paths.append(item.text().strip())
        
        return paths

    def get_all_current_paths(self):
        """Obtener todas las rutas actuales (principal + adicionales)"""
        paths = []
        
        # Ruta principal
        if self.series_path.text().strip():
            paths.append(self.series_path.text().strip())
        
        # Rutas adicionales
        for i in range(self.paths_list.count()):
            item = self.paths_list.item(i)
            if item and item.text().strip():
                paths.append(item.text().strip())
        
        return paths
    
    def save_settings(self):
        """Guardar configuración"""
        # Obtener todas las rutas configuradas para series
        all_series_paths = self.get_all_current_paths()
        
        # Guardar rutas de series usando el nuevo método
        if all_series_paths:
            Settings.save_series_paths(all_series_paths)
        
        # Guardar rutas de otros tipos de contenido
        for content_type in ['movies', 'documentaries', 'music']:
            paths = self.get_all_current_paths_for_type(content_type)
            if paths:
                if content_type == 'movies':
                    Settings.save_movies_paths(paths)
                elif content_type == 'documentaries':
                    Settings.save_documentaries_paths(paths)
                elif content_type == 'music':
                    Settings.save_music_paths(paths)
        
        # Preparar resto de configuración
        settings_dict = {
            'TMDB_API_KEY': self.tmdb_key.text(),
            'HISPASHARE_USER': self.hispashare_user.text(),
            'HISPASHARE_PASS': self.hispashare_pass.text(),
            'CHECK_INTERVAL_HOURS': str(self.interval.value()),
            'CONTENT_DISCOVERY_PAGES': str(self.discovery_pages.value()),
            'MINIMUM_MOVIE_SIZE_GB': str(float(self.minimum_movie_size.value())),
            'SELENIUM_HEADLESS': str(self.headless.isChecked()),
            'EMULE_URL': self.emule_url.text(),
            'EMULE_USERNAME': self.emule_username.text(),
            'EMULE_PASSWORD': self.emule_password.text(),
            'EMULE_AUTO_SEND': str(self.emule_auto_send.isChecked())
        }
        
        try:
            # Leer configuración actual para preservar las rutas ya guardadas
            current_settings = {}
            try:
                with open('.env', 'r') as f:
                    for line in f:
                        if '=' in line and not line.strip().startswith('#'):
                            key, value = line.strip().split('=', 1)
                            current_settings[key] = value
            except FileNotFoundError:
                pass
            
            # Actualizar con nueva configuración (sin sobrescribir rutas)
            current_settings.update(settings_dict)
            Settings.save_settings(current_settings)
            
            # Actualizar valores en memoria
            Settings.TMDB_API_KEY = settings_dict['TMDB_API_KEY']
            Settings.HISPASHARE_USER = settings_dict['HISPASHARE_USER']
            Settings.HISPASHARE_PASS = settings_dict['HISPASHARE_PASS']
            Settings.CHECK_INTERVAL_HOURS = int(settings_dict['CHECK_INTERVAL_HOURS'])
            Settings.CONTENT_DISCOVERY_PAGES = int(settings_dict['CONTENT_DISCOVERY_PAGES'])
            Settings.MINIMUM_MOVIE_SIZE_GB = float(settings_dict['MINIMUM_MOVIE_SIZE_GB'])
            Settings.SELENIUM_HEADLESS = settings_dict['SELENIUM_HEADLESS'] == 'True'
            Settings.EMULE_URL = settings_dict['EMULE_URL']
            Settings.EMULE_USERNAME = settings_dict['EMULE_USERNAME']
            Settings.EMULE_PASSWORD = settings_dict['EMULE_PASSWORD']
            Settings.EMULE_AUTO_SEND = settings_dict['EMULE_AUTO_SEND'] == 'True'
            
            # Actualizar rutas en memoria
            if all_series_paths:
                Settings.SERIES_PATH = all_series_paths[0]
                Settings.SERIES_PATHS = ";".join(all_series_paths[1:]) if len(all_series_paths) > 1 else ""
            
            # Limpiar series de rutas que ya no existen
            try:
                from core.database.db_manager import DatabaseManager
                db_manager = DatabaseManager()
                cleaned_count = db_manager.cleanup_deleted_series()
                if cleaned_count > 0:
                    self.logger.info(f"Se eliminaron {cleaned_count} series de rutas inexistentes")
                db_manager.close()
            except Exception as cleanup_error:
                self.logger.warning(f"Error al limpiar series huérfanas: {cleanup_error}")
            
            self.settings_saved.emit()
            self.logger.info("Configuración guardada correctamente")
            
            QMessageBox.information(
                self,
                "Configuración Guardada",
                f"✅ Configuración guardada correctamente.\n\nRutas de series configuradas: {len(all_series_paths)}"
            )
            
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
    
    def test_emule_connection(self):
        """Probar conexión con eMule"""
        from core.emule.client import EmuleClient
        
        try:
            # Crear cliente temporal con la configuración actual
            emule_client = EmuleClient()
            
            # Actualizar configuración temporal
            emule_client.url = self.emule_url.text() or "http://localhost:4711"
            emule_client.username = self.emule_username.text() or "admin"
            emule_client.password = self.emule_password.text()
            
            # Probar conexión
            if emule_client.test_connection():
                QMessageBox.information(
                    self,
                    "Éxito",
                    "✅ Conexión con eMule correcta"
                )
                self.logger.info("✅ Conexión eMule correcta")
            else:
                QMessageBox.warning(
                    self,
                    "Error de Conexión",
                    "❌ No se pudo conectar con eMule.\n\nVerifica:\n- Que eMule esté ejecutándose\n- La URL del servidor web\n- Las credenciales"
                )
                
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"❌ Error probando conexión eMule:\n{str(e)}"
            )
            self.logger.error(f"Error probando conexión eMule: {e}")