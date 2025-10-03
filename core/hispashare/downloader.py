import os
import re
import logging
from pathlib import Path
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from ..library.parser import EpisodeParser
from ..emule.client import EmuleClient
from config.settings import Settings


class HispashareDownloader:
    def __init__(self, driver, db_manager):
        self.driver = driver
        self.db_manager = db_manager
        self.logger = logging.getLogger(__name__)
        self.parser = EpisodeParser()
        self.emule_client = EmuleClient()
        self.settings = Settings()
    
    def get_missing_episodes(self, series_id):
        """
        Obtiene los episodios que faltan para una serie comparando con archivos locales
        
        Args:
            series_id: ID de la serie en la base de datos
            
        Returns:
            dict: {season: [episode_numbers]} de episodios faltantes
        """
        # Obtener información de la serie
        series = self.db_manager.get_series_by_id(series_id)
        if not series:
            self.logger.error(f"Serie con ID {series_id} no encontrada")
            return {}
        
        series_path = Path(series['folder_path'])
        if not series_path.exists():
            self.logger.error(f"Carpeta de serie no existe: {series_path}")
            return {}
        
        # Escanear episodios existentes
        existing_episodes = self._scan_existing_episodes(series_path)
        
        # Obtener información de TMDB para saber qué episodios deberían existir
        tmdb_episodes = self._get_tmdb_episodes(series['tmdb_id'])
        
        # Calcular episodios faltantes
        missing_episodes = {}
        for season, episodes in tmdb_episodes.items():
            missing_in_season = []
            for episode_num in episodes:
                if season not in existing_episodes or episode_num not in existing_episodes[season]:
                    missing_in_season.append(episode_num)
            
            if missing_in_season:
                missing_episodes[season] = missing_in_season
        
        return missing_episodes
    
    def _scan_existing_episodes(self, series_path):
        """
        Escanea la carpeta de la serie para encontrar episodios existentes
        
        Returns:
            dict: {season: [episode_numbers]}
        """
        existing_episodes = {}
        
        # Extensiones de video válidas
        video_extensions = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm'}
        
        # Buscar archivos de video recursivamente
        for file_path in series_path.rglob('*'):
            if file_path.is_file() and file_path.suffix.lower() in video_extensions:
                episode_info = self.parser.parse_episode(file_path.name)
                if episode_info:
                    season = episode_info['season']
                    episode = episode_info['episode']
                    
                    if season not in existing_episodes:
                        existing_episodes[season] = []
                    
                    if episode not in existing_episodes[season]:
                        existing_episodes[season].append(episode)
        
        return existing_episodes
    
    def _get_tmdb_episodes(self, tmdb_id):
        """
        Obtiene la lista de episodios desde TMDB
        
        Returns:
            dict: {season: [episode_numbers]}
        """
        # Por ahora, implementación básica
        # En una implementación completa, consultaríamos la API de TMDB
        # Para este ejemplo, asumimos que tenemos hasta 10 episodios por temporada
        tmdb_episodes = {}
        
        # Obtener información básica de la serie desde la base de datos
        conn = self.db_manager.get_connection()
        cursor = conn.cursor()
        
        # Por simplicidad, asumimos temporadas 1-5 con 10 episodios cada una
        # En una implementación real, esto vendría de TMDB
        for season in range(1, 6):
            tmdb_episodes[season] = list(range(1, 11))
        
        return tmdb_episodes
    
    def _calculate_quality_score(self, quality_text):
        """
        Calcula una puntuación para una calidad específica de forma simplificada
        
        Criterios de prioridad:
        1. Resolución: 2160p > 1080p > 720p
        2. Codec: x265 > x264
        3. HDR
        
        Args:
            quality_text: Texto de la calidad
            
        Returns:
            int: Puntuación de calidad (mayor = mejor)
        """
        score = 0
        quality_lower = quality_text.lower()
        
        # Resolución (prioridad máxima)
        if '2160p' in quality_lower or '4k' in quality_lower:
            score += 3000
        elif '1080p' in quality_lower:
            score += 2000
        elif '720p' in quality_lower:
            score += 1000
        else:
            # Si no tiene resolución específica, puntuación baja
            score += 100
        
        # Codec de video (segunda prioridad)
        if 'x265' in quality_lower or 'h265' in quality_lower or 'hevc' in quality_lower:
            score += 500
        elif 'x264' in quality_lower or 'h264' in quality_lower or 'avc' in quality_lower:
            score += 300
        
        # HDR (tercera prioridad)
        if 'hdr' in quality_lower or 'dolby vision' in quality_lower or 'dovi' in quality_lower:
            score += 100
        
        return score

    def _get_quality_sections_with_scores(self, soup):
        """
        Obtiene todas las secciones de calidad con sus puntuaciones
        
        Args:
            soup: BeautifulSoup object de la página
            
        Returns:
            list: Lista de tuplas (section, score, quality_text) ordenadas por puntuación
        """
        quality_sections = []
        
        # Buscar todos los h2 que contienen información de calidad
        h2_elements = soup.find_all('h2')
        
        for h2 in h2_elements:
            quality_text = h2.get_text(strip=True)
            
            # Buscar la tabla T2 asociada a este h2
            # La tabla está después del h2, dentro del mismo contenedor
            parent = h2.parent
            if parent:
                table = parent.find('table', class_='T2')
                if table:
                    score = self._calculate_quality_score(quality_text)
                    quality_sections.append((table, score, quality_text))
                    self.logger.info(f"Calidad encontrada: {quality_text} (puntuación: {score})")
        
        # Ordenar por puntuación descendente (mejor calidad primero)
        quality_sections.sort(key=lambda x: x[1], reverse=True)
        
        return quality_sections
    
    def process_download_page(self, hispashare_url, missing_episodes):
        """
        Procesa la página de descarga de Hispashare para obtener enlaces ed2k
        
        Args:
            hispashare_url: URL de la serie en Hispashare
            missing_episodes: dict con episodios faltantes {season: [episodes]}
            
        Returns:
            list: Lista de enlaces ed2k de episodios faltantes
        """
        try:
            self.logger.info(f"Procesando página de descarga: {hispashare_url}")
            self.driver.get(hispashare_url)
            
            # Esperar a que la página cargue
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "WIN1_CONTAINER"))
            )
            
            # Obtener el HTML de la página
            page_source = self.driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')
            
            # Obtener secciones de calidad ordenadas por puntuación
            quality_sections = self._get_quality_sections_with_scores(soup)
            
            if not quality_sections:
                self.logger.warning("No se encontraron secciones de calidad")
                return []
            
            # Procesar solo la mejor calidad que tenga episodios faltantes
            for section, score, quality_text in quality_sections:
                self.logger.info(f"Procesando calidad: {quality_text} (puntuación: {score})")
                
                # Procesar esta sección de calidad
                section_links = self._process_quality_section(section, missing_episodes, quality_text)
                
                # Si encontramos enlaces en esta calidad, no procesar calidades inferiores
                if section_links:
                    self.logger.info(f"✅ Encontrados {len(section_links)} enlaces en la mejor calidad disponible: {quality_text}")
                    return section_links
                else:
                    self.logger.info(f"No se encontraron episodios faltantes en: {quality_text}")
            
            self.logger.warning("No se encontraron episodios faltantes en ninguna calidad")
            return []
            
        except Exception as e:
            self.logger.error(f"Error procesando página de descarga: {e}")
            return []
    
    def _process_quality_section(self, section, missing_episodes, quality_text=None):
        """
        Procesa una sección de calidad específica para encontrar episodios faltantes
        
        Args:
            section: Elemento BeautifulSoup de la tabla T2
            missing_episodes: dict con episodios faltantes
            quality_text: Texto de la calidad para logs
            
        Returns:
            list: Enlaces ed2k de esta sección
        """
        ed2k_links = []
        quality_info = f" ({quality_text})" if quality_text else ""
        
        # Encontrar todas las filas de episodios
        episode_rows = section.find_all('tr')[1:]  # Saltar header
        
        checkboxes_to_select = []
        
        for row in episode_rows:
            # Extraer información del episodio
            checkbox = row.find('input', type='checkbox')
            if not checkbox:
                continue
            
            episode_link = row.find('a')
            if not episode_link:
                continue
            
            episode_title = episode_link.text.strip()
            
            # Parsear temporada y episodio del título
            episode_info = self.parser.parse_episode(episode_title)
            if not episode_info:
                continue
            
            season = episode_info['season']
            episode = episode_info['episode']
            
            # Verificar si este episodio está en la lista de faltantes
            if season in missing_episodes and episode in missing_episodes[season]:
                checkbox_id = checkbox.get('id')
                if checkbox_id:
                    checkboxes_to_select.append(checkbox_id)
                    self.logger.info(f"Episodio faltante encontrado{quality_info}: {episode_title}")
        
        # Seleccionar checkboxes de episodios faltantes
        if checkboxes_to_select:
            self.logger.info(f"Seleccionando {len(checkboxes_to_select)} episodios en calidad{quality_info}")
            ed2k_links = self._select_and_get_links(checkboxes_to_select, section, quality_text)
        else:
            self.logger.info(f"No hay episodios faltantes en esta calidad{quality_info}")
        
        return ed2k_links
    
    def _select_and_get_links(self, checkbox_ids, section, quality_text=None):
        """
        Selecciona checkboxes y obtiene los enlaces ed2k
        
        Args:
            checkbox_ids: Lista de IDs de checkboxes a seleccionar
            section: Sección HTML donde están los checkboxes
            quality_text: Texto de la calidad para logs
            
        Returns:
            list: Enlaces ed2k
        """
        ed2k_links = []
        quality_info = f" ({quality_text})" if quality_text else ""
        
        try:
            # Seleccionar cada checkbox
            for checkbox_id in checkbox_ids:
                try:
                    checkbox = self.driver.find_element(By.ID, checkbox_id)
                    if not checkbox.is_selected():
                        self.driver.execute_script("arguments[0].click();", checkbox)
                        self.logger.info(f"Checkbox seleccionado{quality_info}: {checkbox_id}")
                except NoSuchElementException:
                    self.logger.warning(f"Checkbox no encontrado{quality_info}: {checkbox_id}")
                    continue
            
            # Extraer el ID del grupo desde los checkboxes seleccionados
            # Los checkboxes tienen formato: elink_GROUPID_X
            group_id = None
            if checkbox_ids:
                # Extraer group_id del primer checkbox
                match = re.match(r'elink_(\d+)_\d+', checkbox_ids[0])
                if match:
                    group_id = match.group(1)
                    self.logger.info(f"ID de grupo detectado{quality_info}: {group_id}")
            
            if not group_id:
                self.logger.error(f"No se pudo extraer el ID del grupo de los checkboxes{quality_info}")
                return ed2k_links
            
            # Buscar el botón "Mostrar seleccionados" específico de este grupo
            try:
                # Primero intentar encontrar el botón con múltiples estrategias
                show_button = None
                
                # Estrategia 1: Buscar por onclick exacto
                try:
                    button_xpath_1 = f"//a[@onclick='javascript:DownloadSelected({group_id}, 4)']"
                    show_button = self.driver.find_element(By.XPATH, button_xpath_1)
                    self.logger.info(f"Botón encontrado con estrategia 1 para grupo {group_id}{quality_info}")
                except NoSuchElementException:
                    pass
                
                # Estrategia 2: Buscar por onclick que contenga el group_id
                if not show_button:
                    try:
                        button_xpath_2 = f"//a[contains(@onclick, 'DownloadSelected({group_id},')]"
                        show_button = self.driver.find_element(By.XPATH, button_xpath_2)
                        self.logger.info(f"Botón encontrado con estrategia 2 para grupo {group_id}{quality_info}")
                    except NoSuchElementException:
                        pass
                
                # Estrategia 3: Buscar por texto del span interno
                if not show_button:
                    try:
                        button_xpath_3 = f"//a[contains(@onclick, 'DownloadSelected')]//span[contains(text(), 'Mostrar seleccionados')]/.."
                        show_button = self.driver.find_element(By.XPATH, button_xpath_3)
                        # Verificar que sea el botón correcto comparando el onclick
                        onclick_attr = show_button.get_attribute('onclick')
                        if f"DownloadSelected({group_id}," in onclick_attr:
                            self.logger.info(f"Botón encontrado con estrategia 3 para grupo {group_id}{quality_info}")
                        else:
                            show_button = None
                    except NoSuchElementException:
                        pass
                
                # Estrategia 4: Buscar todos los botones y filtrar por group_id
                if not show_button:
                    try:
                        all_buttons = self.driver.find_elements(By.XPATH, "//a[contains(@onclick, 'DownloadSelected')]")
                        for button in all_buttons:
                            onclick_attr = button.get_attribute('onclick')
                            if f"DownloadSelected({group_id}," in onclick_attr:
                                show_button = button
                                self.logger.info(f"Botón encontrado con estrategia 4 para grupo {group_id}{quality_info}")
                                break
                    except Exception:
                        pass
                
                if show_button:
                    # Hacer scroll al botón para asegurar que esté visible
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", show_button)
                    import time
                    time.sleep(1)
                    
                    # Hacer click en el botón
                    self.driver.execute_script("arguments[0].click();", show_button)
                    self.logger.info(f"Botón 'Mostrar seleccionados' ejecutado para grupo {group_id}{quality_info}")
                    
                    # Esperar un poco para que se procese la acción
                    time.sleep(3)
                    
                    # Buscar el popup modal que aparece con los enlaces
                    try:
                        # Esperar a que aparezca el popup con los enlaces
                        WebDriverWait(self.driver, 15).until(
                            EC.presence_of_element_located((By.ID, "ELINKSLIST"))
                        )
                        
                        # Obtener el contenido del textarea con los enlaces ed2k
                        textarea = self.driver.find_element(By.ID, "ELINKSLIST")
                        links_text = textarea.get_attribute('value')
                        
                        if links_text:
                            # Dividir por líneas y filtrar enlaces ed2k válidos
                            lines = links_text.strip().split('\n')
                            for line in lines:
                                line = line.strip()
                                if line.startswith('ed2k://'):
                                    if line not in ed2k_links:  # Evitar duplicados
                                        ed2k_links.append(line)
                                        self.logger.info(f"Enlace ed2k obtenido{quality_info}: {line[:80]}...")
                        
                        # Cerrar el popup
                        try:
                            close_button = self.driver.find_element(By.XPATH, "//div[@class='X']")
                            self.driver.execute_script("arguments[0].click();", close_button)
                            self.logger.info(f"Popup cerrado{quality_info}")
                        except:
                            # Si no se puede cerrar, no es crítico
                            self.logger.warning(f"No se pudo cerrar el popup{quality_info}")
                                
                    except TimeoutException:
                        self.logger.warning(f"Timeout esperando popup con enlaces ed2k{quality_info}")
                        
                        # Intentar buscar enlaces ed2k directamente en la página
                        ed2k_elements = self.driver.find_elements(By.XPATH, "//a[starts-with(@href, 'ed2k://')]")
                        for element in ed2k_elements:
                            ed2k_link = element.get_attribute('href')
                            if ed2k_link and ed2k_link not in ed2k_links:
                                ed2k_links.append(ed2k_link)
                                self.logger.info(f"Enlace ed2k obtenido (fallback){quality_info}: {ed2k_link[:80]}...")
                else:
                    self.logger.error(f"No se pudo encontrar el botón 'Mostrar seleccionados' para grupo {group_id} con ninguna estrategia{quality_info}")
                    
                    # Debug: Listar todos los botones disponibles
                    try:
                        all_buttons = self.driver.find_elements(By.XPATH, "//a[contains(@onclick, 'DownloadSelected')]")
                        self.logger.info(f"Botones DownloadSelected encontrados: {len(all_buttons)}")
                        for i, button in enumerate(all_buttons):
                            onclick_attr = button.get_attribute('onclick')
                            self.logger.info(f"Botón {i+1}: {onclick_attr}")
                        
                        # Debug adicional: Guardar HTML de la página para análisis
                        page_source = self.driver.page_source
                        debug_file = f"debug_page_{group_id}.html"
                        with open(debug_file, 'w', encoding='utf-8') as f:
                            f.write(page_source)
                        self.logger.info(f"HTML de la página guardado en {debug_file} para análisis")
                        
                    except Exception as debug_e:
                        self.logger.error(f"Error en debug de botones: {debug_e}")
                    
            except Exception as e:
                self.logger.error(f"Error general buscando botón 'Mostrar seleccionados'{quality_info}: {e}")
            
        except Exception as e:
            self.logger.error(f"Error general obteniendo enlaces ed2k{quality_info}: {e}")
        
        self.logger.info(f"Total enlaces ed2k obtenidos en esta sección{quality_info}: {len(ed2k_links)}")
        return ed2k_links
    
    def download_missing_episodes(self, series_id):
        """
        Proceso completo para descargar episodios faltantes de una serie
        
        Args:
            series_id: ID de la serie
            
        Returns:
            list: Enlaces ed2k de episodios faltantes
        """
        # Obtener información de la serie
        series = self.db_manager.get_series_by_id(series_id)
        if not series or not series.get('hispashare_url'):
            self.logger.error(f"Serie {series_id} no tiene URL de Hispashare")
            return []
        
        # Obtener episodios faltantes
        missing_episodes = self.get_missing_episodes(series_id)
        if not missing_episodes:
            self.logger.info(f"No hay episodios faltantes para la serie {series['name']}")
            return []
        
        self.logger.info(f"Episodios faltantes para {series['name']}: {missing_episodes}")
        
        # Procesar página de descarga
        ed2k_links = self.process_download_page(series['hispashare_url'], missing_episodes)
        
        if ed2k_links:
            self.logger.info(f"Se obtuvieron {len(ed2k_links)} enlaces ed2k para {series['name']}")
            
            # Guardar enlaces en la base de datos o archivo
            self._save_ed2k_links(series_id, ed2k_links)
        
        return ed2k_links
    
    def _save_ed2k_links(self, series_id, ed2k_links):
        """
        Guarda los enlaces ed2k y opcionalmente los envía a eMule
        
        Args:
            series_id: ID de la serie
            ed2k_links: Lista de enlaces ed2k
        """
        try:
            # Crear directorio de descargas si no existe
            downloads_dir = Path("downloads")
            downloads_dir.mkdir(exist_ok=True)
            
            # Obtener nombre de la serie
            series = self.db_manager.get_series_by_id(series_id)
            series_name = series['name'] if series else f"series_{series_id}"
            
            # Limpiar nombre para archivo
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', series_name)
            
            # Guardar enlaces en archivo
            links_file = downloads_dir / f"{safe_name}_ed2k_links.txt"
            
            with open(links_file, 'w', encoding='utf-8') as f:
                f.write(f"Enlaces ed2k para: {series_name}\n")
                f.write(f"Fecha: {self._get_current_timestamp()}\n")
                f.write("-" * 50 + "\n\n")
                
                for i, link in enumerate(ed2k_links, 1):
                    f.write(f"{i}. {link}\n")
            
            self.logger.info(f"Enlaces ed2k guardados en: {links_file}")
            
            # Enviar a eMule si está configurado y habilitado
            if self.settings.EMULE_AUTO_SEND:
                if self.emule_client.is_configured():
                    self.logger.info(f"Enviando {len(ed2k_links)} enlaces a eMule...")
                    result = self.emule_client.add_multiple_ed2k_links(ed2k_links)
                    
                    if result['success']:
                        self.logger.info(f"✅ {result['added']}/{result['total']} enlaces enviados a eMule")
                    else:
                        self.logger.warning(f"❌ Error enviando enlaces a eMule: {result.get('message', 'Error desconocido')}")
                else:
                    # Verificar qué configuración específica falta
                    emule_config = self.settings.get_emule_config()
                    missing_config = []
                    
                    if not emule_config.get('url'):
                        missing_config.append("URL del servidor web")
                    if not emule_config.get('username'):
                        missing_config.append("nombre de usuario")
                    
                    if missing_config:
                        self.logger.warning(f"eMule no está configurado correctamente. Falta: {', '.join(missing_config)}")
                    else:
                        self.logger.warning("eMule no está configurado correctamente para envío automático")
            
        except Exception as e:
            self.logger.error(f"Error guardando enlaces ed2k: {e}")
    
    def send_links_to_emule(self, series_id):
        """
        Envía manualmente los enlaces de una serie a eMule
        
        Args:
            series_id: ID de la serie
            
        Returns:
            dict: Resultado de la operación
        """
        try:
            if not self.emule_client.is_configured():
                return {
                    'success': False,
                    'error': 'eMule no está configurado correctamente'
                }
            
            # Obtener nombre de la serie
            series = self.db_manager.get_series_by_id(series_id)
            if not series:
                return {
                    'success': False,
                    'error': 'Serie no encontrada'
                }
            
            series_name = series['name']
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', series_name)
            
            # Buscar archivo de enlaces
            downloads_dir = Path("downloads")
            links_file = downloads_dir / f"{safe_name}_ed2k_links.txt"
            
            if not links_file.exists():
                return {
                    'success': False,
                    'error': 'No se encontraron enlaces para esta serie'
                }
            
            # Leer enlaces del archivo
            with open(links_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extraer enlaces ed2k
            ed2k_links = self.emule_client.extract_ed2k_links_from_text(content)
            
            if not ed2k_links:
                return {
                    'success': False,
                    'error': 'No se encontraron enlaces ed2k válidos en el archivo'
                }
            
            # Enviar enlaces a eMule
            result = self.emule_client.add_multiple_ed2k_links(ed2k_links)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error enviando enlaces a eMule: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _get_current_timestamp(self):
        """Obtiene timestamp actual formateado"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")