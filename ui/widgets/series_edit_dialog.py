import requests
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QTabWidget, QWidget, QTextEdit, QSpinBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QPixmap
import json
import logging
from config.settings import Settings


class TMDBSearchThread(QThread):
    """Thread para búsqueda en TMDB sin bloquear la UI"""
    results_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, query, api_key):
        super().__init__()
        self.query = query
        self.api_key = api_key
    
    def run(self):
        try:
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
        
    def run(self):
        for index, url in enumerate(self.image_urls):
            if url:
                try:
                    response = requests.get(url, timeout=10)
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


class SeriesEditDialog(QDialog):
    """Diálogo para edición manual de series"""
    
    def __init__(self, series_data, parent=None, auto_search=False):
        super().__init__(parent)
        self.series_data = series_data
        self.selected_tmdb_data = None
        self.search_thread = None
        self.image_loader = None
        self.poster_labels = []  # Lista de labels para carátulas
        self.auto_search = auto_search
        self.logger = logging.getLogger(__name__)
        
        self.setWindowTitle(f"Editar Serie: {series_data.get('name', 'Sin nombre')}")
        self.setModal(True)
        self.resize(900, 700)  # Aumentar tamaño para carátulas
        
        self.init_ui()
        self.load_current_data()
    
    def init_ui(self):
        """Inicializar interfaz de usuario"""
        layout = QVBoxLayout(self)
        
        # Información actual de la serie
        info_label = QLabel(f"<b>Serie:</b> {self.series_data.get('name', 'Sin nombre')}")
        info_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(info_label)
        
        # Tabs para organizar la edición
        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget)
        
        # Tab 1: Vinculación TMDB
        self.create_tmdb_tab()
        
        # Tab 2: URL Hispashare
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
        self.search_input.setPlaceholderText("Buscar serie en TMDB...")
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
        layout.addWidget(QLabel("Información de la serie seleccionada:"))
        layout.addWidget(self.selected_info)
        
        self.tab_widget.addTab(widget, "Vinculación TMDB")
    
    def create_hispashare_tab(self):
        """Crear tab de URL Hispashare"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # URL actual
        form_layout = QFormLayout()
        
        self.current_url_label = QLabel(self.series_data.get('hispashare_url', 'No disponible'))
        self.current_url_label.setWordWrap(True)
        form_layout.addRow("URL actual:", self.current_url_label)
        
        layout.addLayout(form_layout)
        
        # Edición manual de URL
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://www.hispashare.org/...")
        self.url_input.setText(self.series_data.get('hispashare_url', ''))
        
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("Nueva URL:"))
        url_layout.addWidget(self.url_input)
        
        layout.addLayout(url_layout)
        
        # Botón para validar URL
        self.btn_validate_url = QPushButton("✓ Validar URL")
        self.btn_validate_url.clicked.connect(self.validate_url)
        layout.addWidget(self.btn_validate_url)
        
        # Información de validación
        self.url_validation_info = QTextEdit()
        self.url_validation_info.setMaximumHeight(100)
        self.url_validation_info.setReadOnly(True)
        layout.addWidget(QLabel("Estado de validación:"))
        layout.addWidget(self.url_validation_info)
        
        layout.addStretch()
        
        self.tab_widget.addTab(widget, "URL Hispashare")
    
    def load_current_data(self):
        """Cargar datos actuales de la serie"""
        # Si hay datos TMDB actuales, mostrar información
        if self.series_data.get('tmdb_id'):
            self.selected_info.setText(f"""
<b>Serie actual vinculada:</b>
ID: {self.series_data.get('tmdb_id')}
Nombre: {self.series_data.get('tmdb_name', 'No disponible')}
Año: {self.series_data.get('year', 'No disponible')}
Confianza: {self.series_data.get('confidence_score', 0):.2%}
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
        self.results_list.clear()
        self.selected_info.clear()
        self.poster_labels.clear()
        
        # Deshabilitar botón durante búsqueda
        self.btn_search.setEnabled(False)
        self.btn_search.setText("Buscando...")
        
        # Iniciar búsqueda en thread separado
        self.search_thread = TMDBSearchThread(query, api_key)
        self.search_thread.results_ready.connect(self.on_search_results)
        self.search_thread.error_occurred.connect(self.on_search_error)
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
            date_str = result.get('first_air_date', '')
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
        for i, result in enumerate(sorted_results[:10]):  # Limitar a 10 resultados
            name = result.get('name', 'Sin nombre')
            year = result.get('first_air_date', '')[:4] if result.get('first_air_date') else 'N/A'
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
            self.image_loader = ImageLoader(image_urls)
            self.image_loader.image_loaded.connect(self.on_image_loaded)
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
<b>Serie seleccionada:</b><br>
<b>ID:</b> {result_data.get('id')}<br>
<b>Nombre:</b> {result_data.get('name', 'Sin nombre')}<br>
<b>Nombre original:</b> {result_data.get('original_name', 'N/A')}<br>
<b>Fecha de estreno:</b> {result_data.get('first_air_date', 'N/A')}<br>
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
            changes['tmdb_name'] = self.selected_tmdb_data.get('name', '')
            
            # Agregar año si está disponible
            first_air_date = self.selected_tmdb_data.get('first_air_date', '')
            if first_air_date and len(first_air_date) >= 4:
                changes['year'] = int(first_air_date[:4])
            
            # Actualizar nombre si es diferente
            tmdb_name = self.selected_tmdb_data.get('name', '')
            current_name = self.series_data.get('name', '')
            if tmdb_name and tmdb_name != current_name:
                changes['name'] = tmdb_name
            
            # Agregar fecha de estreno si está disponible
            if self.selected_tmdb_data.get('first_air_date'):
                changes['first_air_date'] = self.selected_tmdb_data.get('first_air_date')
            
            # Agregar descripción si está disponible
            if self.selected_tmdb_data.get('overview'):
                changes['overview'] = self.selected_tmdb_data.get('overview')
            
            # Guardar cambios y cerrar diálogo
            self.changes_to_save = changes
            self.accept()
    
    
    def validate_url(self):
        """Validar URL de Hispashare"""
        url = self.url_input.text().strip()
        
        if not url:
            self.url_validation_info.setText("❌ URL vacía")
            return
        
        if not url.startswith('https://www.hispashare.org/'):
            self.url_validation_info.setText("⚠️ La URL no parece ser de Hispashare")
            return
        
        # Validación básica de formato
        try:
            import urllib.parse
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme and parsed.netloc:
                self.url_validation_info.setText("✅ Formato de URL válido")
            else:
                self.url_validation_info.setText("❌ Formato de URL inválido")
        except Exception as e:
            self.url_validation_info.setText(f"❌ Error al validar URL: {str(e)}")
    
    def save_changes(self):
        """Guardar cambios realizados"""
        changes = {}
        
        # Cambios en TMDB
        if self.selected_tmdb_data:
            changes['tmdb_id'] = self.selected_tmdb_data.get('id')
            changes['tmdb_name'] = self.selected_tmdb_data.get('name')
            changes['year'] = self.selected_tmdb_data.get('first_air_date', '')[:4] if self.selected_tmdb_data.get('first_air_date') else None
            changes['confidence_score'] = 1.0  # Confianza máxima para selección manual
            changes['needs_review'] = False
        
        # Cambios en URL
        new_url = self.url_input.text().strip()
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