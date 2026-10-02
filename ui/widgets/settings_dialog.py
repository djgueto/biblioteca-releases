from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QFileDialog, QCheckBox, QSpinBox,
    QHBoxLayout, QMessageBox, QGroupBox, QListWidget,
    QListWidgetItem, QComboBox, QTabWidget
)
from PyQt6.QtCore import pyqtSignal
from config.settings import Settings, ENV_PATH
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

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        credentials_tab = QWidget()
        credentials_layout = QVBoxLayout(credentials_tab)
        episodic_tab = QWidget()
        episodic_layout = QVBoxLayout(episodic_tab)
        single_file_tab = QWidget()
        single_file_layout = QVBoxLayout(single_file_tab)
        automation_tab = QWidget()
        automation_layout = QVBoxLayout(automation_tab)
        emule_tab = QWidget()
        emule_layout = QVBoxLayout(emule_tab)

        self.tabs.addTab(credentials_tab, "Credenciales")
        self.tabs.addTab(episodic_tab, "Bibliotecas Episódicas")
        self.tabs.addTab(single_file_tab, "Archivo Único")
        self.tabs.addTab(automation_tab, "Automatización")
        self.tabs.addTab(emule_tab, "eMule")
        
        # Grupo TMDB
        tmdb_group = QGroupBox("🎬 TMDB API")
        tmdb_layout = QFormLayout()
        
        self.tmdb_key = QLineEdit()
        self.tmdb_key.setPlaceholderText("Introduce tu API Key de TMDB")
        self.tmdb_key.setEchoMode(QLineEdit.EchoMode.Password)
        tmdb_layout.addRow("API Key:", self.tmdb_key)
        
        tmdb_group.setLayout(tmdb_layout)
        credentials_layout.addWidget(tmdb_group)
        
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
        credentials_layout.addWidget(hispa_group)
        
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
        episodic_layout.addWidget(paths_group)
        
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
        single_file_layout.addWidget(movies_group)
        
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
        single_file_layout.addWidget(docs_group)

        # Grupo Rutas de Dibujos
        cartoons_group = QGroupBox("🧸 Rutas de Dibujos")
        cartoons_layout = QVBoxLayout()

        cartoons_primary_layout = QHBoxLayout()
        self.cartoons_path = QLineEdit()
        self.cartoons_path.setPlaceholderText("/ruta/principal/Dibujos")
        btn_browse_cartoons = QPushButton("Buscar...")
        btn_browse_cartoons.clicked.connect(lambda: self.browse_folder_for_type('cartoons'))
        cartoons_primary_layout.addWidget(self.cartoons_path)
        cartoons_primary_layout.addWidget(btn_browse_cartoons)

        cartoons_form = QFormLayout()
        cartoons_form.addRow("Ruta Principal:", cartoons_primary_layout)
        cartoons_layout.addLayout(cartoons_form)

        cartoons_additional_layout = QVBoxLayout()
        self.cartoons_paths_list = QListWidget()
        self.cartoons_paths_list.setMaximumHeight(100)
        self.cartoons_paths_list.setToolTip("Rutas adicionales de dibujos.")
        cartoons_additional_layout.addWidget(self.cartoons_paths_list)

        cartoons_buttons_layout = QHBoxLayout()
        btn_add_cartoons_path = QPushButton("➕ Agregar")
        btn_add_cartoons_path.clicked.connect(lambda: self.add_path_for_type('cartoons'))
        btn_remove_cartoons_path = QPushButton("➖ Eliminar")
        btn_remove_cartoons_path.clicked.connect(lambda: self.remove_path_for_type('cartoons'))
        cartoons_buttons_layout.addWidget(btn_add_cartoons_path)
        cartoons_buttons_layout.addWidget(btn_remove_cartoons_path)
        cartoons_buttons_layout.addStretch()
        cartoons_additional_layout.addLayout(cartoons_buttons_layout)

        cartoons_form_additional = QFormLayout()
        cartoons_form_additional.addRow("Rutas Adicionales:", cartoons_additional_layout)
        cartoons_layout.addLayout(cartoons_form_additional)

        cartoons_group.setLayout(cartoons_layout)
        episodic_layout.addWidget(cartoons_group)

        # Grupo Rutas de Películas de Animación
        animated_movies_group = QGroupBox("🎞️ Rutas de Películas Animación")
        animated_movies_layout = QVBoxLayout()

        animated_movies_primary_layout = QHBoxLayout()
        self.animated_movies_path = QLineEdit()
        self.animated_movies_path.setPlaceholderText("/ruta/principal/Películas Animación")
        btn_browse_animated_movies = QPushButton("Buscar...")
        btn_browse_animated_movies.clicked.connect(lambda: self.browse_folder_for_type('animated_movies'))
        animated_movies_primary_layout.addWidget(self.animated_movies_path)
        animated_movies_primary_layout.addWidget(btn_browse_animated_movies)

        animated_movies_form = QFormLayout()
        animated_movies_form.addRow("Ruta Principal:", animated_movies_primary_layout)
        animated_movies_layout.addLayout(animated_movies_form)

        animated_movies_additional_layout = QVBoxLayout()
        self.animated_movies_paths_list = QListWidget()
        self.animated_movies_paths_list.setMaximumHeight(100)
        self.animated_movies_paths_list.setToolTip("Rutas adicionales de películas de animación.")
        animated_movies_additional_layout.addWidget(self.animated_movies_paths_list)

        animated_movies_buttons_layout = QHBoxLayout()
        btn_add_animated_movies_path = QPushButton("➕ Agregar")
        btn_add_animated_movies_path.clicked.connect(lambda: self.add_path_for_type('animated_movies'))
        btn_remove_animated_movies_path = QPushButton("➖ Eliminar")
        btn_remove_animated_movies_path.clicked.connect(lambda: self.remove_path_for_type('animated_movies'))
        animated_movies_buttons_layout.addWidget(btn_add_animated_movies_path)
        animated_movies_buttons_layout.addWidget(btn_remove_animated_movies_path)
        animated_movies_buttons_layout.addStretch()
        animated_movies_additional_layout.addLayout(animated_movies_buttons_layout)

        animated_movies_form_additional = QFormLayout()
        animated_movies_form_additional.addRow("Rutas Adicionales:", animated_movies_additional_layout)
        animated_movies_layout.addLayout(animated_movies_form_additional)

        animated_movies_group.setLayout(animated_movies_layout)
        single_file_layout.addWidget(animated_movies_group)

        # Grupo Rutas de Documentales Series
        docuseries_group = QGroupBox("📺 Rutas de Documentales Series")
        docuseries_layout = QVBoxLayout()

        docuseries_primary_layout = QHBoxLayout()
        self.docuseries_path = QLineEdit()
        self.docuseries_path.setPlaceholderText("/ruta/principal/Documentales Series")
        btn_browse_docuseries = QPushButton("Buscar...")
        btn_browse_docuseries.clicked.connect(lambda: self.browse_folder_for_type('docuseries'))
        docuseries_primary_layout.addWidget(self.docuseries_path)
        docuseries_primary_layout.addWidget(btn_browse_docuseries)

        docuseries_form = QFormLayout()
        docuseries_form.addRow("Ruta Principal:", docuseries_primary_layout)
        docuseries_layout.addLayout(docuseries_form)

        docuseries_additional_layout = QVBoxLayout()
        self.docuseries_paths_list = QListWidget()
        self.docuseries_paths_list.setMaximumHeight(100)
        self.docuseries_paths_list.setToolTip("Rutas adicionales de documentales en serie.")
        docuseries_additional_layout.addWidget(self.docuseries_paths_list)

        docuseries_buttons_layout = QHBoxLayout()
        btn_add_docuseries_path = QPushButton("➕ Agregar")
        btn_add_docuseries_path.clicked.connect(lambda: self.add_path_for_type('docuseries'))
        btn_remove_docuseries_path = QPushButton("➖ Eliminar")
        btn_remove_docuseries_path.clicked.connect(lambda: self.remove_path_for_type('docuseries'))
        docuseries_buttons_layout.addWidget(btn_add_docuseries_path)
        docuseries_buttons_layout.addWidget(btn_remove_docuseries_path)
        docuseries_buttons_layout.addStretch()
        docuseries_additional_layout.addLayout(docuseries_buttons_layout)

        docuseries_form_additional = QFormLayout()
        docuseries_form_additional.addRow("Rutas Adicionales:", docuseries_additional_layout)
        docuseries_layout.addLayout(docuseries_form_additional)

        docuseries_group.setLayout(docuseries_layout)
        episodic_layout.addWidget(docuseries_group)

        # Grupo Rutas de Conciertos
        concerts_group = QGroupBox("🎤 Rutas de Conciertos")
        concerts_layout = QVBoxLayout()

        concerts_primary_layout = QHBoxLayout()
        self.concerts_path = QLineEdit()
        self.concerts_path.setPlaceholderText("/ruta/principal/Conciertos")
        btn_browse_concerts = QPushButton("Buscar...")
        btn_browse_concerts.clicked.connect(lambda: self.browse_folder_for_type('concerts'))
        concerts_primary_layout.addWidget(self.concerts_path)
        concerts_primary_layout.addWidget(btn_browse_concerts)

        concerts_form = QFormLayout()
        concerts_form.addRow("Ruta Principal:", concerts_primary_layout)
        concerts_layout.addLayout(concerts_form)

        concerts_additional_layout = QVBoxLayout()
        self.concerts_paths_list = QListWidget()
        self.concerts_paths_list.setMaximumHeight(100)
        self.concerts_paths_list.setToolTip("Rutas adicionales de conciertos.")
        concerts_additional_layout.addWidget(self.concerts_paths_list)

        concerts_buttons_layout = QHBoxLayout()
        btn_add_concerts_path = QPushButton("➕ Agregar")
        btn_add_concerts_path.clicked.connect(lambda: self.add_path_for_type('concerts'))
        btn_remove_concerts_path = QPushButton("➖ Eliminar")
        btn_remove_concerts_path.clicked.connect(lambda: self.remove_path_for_type('concerts'))
        concerts_buttons_layout.addWidget(btn_add_concerts_path)
        concerts_buttons_layout.addWidget(btn_remove_concerts_path)
        concerts_buttons_layout.addStretch()
        concerts_additional_layout.addLayout(concerts_buttons_layout)

        concerts_form_additional = QFormLayout()
        concerts_form_additional.addRow("Rutas Adicionales:", concerts_additional_layout)
        concerts_layout.addLayout(concerts_form_additional)

        concerts_group.setLayout(concerts_layout)
        single_file_layout.addWidget(concerts_group)
        
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
        single_file_layout.addWidget(music_group)
        
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

        self.auto_rename_folder = QCheckBox("Renombrar carpeta al asignar TMDB manualmente")
        self.auto_rename_folder.setToolTip(
            "Cuando asignas un TMDB a una serie/película, la carpeta en disco se renombra "
            "automáticamente al nombre oficial de TMDB."
        )
        options_layout.addRow("Renombrado automático:", self.auto_rename_folder)

        options_group.setLayout(options_layout)
        automation_layout.addWidget(options_group)

        # Grupo Calidad / Upgrades
        quality_group = QGroupBox("📈 Calidad / Upgrades")
        quality_layout = QFormLayout()

        self.wait_for_4k = QCheckBox("Esperar por 4K/2160p si existe")
        self.wait_for_4k.setToolTip("Si Hispashare tiene secciones 4K, puede esperar unos días antes de descargar calidades inferiores.")
        quality_layout.addRow("WAIT_FOR_4K:", self.wait_for_4k)

        self.wait_for_4k_max_wait_days = QSpinBox()
        self.wait_for_4k_max_wait_days.setMinimum(0)
        self.wait_for_4k_max_wait_days.setMaximum(365)
        self.wait_for_4k_max_wait_days.setSuffix(" días")
        self.wait_for_4k_max_wait_days.setToolTip("Solo aplica si WAIT_FOR_4K está activado. 0 = no esperar (descarga ya).")
        quality_layout.addRow("Máx. espera 4K:", self.wait_for_4k_max_wait_days)

        self.upgrade_enabled = QCheckBox("Mejorar automáticamente a 4K cuando esté disponible")
        self.upgrade_enabled.setToolTip("En cada ciclo, busca enlaces de episodios ya existentes en tu biblioteca pero en calidad superior (ej. 2160p).")
        quality_layout.addRow("Upgrades auto:", self.upgrade_enabled)

        self.upgrade_target_quality = QComboBox()
        self.upgrade_target_quality.addItems(["2160p", "1080p"])
        self.upgrade_target_quality.setToolTip("Calidad objetivo para upgrades automáticos.")
        quality_layout.addRow("Objetivo:", self.upgrade_target_quality)

        self.upgrade_max_series = QSpinBox()
        self.upgrade_max_series.setMinimum(1)
        self.upgrade_max_series.setMaximum(500)
        self.upgrade_max_series.setToolTip("Límite de series procesadas por ciclo para upgrades.")
        quality_layout.addRow("Máx. series/ciclo:", self.upgrade_max_series)

        self.upgrade_max_links = QSpinBox()
        self.upgrade_max_links.setMinimum(1)
        self.upgrade_max_links.setMaximum(5000)
        self.upgrade_max_links.setToolTip("Límite de enlaces enviados por ciclo para upgrades.")
        quality_layout.addRow("Máx. enlaces/ciclo:", self.upgrade_max_links)

        quality_group.setLayout(quality_layout)
        automation_layout.addWidget(quality_group)

        # Grupo Wolfmax4k
        wolfmax_group = QGroupBox("🐺 Wolfmax4k — Upgrades 4K directos")
        wolfmax_layout = QFormLayout()

        self.wolfmax_upgrade_enabled = QCheckBox("Buscar y descargar upgrades 4K desde wolfmax4k.com")
        self.wolfmax_upgrade_enabled.setToolTip(
            "Busca en wolfmax4k.com versiones 4K de contenido que ya tienes en resolución inferior "
            "y descarga los archivos directamente al directorio de staging."
        )
        wolfmax_layout.addRow("Wolfmax upgrades:", self.wolfmax_upgrade_enabled)

        wolfmax_staging_layout = QHBoxLayout()
        self.wolfmax_staging_dir = QLineEdit()
        self.wolfmax_staging_dir.setPlaceholderText("Carpeta donde se guardan los 4K descargados")
        btn_browse_wolfmax = QPushButton("Buscar...")
        btn_browse_wolfmax.clicked.connect(self._browse_wolfmax_staging)
        wolfmax_staging_layout.addWidget(self.wolfmax_staging_dir)
        wolfmax_staging_layout.addWidget(btn_browse_wolfmax)
        wolfmax_layout.addRow("Carpeta staging:", wolfmax_staging_layout)

        self.wolfmax_max_downloads = QSpinBox()
        self.wolfmax_max_downloads.setMinimum(1)
        self.wolfmax_max_downloads.setMaximum(20)
        self.wolfmax_max_downloads.setValue(2)
        self.wolfmax_max_downloads.setToolTip("Máximo de archivos wolfmax descargados por ciclo del scheduler.")
        wolfmax_layout.addRow("Máx. descargas/ciclo:", self.wolfmax_max_downloads)

        wolfmax_group.setLayout(wolfmax_layout)
        automation_layout.addWidget(wolfmax_group)

        # Grupo eMule
        emule_group = QGroupBox("🔗 eMule Integración")
        emule_form_layout = QFormLayout()
        
        self.emule_url = QLineEdit()
        self.emule_url.setPlaceholderText("http://localhost:4711")
        emule_form_layout.addRow("URL Servidor:", self.emule_url)
        
        self.emule_username = QLineEdit()
        self.emule_username.setPlaceholderText("admin")
        emule_form_layout.addRow("Usuario:", self.emule_username)
        
        self.emule_password = QLineEdit()
        self.emule_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.emule_password.setPlaceholderText("Contraseña del servidor web")
        emule_form_layout.addRow("Contraseña:", self.emule_password)
        
        self.emule_auto_send = QCheckBox("Enviar enlaces automáticamente")
        emule_form_layout.addRow("Auto-envío:", self.emule_auto_send)
        
        emule_group.setLayout(emule_form_layout)
        emule_layout.addWidget(emule_group)
        
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
        credentials_layout.addStretch()
        episodic_layout.addStretch()
        single_file_layout.addStretch()
        automation_layout.addStretch()
        emule_layout.addStretch()
    
    def _browse_wolfmax_staging(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Seleccionar carpeta de staging para 4K de Wolfmax",
            self.wolfmax_staging_dir.text(),
        )
        if folder:
            self.wolfmax_staging_dir.setText(folder)

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
            'cartoons': 'Dibujos',
            'animated_movies': 'Películas Animación',
            'docuseries': 'Documentales Series',
            'concerts': 'Conciertos',
            'music': 'Música'
        }
        
        type_widgets = {
            'movies': self.movies_path,
            'documentaries': self.documentaries_path,
            'cartoons': self.cartoons_path,
            'animated_movies': self.animated_movies_path,
            'docuseries': self.docuseries_path,
            'concerts': self.concerts_path,
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
        self.auto_rename_folder.setChecked(getattr(Settings, "AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN", True))

        self.wait_for_4k.setChecked(getattr(Settings, "WAIT_FOR_4K", True))
        self.wait_for_4k_max_wait_days.setValue(int(getattr(Settings, "WAIT_FOR_4K_MAX_WAIT_DAYS", 7)))

        self.upgrade_enabled.setChecked(getattr(Settings, "UPGRADE_ENABLED", False))
        current_target = str(getattr(Settings, "UPGRADE_TARGET_QUALITY", "2160p") or "2160p")
        idx = self.upgrade_target_quality.findText(current_target)
        if idx >= 0:
            self.upgrade_target_quality.setCurrentIndex(idx)
        else:
            self.upgrade_target_quality.setCurrentText(current_target)
        self.upgrade_max_series.setValue(int(getattr(Settings, "UPGRADE_MAX_SERIES_PER_CYCLE", 15)))
        self.upgrade_max_links.setValue(int(getattr(Settings, "UPGRADE_MAX_LINKS_PER_CYCLE", 80)))

        # Cargar configuración Wolfmax
        self.wolfmax_upgrade_enabled.setChecked(getattr(Settings, "WOLFMAX_UPGRADE_ENABLED", False))
        self.wolfmax_staging_dir.setText(getattr(Settings, "WOLFMAX_STAGING_DIR", "") or "")
        self.wolfmax_max_downloads.setValue(int(getattr(Settings, "WOLFMAX_MAX_DOWNLOADS_PER_CYCLE", 2)))

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
        self.load_paths_for_type('cartoons')
        self.load_paths_for_type('animated_movies')
        self.load_paths_for_type('docuseries')
        self.load_paths_for_type('concerts')
        self.load_paths_for_type('music')
    
    def load_paths_for_type(self, content_type):
        """Cargar rutas para un tipo específico de contenido"""
        type_settings = {
            'movies': (Settings.MOVIES_PATH, Settings.get_movies_paths, self.movies_path, self.movies_paths_list),
            'documentaries': (Settings.DOCUMENTARIES_PATH, Settings.get_documentaries_paths, self.documentaries_path, self.documentaries_paths_list),
            'cartoons': (Settings.CARTOONS_PATH, Settings.get_cartoons_paths, self.cartoons_path, self.cartoons_paths_list),
            'animated_movies': (Settings.ANIMATED_MOVIES_PATH, Settings.get_animated_movies_paths, self.animated_movies_path, self.animated_movies_paths_list),
            'docuseries': (Settings.DOCUSERIES_PATH, Settings.get_docuseries_paths, self.docuseries_path, self.docuseries_paths_list),
            'concerts': (Settings.CONCERTS_PATH, Settings.get_concerts_paths, self.concerts_path, self.concerts_paths_list),
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
            'cartoons': 'Dibujos',
            'animated_movies': 'Películas Animación',
            'docuseries': 'Documentales Series',
            'concerts': 'Conciertos',
            'music': 'Música'
        }
        
        type_widgets = {
            'movies': self.movies_paths_list,
            'documentaries': self.documentaries_paths_list,
            'cartoons': self.cartoons_paths_list,
            'animated_movies': self.animated_movies_paths_list,
            'docuseries': self.docuseries_paths_list,
            'concerts': self.concerts_paths_list,
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
            'cartoons': self.cartoons_paths_list,
            'animated_movies': self.animated_movies_paths_list,
            'docuseries': self.docuseries_paths_list,
            'concerts': self.concerts_paths_list,
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
            'cartoons': (self.cartoons_path, self.cartoons_paths_list),
            'animated_movies': (self.animated_movies_path, self.animated_movies_paths_list),
            'docuseries': (self.docuseries_path, self.docuseries_paths_list),
            'concerts': (self.concerts_path, self.concerts_paths_list),
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

        # Guardar rutas de series usando el nuevo método (también si se vació la lista)
        Settings.save_series_paths(all_series_paths)

        # Guardar rutas de otros tipos de contenido (también si se vació la lista,
        # para que "borrar todas las rutas y guardar" funcione igual en todas las categorías)
        for content_type in ['movies', 'documentaries', 'cartoons', 'animated_movies', 'docuseries', 'concerts', 'music']:
            paths = self.get_all_current_paths_for_type(content_type)
            if content_type == 'movies':
                Settings.save_movies_paths(paths)
            elif content_type == 'documentaries':
                Settings.save_documentaries_paths(paths)
            elif content_type == 'cartoons':
                Settings.save_cartoons_paths(paths)
            elif content_type == 'animated_movies':
                Settings.save_animated_movies_paths(paths)
            elif content_type == 'docuseries':
                Settings.save_docuseries_paths(paths)
            elif content_type == 'concerts':
                Settings.save_concerts_paths(paths)
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
            'AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN': str(self.auto_rename_folder.isChecked()),
            'WAIT_FOR_4K': str(self.wait_for_4k.isChecked()),
            'WAIT_FOR_4K_MAX_WAIT_DAYS': str(self.wait_for_4k_max_wait_days.value()),
            'UPGRADE_ENABLED': str(self.upgrade_enabled.isChecked()),
            'UPGRADE_TARGET_QUALITY': self.upgrade_target_quality.currentText().strip() or "2160p",
            'UPGRADE_MAX_SERIES_PER_CYCLE': str(self.upgrade_max_series.value()),
            'UPGRADE_MAX_LINKS_PER_CYCLE': str(self.upgrade_max_links.value()),
            'WOLFMAX_UPGRADE_ENABLED': str(self.wolfmax_upgrade_enabled.isChecked()),
            'WOLFMAX_STAGING_DIR': self.wolfmax_staging_dir.text().strip(),
            'WOLFMAX_MAX_DOWNLOADS_PER_CYCLE': str(self.wolfmax_max_downloads.value()),
            'EMULE_URL': self.emule_url.text(),
            'EMULE_USERNAME': self.emule_username.text(),
            'EMULE_PASSWORD': self.emule_password.text(),
            'EMULE_AUTO_SEND': str(self.emule_auto_send.isChecked())
        }
        
        try:
            # Leer configuración actual para preservar las rutas ya guardadas
            current_settings = {}
            try:
                with open(ENV_PATH, 'r', encoding='utf-8') as f:
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
            Settings.AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN = settings_dict['AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN'] == 'True'
            Settings.WAIT_FOR_4K = settings_dict['WAIT_FOR_4K'] == 'True'
            Settings.WAIT_FOR_4K_MAX_WAIT_DAYS = int(settings_dict['WAIT_FOR_4K_MAX_WAIT_DAYS'])
            Settings.UPGRADE_ENABLED = settings_dict['UPGRADE_ENABLED'] == 'True'
            Settings.UPGRADE_TARGET_QUALITY = settings_dict['UPGRADE_TARGET_QUALITY']
            Settings.UPGRADE_MAX_SERIES_PER_CYCLE = int(settings_dict['UPGRADE_MAX_SERIES_PER_CYCLE'])
            Settings.UPGRADE_MAX_LINKS_PER_CYCLE = int(settings_dict['UPGRADE_MAX_LINKS_PER_CYCLE'])
            Settings.WOLFMAX_UPGRADE_ENABLED = settings_dict['WOLFMAX_UPGRADE_ENABLED'] == 'True'
            Settings.WOLFMAX_STAGING_DIR = settings_dict['WOLFMAX_STAGING_DIR']
            Settings.WOLFMAX_MAX_DOWNLOADS_PER_CYCLE = int(settings_dict['WOLFMAX_MAX_DOWNLOADS_PER_CYCLE'])
            Settings.EMULE_URL = settings_dict['EMULE_URL']
            Settings.EMULE_USERNAME = settings_dict['EMULE_USERNAME']
            Settings.EMULE_PASSWORD = settings_dict['EMULE_PASSWORD']
            Settings.EMULE_AUTO_SEND = settings_dict['EMULE_AUTO_SEND'] == 'True'
            
            # Actualizar rutas en memoria
            if all_series_paths:
                Settings.SERIES_PATH = all_series_paths[0]
                Settings.SERIES_PATHS = ";".join(all_series_paths[1:]) if len(all_series_paths) > 1 else ""
            else:
                Settings.SERIES_PATH = ""
                Settings.SERIES_PATHS = ""

            category_attr_map = {
                'movies': ('MOVIES_PATH', 'MOVIES_PATHS'),
                'documentaries': ('DOCUMENTARIES_PATH', 'DOCUMENTARIES_PATHS'),
                'cartoons': ('CARTOONS_PATH', 'CARTOONS_PATHS'),
                'animated_movies': ('ANIMATED_MOVIES_PATH', 'ANIMATED_MOVIES_PATHS'),
                'docuseries': ('DOCUSERIES_PATH', 'DOCUSERIES_PATHS'),
                'concerts': ('CONCERTS_PATH', 'CONCERTS_PATHS'),
                'music': ('MUSIC_PATH', 'MUSIC_PATHS'),
            }
            for content_type, (primary_attr, additional_attr) in category_attr_map.items():
                paths = self.get_all_current_paths_for_type(content_type)
                setattr(Settings, primary_attr, paths[0] if paths else "")
                setattr(Settings, additional_attr, ";".join(paths[1:]) if len(paths) > 1 else "")
            
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
                "✅ Configuración guardada correctamente.\n\n"
                f"Rutas de series configuradas: {len(all_series_paths)}\n"
                f"Rutas de bibliotecas adicionales configuradas: "
                f"{sum(len(self.get_all_current_paths_for_type(t)) for t in ['movies', 'documentaries', 'cartoons', 'animated_movies', 'docuseries', 'concerts', 'music'])}"
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
            emule_client = EmuleClient(
                url=self.emule_url.text() or "http://localhost:4711",
                username=self.emule_username.text() or "admin",
                password=self.emule_password.text()
            )

            result = emule_client.test_connection()
            if result['success']:
                QMessageBox.information(
                    self,
                    "Éxito",
                    "✅ Conexión con aMule correcta"
                )
                self.logger.info("✅ Conexión aMule correcta")
            else:
                QMessageBox.warning(
                    self,
                    "Error de Conexión",
                    "❌ No se pudo conectar con aMule.\n\n"
                    f"Detalle: {result.get('error', 'Error desconocido')}\n\n"
                    "Verifica:\n- Que aMule esté ejecutándose\n- La URL del servidor web\n- La contraseña del servidor web"
                )
                
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"❌ Error probando conexión aMule:\n{str(e)}"
            )
            self.logger.error(f"Error probando conexión aMule: {e}")
