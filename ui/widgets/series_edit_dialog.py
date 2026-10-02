import requests
import webbrowser
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QTabWidget, QWidget, QTextEdit, QSpinBox, QScrollArea,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QPixmap
import logging
from config.settings import Settings


class TMDBSearchThread(QThread):
    """Thread para búsqueda en TMDB sin bloquear la UI"""
    results_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, query, api_key, content_mode='episodic'):
        super().__init__()
        self.query = query
        self.api_key = api_key
        self.content_mode = content_mode
        self.debug_label = f"TMDBSearchThread:{query[:60]}"
    
    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            if self.content_mode == 'single_file':
                url = "https://api.themoviedb.org/3/search/movie"
            else:
                url = "https://api.themoviedb.org/3/search/tv"
            params = {
                'api_key': self.api_key,
                'query': self.query,
                'language': 'es-ES'
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            results = data.get('results', [])
            
            # Añadir URL base para imágenes
            for result in results:
                if result.get('poster_path'):
                    result['poster_url'] = f"https://image.tmdb.org/t/p/w185{result['poster_path']}"
                else:
                    result['poster_url'] = None
            
            self.results_ready.emit(results)
            
        except Exception as e:
            self.error_occurred.emit(str(e))


class ImageLoader(QThread):
    """Thread para cargar imágenes de forma asíncrona"""
    image_loaded = pyqtSignal(int, QPixmap)  # índice, imagen
    
    def __init__(self, image_urls):
        super().__init__()
        self.image_urls = image_urls
        self.debug_label = f"ImageLoader:{len(image_urls)}"
        
    def run(self):
        for index, url in enumerate(self.image_urls):
            if self.isInterruptionRequested():
                return
            if url:
                try:
                    response = requests.get(url, timeout=4)
                    response.raise_for_status()
                    
                    pixmap = QPixmap()
                    pixmap.loadFromData(response.content)
                    
                    if not pixmap.isNull():
                        # Redimensionar imagen
                        scaled_pixmap = pixmap.scaled(100, 150, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                        self.image_loaded.emit(index, scaled_pixmap)
                except Exception:
                    # Si falla la carga, emitir pixmap vacío
                    self.image_loaded.emit(index, QPixmap())


class UrlPreviewThread(QThread):
    """Thread que hace login en Hispashare y captura screenshot de la URL dada."""

    status_signal = pyqtSignal(str)       # mensajes de progreso
    preview_ready = pyqtSignal(bytes)     # PNG bytes del screenshot
    error_occurred = pyqtSignal(str)      # mensaje de error

    def __init__(self, url: str, username: str, password: str, headless: bool = True):
        super().__init__()
        self.url = url
        self.username = username
        self.password = password
        self.headless = headless
        self.debug_label = f"UrlPreviewThread:{url[:60]}"

    def run(self):
        driver = None
        try:
            import time
            from core.auth.hispashare_login import HispashareAuth

            self.status_signal.emit("⏳ Iniciando navegador...")
            if self.isInterruptionRequested():
                return

            auth = HispashareAuth(self.username, self.password, headless=self.headless)
            auth.init_driver()
            driver = auth.driver

            self.status_signal.emit("🔐 Iniciando sesión en Hispashare...")
            if self.isInterruptionRequested():
                return

            ok = auth.login()
            if not ok:
                self.error_occurred.emit("No se pudo iniciar sesión en Hispashare. Comprueba las credenciales.")
                return

            if self.isInterruptionRequested():
                return

            self.status_signal.emit("🌐 Cargando la página...")
            driver.get(self.url)
            time.sleep(2)

            if self.isInterruptionRequested():
                return

            self.status_signal.emit("📷 Capturando pantalla...")
            png_bytes = driver.get_screenshot_as_png()
            self.preview_ready.emit(png_bytes)

        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            try:
                if driver:
                    driver.quit()
            except Exception:
                pass


class SeriesEditDialog(QDialog):
    """Diálogo para edición manual de series"""
    
    def __init__(self, series_data, parent=None, auto_search=False):
        super().__init__(parent)
        self.series_data = series_data
        self.selected_tmdb_data = None
        self.search_thread = None
        self.image_loader = None
        self.url_preview_thread = None
        self._retained_threads = []
        self.poster_labels = []  # Lista de labels para carátulas
        self.auto_search = auto_search
        self.logger = logging.getLogger(__name__)
        self.category_label = Settings.get_category_label(series_data.get('content_category', 'series'))
        self.content_mode = Settings.get_category_content_mode(series_data.get('content_category', 'series'))
        
        self.setWindowTitle(f"Editar {self.category_label}: {series_data.get('name', 'Sin nombre')}")
        self.setModal(True)
        self.resize(900, 700)  # Aumentar tamaño para carátulas
        
        self.init_ui()
        self.load_current_data()

    def _get_tmdb_result_name(self, result):
        if self.content_mode == 'single_file':
            return result.get('title') or result.get('name') or 'Sin nombre'
        return result.get('name') or result.get('title') or 'Sin nombre'

    def _get_tmdb_result_original_name(self, result):
        if self.content_mode == 'single_file':
            return result.get('original_title') or result.get('original_name') or 'N/A'
        return result.get('original_name') or result.get('original_title') or 'N/A'

    def _get_tmdb_result_date(self, result):
        if self.content_mode == 'single_file':
            return result.get('release_date', '')
        return result.get('first_air_date', '')

    def _get_tmdb_result_year(self, result):
        date_value = self._get_tmdb_result_date(result)
        return date_value[:4] if date_value else 'N/A'

    def _get_content_label(self):
        return "Contenido" if self.content_mode == 'single_file' else "Serie"

    def _cleanup_threads(self):
        self._cleanup_search_thread()
        self._cleanup_image_loader()
        self._cleanup_preview_thread()

    def _cleanup_preview_thread(self):
        thread = self.url_preview_thread
        self.url_preview_thread = None
        if not thread:
            return
        if thread.isRunning():
            thread.requestInterruption()
            self._retain_thread_until_finished(thread)
            return
        thread.deleteLater()

    def _retain_thread_until_finished(self, thread):
        if thread is None:
            return
        self._retained_threads.append(thread)

        def _release():
            try:
                self._retained_threads.remove(thread)
            except ValueError:
                pass
            thread.deleteLater()

        thread.finished.connect(_release)

    def _cleanup_search_thread(self):
        thread = self.search_thread
        self.search_thread = None
        if not thread:
            return
        if thread.isRunning():
            thread.requestInterruption()
            self._retain_thread_until_finished(thread)
            return
        thread.deleteLater()

    def _cleanup_image_loader(self):
        thread = self.image_loader
        self.image_loader = None
        if not thread:
            return
        if thread.isRunning():
            thread.requestInterruption()
            self._retain_thread_until_finished(thread)
            return
        thread.deleteLater()

    def accept(self):
        self._cleanup_threads()
        super().accept()

    def reject(self):
        self._cleanup_threads()
        super().reject()

    def closeEvent(self, event):
        self._cleanup_threads()
        super().closeEvent(event)
    
    def init_ui(self):
        """Inicializar interfaz de usuario"""
        layout = QVBoxLayout(self)
        
        # Información actual del contenido episódico
        info_label = QLabel(f"<b>{self.category_label}:</b> {self.series_data.get('name', 'Sin nombre')}")
        info_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(info_label)
        
        # Tabs para organizar la edición
        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget)
        
        # Tab 1: Vinculación TMDB
        self.create_tmdb_tab()

        # Tab 2: Episodios faltantes
        self.create_missing_episodes_tab()

        # Tab 3: URL Hispashare
        self.create_hispashare_tab()
        
        # Botones
        button_layout = QHBoxLayout()
        
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.clicked.connect(self.reject)
        
        self.btn_save = QPushButton("Guardar Cambios")
        self.btn_save.clicked.connect(self.save_changes)
        self.btn_save.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-weight: bold; }")
        
        button_layout.addStretch()
        button_layout.addWidget(self.btn_cancel)
        button_layout.addWidget(self.btn_save)
        
        layout.addLayout(button_layout)
    
    def create_tmdb_tab(self):
        """Crear tab de vinculación TMDB"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Información actual
        current_layout = QFormLayout()
        
        self.current_tmdb_id = QLabel(str(self.series_data.get('tmdb_id', 'No vinculado')))
        self.current_tmdb_name = QLabel(self.series_data.get('tmdb_name', 'No disponible'))
        confidence_score = self.series_data.get('confidence_score', 0)
        if confidence_score is not None:
            confidence_text = f"{confidence_score:.2%}"
        else:
            confidence_text = "No disponible"
        self.current_confidence = QLabel(confidence_text)
        
        current_layout.addRow("TMDB ID actual:", self.current_tmdb_id)
        current_layout.addRow("Nombre TMDB actual:", self.current_tmdb_name)
        current_layout.addRow("Confianza actual:", self.current_confidence)
        
        layout.addLayout(current_layout)
        
        # Búsqueda manual
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(f"Buscar {self.category_label.lower()} en TMDB...")
        self.search_input.setText(self.series_data.get('name', ''))
        
        self.btn_search = QPushButton("🔍 Buscar")
        self.btn_search.clicked.connect(self.search_tmdb)
        
        search_layout.addWidget(QLabel("Búsqueda:"))
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.btn_search)
        
        layout.addLayout(search_layout)
        
        # Lista de resultados con carátulas
        self.results_list = QListWidget()
        self.results_list.itemClicked.connect(self.on_result_selected)
        self.results_list.itemDoubleClicked.connect(self.on_result_double_clicked)
        self.results_list.setMinimumHeight(200)
        layout.addWidget(QLabel("Resultados de búsqueda:"))
        layout.addWidget(self.results_list)
        
        # Información de la serie seleccionada
        self.selected_info = QTextEdit()
        self.selected_info.setMaximumHeight(150)
        self.selected_info.setReadOnly(True)
        layout.addWidget(QLabel(f"Información del {self._get_content_label().lower()} seleccionado:"))
        layout.addWidget(self.selected_info)
        
        self.tab_widget.addTab(widget, "Vinculación TMDB")

    def create_missing_episodes_tab(self):
        """Crear tab con el detalle de episodios pendientes (para distinguir
        si falta una temporada nueva completa o solo episodios sueltos)."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.episodes_summary_label = QLabel("")
        self.episodes_summary_label.setWordWrap(True)
        layout.addWidget(self.episodes_summary_label)

        self.episodes_text = QTextEdit()
        self.episodes_text.setReadOnly(True)
        layout.addWidget(self.episodes_text)

        self.tab_widget.addTab(widget, "Episodios faltantes")

    def load_missing_episodes(self):
        """Consultar y mostrar el detalle de episodios pendientes de la serie actual."""
        series_id = self.series_data.get('id')
        if not series_id:
            self.episodes_summary_label.setText("No se puede consultar: esta serie no tiene id local.")
            return

        try:
            from core.database.db_manager import DatabaseManager
            db = DatabaseManager()
            missing = db.get_missing_episodes_for_series(series_id)
            local = db.get_local_episodes(series_id)
        except Exception as e:
            self.logger.error(f"Error al consultar episodios pendientes: {e}")
            self.episodes_summary_label.setText(f"Error al consultar episodios pendientes: {e}")
            return

        if not missing:
            self.episodes_summary_label.setText(
                "✅ No hay episodios pendientes según TMDB."
            )
            self.episodes_text.setHtml("")
            return

        local_seasons = {ep['season'] for ep in local}

        by_season = {}
        for ep in missing:
            by_season.setdefault(ep['season'], []).append(ep)

        new_seasons = sorted(s for s in by_season if s not in local_seasons)
        partial_seasons = sorted(s for s in by_season if s in local_seasons)

        summary_parts = [f"<b>{len(missing)} episodio(s) pendiente(s)</b>"]
        if new_seasons:
            summary_parts.append(
                f"🆕 Temporada(s) nueva(s) sin ningún episodio: {', '.join(str(s) for s in new_seasons)}"
            )
        if partial_seasons:
            summary_parts.append(
                f"📎 Episodios sueltos en temporada(s) ya empezada(s): {', '.join(str(s) for s in partial_seasons)}"
            )
        self.episodes_summary_label.setText(" &nbsp;·&nbsp; ".join(summary_parts))

        html_parts = []
        for season in sorted(by_season.keys()):
            eps = by_season[season]
            tag = "🆕 Temporada nueva" if season not in local_seasons else "📎 Episodios sueltos"
            html_parts.append(f"<p><b>Temporada {season}</b> — {tag} ({len(eps)} episodio(s))</p><ul>")
            for ep in eps:
                name = ep.get('name') or ''
                air_date = ep.get('air_date') or 'fecha desconocida'
                title_part = f" — {name}" if name else ""
                html_parts.append(f"<li>E{ep['episode']:02d}{title_part} <i>({air_date})</i></li>")
            html_parts.append("</ul>")

        self.episodes_text.setHtml("".join(html_parts))

    def create_hispashare_tab(self):
        """Crear tab de URL Hispashare con vista previa de la página."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # -- URL actual --
        form_layout = QFormLayout()
        self.current_url_label = QLabel(self.series_data.get('hispashare_url', 'No disponible'))
        self.current_url_label.setWordWrap(True)
        form_layout.addRow("URL actual:", self.current_url_label)
        layout.addLayout(form_layout)

        # -- Campo de nueva URL --
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("Nueva URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://www.hispashare.org/...")
        self.url_input.setText(self.series_data.get('hispashare_url', ''))
        url_layout.addWidget(self.url_input)
        layout.addLayout(url_layout)

        # -- Botones de acción --
        actions_layout = QHBoxLayout()

        self.btn_validate_url = QPushButton("🔍 Capturar vista previa")
        self.btn_validate_url.setToolTip(
            "Abre la URL en un navegador en segundo plano, inicia sesión en Hispashare "
            "y muestra una captura de pantalla para verificar que la URL es correcta."
        )
        self.btn_validate_url.clicked.connect(self.validate_url)
        actions_layout.addWidget(self.btn_validate_url)

        self.btn_open_browser = QPushButton("🌐 Abrir en navegador")
        self.btn_open_browser.setToolTip("Abre la URL en el navegador predeterminado del sistema.")
        self.btn_open_browser.clicked.connect(self.open_url_in_browser)
        actions_layout.addWidget(self.btn_open_browser)

        actions_layout.addStretch()
        layout.addLayout(actions_layout)

        # -- Estado / progreso --
        self.url_status_label = QLabel("")
        self.url_status_label.setWordWrap(True)
        layout.addWidget(self.url_status_label)

        # -- Área de vista previa (screenshot) --
        preview_label_header = QLabel("Vista previa:")
        layout.addWidget(preview_label_header)

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(False)
        self.preview_scroll.setMinimumHeight(350)
        self.preview_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.preview_image_label = QLabel("Sin vista previa. Pulsa \"Capturar vista previa\" para cargar.")
        self.preview_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image_label.setStyleSheet(
            "border: 1px solid #ccc; background-color: #f9f9f9; padding: 10px;"
        )
        self.preview_image_label.setMinimumSize(600, 340)
        self.preview_scroll.setWidget(self.preview_image_label)
        layout.addWidget(self.preview_scroll)

        self.tab_widget.addTab(widget, "URL Hispashare")
    
    def load_current_data(self):
        """Cargar datos actuales de la serie"""
        self.load_missing_episodes()

        # Si hay datos TMDB actuales, mostrar información
        if self.series_data.get('tmdb_id'):
            score = self.series_data.get('confidence_score')
            confidence_text = "No disponible"
            if isinstance(score, (int, float)):
                confidence_text = f"{score:.2%}"
            self.selected_info.setText(f"""
<b>{self._get_content_label()} actual vinculado:</b>
ID: {self.series_data.get('tmdb_id')}
Nombre: {self.series_data.get('tmdb_name', 'No disponible')}
Año: {self.series_data.get('year', 'No disponible')}
Confianza: {confidence_text}
            """.strip())
        
        # Si se solicita búsqueda automática, ejecutarla siempre (con o sin TMDB ID)
        if self.auto_search and self.series_data.get('name'):
            # Usar QTimer para ejecutar la búsqueda después de que se complete la inicialización de la UI
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(500, self.auto_search_tmdb)  # Aumentar tiempo para asegurar inicialización completa
    
    def auto_search_tmdb(self):
        """Ejecutar búsqueda automática en TMDB"""
        try:
            # Verificar que los widgets estén inicializados
            if not hasattr(self, 'search_input') or not hasattr(self, 'btn_search'):
                self.logger.warning("Widgets no inicializados para búsqueda automática")
                return
            
            # Establecer el nombre de la serie en el campo de búsqueda
            series_name = self.series_data.get('name', '')
            if series_name:
                self.search_input.setText(series_name)
                
                # Ejecutar la búsqueda
                self.search_tmdb()
            else:
                self.logger.warning("No hay nombre de serie para búsqueda automática")
        except Exception as e:
            self.logger.error(f"Error en búsqueda automática: {e}")
    
    def search_tmdb(self):
        """Buscar series en TMDB"""
        query = self.search_input.text().strip()
        if not query:
            QMessageBox.warning(self, "Advertencia", "Por favor ingresa un término de búsqueda")
            return
        
        # Obtener API key
        api_key = Settings.TMDB_API_KEY
        if not api_key:
            QMessageBox.critical(self, "Error", "API Key de TMDB no configurada")
            return
        
        # Limpiar resultados anteriores
        self._cleanup_search_thread()
        self._cleanup_image_loader()
        self.results_list.clear()
        self.selected_info.clear()
        self.poster_labels.clear()
        
        # Deshabilitar botón durante búsqueda
        self.btn_search.setEnabled(False)
        self.btn_search.setText("Buscando...")
        
        # Iniciar búsqueda en thread separado
        self.search_thread = TMDBSearchThread(query, api_key, self.content_mode)
        self.search_thread.results_ready.connect(self.on_search_results)
        self.search_thread.error_occurred.connect(self.on_search_error)
        self.search_thread.finished.connect(self._cleanup_search_thread)
        self.search_thread.start()
    
    def on_search_results(self, results):
        """Procesar resultados de búsqueda"""
        self.btn_search.setEnabled(True)
        self.btn_search.setText("🔍 Buscar")
        
        if not results:
            self.results_list.addItem("No se encontraron resultados")
            return
        
        # Ordenar resultados por fecha de estreno (más nuevos primero)
        def get_date_for_sorting(result):
            date_str = self._get_tmdb_result_date(result)
            if date_str:
                try:
                    # Convertir fecha a formato comparable (YYYY-MM-DD)
                    return date_str
                except:
                    return '0000-00-00'  # Fecha por defecto para series sin fecha
            return '0000-00-00'
        
        # Ordenar por fecha descendente (más nuevas primero)
        sorted_results = sorted(results, key=get_date_for_sorting, reverse=True)
        
        # Crear items con placeholder para carátulas
        image_urls = []
        for i, result in enumerate(sorted_results[:6]):  # Limitar resultados acelera la UI y la carga de posters
            name = self._get_tmdb_result_name(result)
            year = self._get_tmdb_result_year(result)
            overview = result.get('overview', 'Sin descripción')[:80] + "..." if len(result.get('overview', '')) > 80 else result.get('overview', 'Sin descripción')
            
            # Crear widget personalizado para el item
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, result)
            item.setSizeHint(QSize(0, 160))  # Altura para acomodar carátula
            
            # Crear widget con layout horizontal
            item_widget = QWidget()
            item_layout = QHBoxLayout(item_widget)
            
            # Label para carátula (placeholder inicial)
            poster_label = QLabel()
            poster_label.setFixedSize(100, 150)
            poster_label.setStyleSheet("border: 1px solid #ccc; background-color: #f0f0f0;")
            poster_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            poster_label.setText("Cargando...")
            poster_label.setScaledContents(True)
            self.poster_labels.append(poster_label)
            
            # Información de la serie
            info_label = QLabel(f"<b>{name}</b> ({year})<br><small>{overview}</small>")
            info_label.setWordWrap(True)
            
            item_layout.addWidget(poster_label)
            item_layout.addWidget(info_label, 1)
            
            self.results_list.addItem(item)
            self.results_list.setItemWidget(item, item_widget)
            
            # Guardar URL de imagen para cargar después
            image_urls.append(result.get('poster_url'))
        
        # Cargar imágenes de forma asíncrona
        if image_urls:
            self._cleanup_image_loader()
            self.image_loader = ImageLoader(image_urls)
            self.image_loader.image_loaded.connect(self.on_image_loaded)
            self.image_loader.finished.connect(self._cleanup_image_loader)
            self.image_loader.start()
    
    def on_image_loaded(self, index, pixmap):
        """Manejar imagen cargada"""
        if index < len(self.poster_labels):
            if not pixmap.isNull():
                self.poster_labels[index].setPixmap(pixmap)
                self.poster_labels[index].setText("")
            else:
                self.poster_labels[index].setText("Sin imagen")
    
    def on_search_error(self, error_msg):
        """Manejar errores de búsqueda"""
        self.btn_search.setEnabled(True)
        self.btn_search.setText("🔍 Buscar")
        
        QMessageBox.critical(self, "Error de búsqueda", f"Error al buscar en TMDB:\n{error_msg}")
    
    def on_result_selected(self, item):
        """Manejar selección de resultado"""
        result_data = item.data(Qt.ItemDataRole.UserRole)
        if not result_data:
            return
        
        self.selected_tmdb_data = result_data
        
        # Mostrar información detallada
        info_text = f"""
<b>{self._get_content_label()} seleccionado:</b><br>
<b>ID:</b> {result_data.get('id')}<br>
<b>Nombre:</b> {self._get_tmdb_result_name(result_data)}<br>
<b>Nombre original:</b> {self._get_tmdb_result_original_name(result_data)}<br>
<b>Fecha de estreno:</b> {self._get_tmdb_result_date(result_data) or 'N/A'}<br>
<b>Popularidad:</b> {result_data.get('popularity', 0):.1f}<br>
<b>Puntuación:</b> {result_data.get('vote_average', 0):.1f}/10<br>
<b>Descripción:</b> {result_data.get('overview', 'Sin descripción')}
        """
        
        self.selected_info.setHtml(info_text)
    
    def on_result_double_clicked(self, item):
        """Manejar doble clic en resultado - seleccionar y guardar automáticamente"""
        # Primero seleccionar el resultado
        self.on_result_selected(item)
        
        # Si hay datos seleccionados, guardar automáticamente
        if hasattr(self, 'selected_tmdb_data') and self.selected_tmdb_data:
            # Preparar los cambios con la información de TMDB
            changes = {}
            
            # Agregar ID de TMDB
            changes['tmdb_id'] = self.selected_tmdb_data.get('id')
            
            # Establecer confianza al 100% para selección manual
            changes['confidence_score'] = 1.0
            
            # Establecer que no necesita revisión (selección manual)
            changes['needs_review'] = False
            
            # Actualizar nombre TMDB
            changes['tmdb_name'] = self._get_tmdb_result_name(self.selected_tmdb_data)
            
            # Agregar año si está disponible
            release_date = self._get_tmdb_result_date(self.selected_tmdb_data)
            if release_date and len(release_date) >= 4:
                changes['year'] = int(release_date[:4])
            
            # Actualizar nombre si es diferente
            tmdb_name = self._get_tmdb_result_name(self.selected_tmdb_data)
            current_name = self.series_data.get('name', '')
            if tmdb_name and tmdb_name != current_name:
                changes['name'] = tmdb_name
            
            # Agregar fecha de estreno si está disponible
            if release_date:
                changes['first_air_date'] = release_date
            
            # Agregar descripción si está disponible
            if self.selected_tmdb_data.get('overview'):
                changes['overview'] = self.selected_tmdb_data.get('overview')
            
            # Guardar cambios y cerrar diálogo
            self.changes_to_save = changes
            self.accept()
    
    
    def validate_url(self):
        """Capturar vista previa de la URL de Hispashare en un navegador headless."""
        import urllib.parse

        url = self.url_input.text().strip()

        if not url:
            self._set_preview_status("❌ URL vacía. Introduce una URL antes de capturar.")
            return

        try:
            parsed = urllib.parse.urlparse(url)
            if not (parsed.scheme and parsed.netloc):
                self._set_preview_status("❌ Formato de URL inválido.")
                return
        except Exception as exc:
            self._set_preview_status(f"❌ Error al parsear la URL: {exc}")
            return

        if not Settings.HISPASHARE_USER or not Settings.HISPASHARE_PASS:
            self._set_preview_status(
                "⚠️ Credenciales de Hispashare no configuradas. "
                "Configúralas en Ajustes para poder capturar la vista previa."
            )
            return

        # Cancelar preview anterior si estuviera en curso
        self._cleanup_preview_thread()

        self._set_preview_busy(True)
        self._set_preview_status("⏳ Iniciando captura de vista previa...")
        self.preview_image_label.setText("Cargando vista previa...")
        self.preview_image_label.setPixmap(QPixmap())

        self.url_preview_thread = UrlPreviewThread(
            url=url,
            username=Settings.HISPASHARE_USER,
            password=Settings.HISPASHARE_PASS,
            headless=True,
        )
        self.url_preview_thread.status_signal.connect(self._set_preview_status)
        self.url_preview_thread.preview_ready.connect(self._on_preview_ready)
        self.url_preview_thread.error_occurred.connect(self._on_preview_error)
        self.url_preview_thread.finished.connect(lambda: self._set_preview_busy(False))
        self.url_preview_thread.start()

    def open_url_in_browser(self):
        """Abrir la URL del campo de texto en el navegador predeterminado del sistema."""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "URL vacía", "Introduce una URL antes de abrirla.")
            return
        webbrowser.open(url)

    def _set_preview_status(self, message: str):
        if hasattr(self, 'url_status_label'):
            self.url_status_label.setText(message)

    def _set_preview_busy(self, busy: bool):
        if hasattr(self, 'btn_validate_url'):
            self.btn_validate_url.setEnabled(not busy)
            self.btn_validate_url.setText(
                "⏳ Capturando..." if busy else "🔍 Capturar vista previa"
            )

    def _on_preview_ready(self, png_bytes: bytes):
        """Mostrar el screenshot recibido en el área de vista previa."""
        pixmap = QPixmap()
        pixmap.loadFromData(png_bytes)
        if pixmap.isNull():
            self._set_preview_status("❌ La imagen capturada está vacía o es inválida.")
            return

        # Escalar para encajar en el área de scroll manteniendo proporción
        scaled = pixmap.scaledToWidth(
            max(600, self.preview_scroll.width() - 20),
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_image_label.setPixmap(scaled)
        self.preview_image_label.setFixedSize(scaled.size())
        self.preview_image_label.setText("")
        self._set_preview_status("✅ Vista previa cargada correctamente.")

    def _on_preview_error(self, message: str):
        self.preview_image_label.setText(f"Error al cargar la vista previa:\n{message}")
        self._set_preview_status(f"❌ {message}")
    
    def save_changes(self):
        """Guardar cambios realizados"""
        changes = {}
        
        # Cambios en TMDB
        if self.selected_tmdb_data:
            changes['tmdb_id'] = self.selected_tmdb_data.get('id')
            changes['tmdb_name'] = self._get_tmdb_result_name(self.selected_tmdb_data)
            release_date = self._get_tmdb_result_date(self.selected_tmdb_data)
            changes['year'] = int(release_date[:4]) if release_date and len(release_date) >= 4 else None
            changes['confidence_score'] = 1.0  # Confianza máxima para selección manual
            changes['needs_review'] = False
        
        # Cambios en URL
        new_url = self.url_input.text().strip()
        if new_url:
            import urllib.parse
            parsed = urllib.parse.urlparse(new_url)
            if not (parsed.scheme and parsed.netloc):
                QMessageBox.warning(
                    self,
                    "URL inválida",
                    f"'{new_url}' no parece una URL válida (falta http(s):// o el dominio).\n"
                    "Corrígela antes de guardar.",
                )
                return
        if new_url != self.series_data.get('hispashare_url', ''):
            changes['hispashare_url'] = new_url

        if not changes:
            QMessageBox.information(self, "Sin cambios", "No se realizaron cambios")
            return
        
        # Confirmar cambios
        change_summary = []
        if 'tmdb_id' in changes:
            change_summary.append(f"TMDB: {changes['tmdb_name']} (ID: {changes['tmdb_id']})")
        if 'hispashare_url' in changes:
            change_summary.append(f"URL: {changes['hispashare_url']}")
        
        reply = QMessageBox.question(
            self,
            "Confirmar cambios",
            f"¿Confirmas los siguientes cambios?\n\n" + "\n".join(change_summary),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.changes_to_save = changes
            self.accept()
    
    def get_changes(self):
        """Obtener cambios realizados"""
        return getattr(self, 'changes_to_save', {})
