from PyQt6.QtCore import QThread, pyqtSignal
import time
import logging
from datetime import datetime
import re

from core.auth.hispashare_login import HispashareAuth
from core.library.scanner import LibraryScanner
from core.tmdb.api_client import TMDBClient
from core.tmdb.auto_matcher import TMDBAutoMatcher
from core.hispashare.searcher import HispashareSearcher
from core.hispashare.downloader import HispashareDownloader
from core.hispashare.content_scanner import ContentScanner
from core.database.db_manager import DatabaseManager
from config.settings import Settings


class SchedulerThread(QThread):
    progress_signal = pyqtSignal(int, str)
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)
    url_saved_signal = pyqtSignal(int, str)  # series_id, url
    tmdb_matched_signal = pyqtSignal(int, dict)  # series_id, match_data
    series_updated_signal = pyqtSignal(int)  # series_id para refrescar fila específica
    
    def __init__(self):
        super().__init__()
        self.running = True
        self.logger = logging.getLogger(__name__)
        self.db = DatabaseManager()
        self.current_driver = None  # Mantener referencia al driver actual
    
    def run(self):
        """Ejecutar ciclo principal"""
        self.logger.info("Scheduler iniciado")
        while self.running:
            try:
                self.execute_cycle()
                
                # Esperar intervalo configurado
                hours = Settings.CHECK_INTERVAL_HOURS
                self.log_signal.emit(f"⏰ Próximo ciclo en {hours} horas")
                
                # Dormir en intervalos pequeños para poder detener el thread
                sleep_time = hours * 3600
                elapsed = 0
                while elapsed < sleep_time and self.running:
                    time.sleep(60)  # Dormir 1 minuto
                    elapsed += 60
                
            except Exception as e:
                self.logger.error(f"Error en ciclo: {e}", exc_info=True)
                self.error_signal.emit(str(e))
                self.log_signal.emit(f"❌ ERROR: {str(e)}")
                # Esperar antes de reintentar, pero verificar si seguimos ejecutándose
                for _ in range(1800):  # 30 minutos en segundos
                    if not self.running:
                        break
                    time.sleep(1)
        
        self.logger.info("Scheduler detenido correctamente")
    
    def execute_cycle(self):
        """Ejecutar un ciclo completo"""
        self.log_signal.emit("=" * 60)
        self.log_signal.emit(f"🚀 INICIO CICLO - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log_signal.emit("=" * 60)
        
        driver = None
        try:
            # 1. Login Hispashare
            self.progress_signal.emit(5, "🔐 Login Hispashare...")
            self.log_signal.emit("🔐 Iniciando sesión en Hispashare...")
            driver = self.login_hispashare()
            self.current_driver = driver  # Mantener referencia
            
            # 2. Limpiar series eliminadas
            self.progress_signal.emit(15, "🧹 Limpiando series eliminadas...")
            self.log_signal.emit("🧹 Limpiando series eliminadas del disco...")
            cleaned_count = self.db.cleanup_deleted_series()
            self.log_signal.emit(f"   ✅ {cleaned_count} series eliminadas")
            
            # 2.1. Limpiar episodios faltantes huérfanos
            self.log_signal.emit("🧹 Limpiando episodios faltantes huérfanos...")
            orphan_count = self.db.cleanup_orphaned_missing_episodes()
            self.log_signal.emit(f"   ✅ {orphan_count} episodios huérfanos eliminados")

            # 3. Escanear biblioteca
            self.progress_signal.emit(25, "📁 Escaneando biblioteca...")
            self.log_signal.emit("📁 Escaneando biblioteca local...")
            series_list = self.scan_library()
            self.log_signal.emit(f"   ✅ {len(series_list)} series encontradas")
            
            # 4. Limpiar duplicados de episodios
            self.progress_signal.emit(35, "🔧 Limpiando duplicados...")
            self.log_signal.emit("🔧 Limpiando episodios duplicados...")
            cleaned_count = self.db.cleanup_duplicate_episodes()
            self.log_signal.emit(f"   ✅ {cleaned_count} duplicados eliminados")
            
            # 4b. Limpiar duplicados de series
            self.log_signal.emit("🔧 Limpiando series duplicadas...")
            cleaned_series = self.db.cleanup_duplicate_series()
            self.log_signal.emit(f"   ✅ {cleaned_series} series duplicadas eliminadas")
            
            # 5. Insertar/actualizar series en BD
            self.progress_signal.emit(45, "💾 Actualizando base de datos...")
            self.log_signal.emit("💾 Actualizando series en base de datos...")
            for series in series_list:
                self.db.insert_or_update_series(series)
            
            # 6. Vincular con TMDB
            self.progress_signal.emit(55, "🎬 Vinculando con TMDB...")
            self.log_signal.emit("🎬 Vinculando series con TMDB...")
            db_series_list = self.db.get_all_series()
            self.match_tmdb(db_series_list)
            
            # 7. Detectar episodios faltantes
            self.progress_signal.emit(65, "🔍 Detectando episodios faltantes...")
            self.log_signal.emit("🔍 Detectando episodios faltantes...")
            missing_count = self.detect_missing()
            self.log_signal.emit(f"   ✅ {missing_count} episodios faltantes detectados")

            # 7.1. Buscar URLs para series nuevas sin episodios faltantes
            self.progress_signal.emit(70, "🔎 Buscando URLs para series nuevas...")
            self.log_signal.emit("🔎 Buscando URLs en Hispashare para series nuevas sin episodios faltantes...")
            self.search_hispashare_for_new_series(driver)

            # 8. Buscar en Hispashare
            self.progress_signal.emit(75, "🔎 Buscando en Hispashare...")
            self.log_signal.emit("🔎 Buscando URLs en Hispashare...")
            self.search_hispashare(driver)
            
            # 9. Descargar episodios faltantes
            self.progress_signal.emit(85, "⬇️ Procesando descargas...")
            self.log_signal.emit("⬇️ Procesando descargas de episodios faltantes...")
            self.process_downloads(driver)
            
            # 10. Barrido de nuevo contenido en Hispashare
            self.progress_signal.emit(95, "🔍 Buscando nuevo contenido...")
            self.log_signal.emit("🔍 Iniciando barrido de nuevo contenido en Hispashare...")
            self.scan_new_content(driver)
            
            self.progress_signal.emit(100, "✅ Ciclo completado")
            self.log_signal.emit("✅ CICLO COMPLETADO CON ÉXITO")
            self.log_signal.emit("=" * 60)
            
            self.finished_signal.emit()
            
        except Exception as e:
            self.logger.error(f"Error en execute_cycle: {e}", exc_info=True)
            raise
        finally:
            # Asegurar que el navegador se cierre siempre
            if driver:
                try:
                    driver.quit()
                    self.logger.info("Driver cerrado correctamente")
                except Exception as e:
                    self.logger.error(f"Error cerrando driver: {e}")
            self.current_driver = None
    
    def login_hispashare(self):
        """Iniciar sesión en Hispashare"""
        try:
            auth = HispashareAuth(
                Settings.HISPASHARE_USER,
                Settings.HISPASHARE_PASS,
                headless=Settings.SELENIUM_HEADLESS
            )
            
            if auth.login():
                self.log_signal.emit("   ✅ Login exitoso")
                return auth.driver
            else:
                raise Exception("Login fallido en Hispashare")
                
        except Exception as e:
            self.logger.error(f"Error en login: {e}")
            raise
    
    def scan_library(self):
        """Escanear biblioteca de series"""
        try:
            scanner = LibraryScanner(db_manager=self.db)
            series_list = scanner.scan()
            
            # Limpiar duplicados existentes antes de procesar nuevas series
            self.log_signal.emit("🧹 Limpiando episodios duplicados...")
            cleaned_count = self.db.cleanup_duplicate_episodes()
            if cleaned_count > 0:
                self.log_signal.emit(f"   ✅ {cleaned_count} episodios duplicados eliminados")
            
            # Guardar en base de datos
            for series in series_list:
                self.db.insert_or_update_series(series)
            
            return series_list
            
        except Exception as e:
            self.logger.error(f"Error en scan: {e}")
            raise
    
    def match_tmdb(self, series_list):
        """Vincular series con TMDB"""
        try:
            tmdb_client = TMDBClient(Settings.TMDB_API_KEY)
            matcher = TMDBAutoMatcher(tmdb_client)
            
            matched = 0
            for series in series_list:
                # Si ya tiene TMDB ID, saltar
                if series.get('tmdb_id'):
                    continue
                
                self.log_signal.emit(f"   🔗 Matching: {series['name']}...")
                
                match_result = matcher.match_series(series)
                
                if match_result:
                    series['tmdb_id'] = match_result['tmdb_id']
                    series['year'] = match_result['year']
                    series['confidence_score'] = match_result['confidence_score']
                    series['needs_review'] = match_result['needs_review']
                    
                    self.db.update_tmdb_match(series['id'], match_result)
                    matched += 1
                    
                    # Emitir señal para actualizar la interfaz en tiempo real
                    self.tmdb_matched_signal.emit(series['id'], match_result)
                    
                    confidence_emoji = "✅" if match_result['confidence_score'] >= 0.85 else "⚠️"
                    self.log_signal.emit(
                        f"      {confidence_emoji} {match_result['name']} "
                        f"({match_result['year']}) - "
                        f"Confianza: {match_result['confidence_score']:.1%}"
                    )
                else:
                    self.log_signal.emit(f"      ❌ No se encontró match")
            
            self.log_signal.emit(f"   ✅ {matched} series vinculadas con TMDB")
            
        except Exception as e:
            self.logger.error(f"Error en match_tmdb: {e}")
            raise
    
    def detect_missing(self):
        """Detectar episodios faltantes"""
        try:
            tmdb_client = TMDBClient(Settings.TMDB_API_KEY)
            series_list = self.db.get_series_with_tmdb()
            
            total_missing = 0
            
            for series in series_list:
                # Limpiar episodios faltantes existentes para esta serie antes de recalcular
                self.db.clear_missing_episodes(series['id'])
                
                # Obtener episodios de TMDB (solo los ya emitidos)
                tmdb_episodes = tmdb_client.get_all_episodes(series['tmdb_id'], only_aired=True)
                
                # Obtener episodios locales
                local_episodes = self.db.get_local_episodes(series['id'])
                
                # Comparar y detectar faltantes
                missing = self.compare_episodes(tmdb_episodes, local_episodes)
                
                # Guardar faltantes en BD
                for episode in missing:
                    episode['series_id'] = series['id']
                    episode['series_name'] = series['name']
                    self.db.insert_missing_episode(episode)
                    total_missing += 1
                
                if missing:
                    self.log_signal.emit(f"      📺 {series['name']}: {len(missing)} episodios faltantes")
            
            return total_missing
            
        except Exception as e:
            self.logger.error(f"Error en detect_missing: {e}")
            raise
    
    def compare_episodes(self, tmdb_episodes, local_episodes):
        """Comparar episodios TMDB vs locales"""
        # Crear set de episodios locales
        local_set = set()
        for ep in local_episodes:
            local_set.add((ep['season'], ep['episode']))
        
        # Encontrar faltantes
        missing = []
        for ep in tmdb_episodes:
            key = (ep['season_number'], ep['episode_number'])
            if key not in local_set:
                missing.append({
                    'season': ep['season_number'],
                    'episode': ep['episode_number'],
                    'name': ep['name'],
                    'air_date': ep.get('air_date', '')
                })
        
        return missing

    def _normalize_hispashare_query(self, text: str) -> str:
        value = (text or "").strip()
        if not value:
            return ""

        value = re.sub(r'\s+', ' ', value)
        value = re.sub(r'\bTemporadas?\s*\d+(?:\s*-\s*\d+)?\b', '', value, flags=re.IGNORECASE)
        value = re.sub(r'\bTemporada\s*\d+\b', '', value, flags=re.IGNORECASE)
        value = re.sub(r'\bSeason\s*\d+(?:\s*-\s*\d+)?\b', '', value, flags=re.IGNORECASE)
        value = re.sub(r'\bComplete\b', '', value, flags=re.IGNORECASE)
        value = re.sub(r'\bPack\b', '', value, flags=re.IGNORECASE)
        value = re.sub(r'\b\(.*?Temporad.*?\)\b', '', value, flags=re.IGNORECASE)
        value = re.sub(r'\s+', ' ', value).strip()
        return value

    def _get_hispashare_search_query(self, series: dict) -> tuple[str, int | None]:
        tmdb_name = series.get('tmdb_name') or ''
        local_name = series.get('name') or ''
        year = series.get('year')

        preferred = tmdb_name.strip() if tmdb_name and tmdb_name.strip() else local_name.strip()
        query = self._normalize_hispashare_query(preferred)
        if not query:
            query = self._normalize_hispashare_query(local_name)

        return query, year
    
    def search_hispashare(self, driver):
        """Buscar series en Hispashare - solo las que tienen episodios faltantes"""
        try:
            searcher = HispashareSearcher(driver, self.db)
            # Cambiar para obtener solo series con episodios faltantes sin URL
            series_list = self.db.get_series_with_missing_episodes_without_url()
            
            found = 0
            total_series = len(series_list)
            
            if total_series == 0:
                self.log_signal.emit("   ℹ️ No hay series con episodios faltantes que necesiten búsqueda en Hispashare")
                return
            
            self.log_signal.emit(f"   📊 Series con episodios faltantes a buscar: {total_series}")
            
            for i, series in enumerate(series_list, 1):
                query, year = self._get_hispashare_search_query(series)
                label = series.get('tmdb_name') or series.get('name')
                self.log_signal.emit(f"   🔎 [{i}/{total_series}] Buscando: {label} -> '{query}'...")
                
                try:
                    url = searcher.search_and_select(
                        query,
                        year,
                        series['id']  # Pasar el ID de la serie
                    )
                    
                    if url:
                        # Guardar URL inmediatamente cuando se encuentra
                        try:
                            self.db.update_hispashare_url(series['id'], url)
                            cover_url = self.db.derive_hispashare_cover_url(url)
                            if cover_url:
                                self.db.update_hispashare_cover_url(series['id'], cover_url)
                            found += 1
                            self.logger.info(f"URL guardada para '{series['name']}': {url}")
                            self.log_signal.emit(f"      ✅ URL encontrada y guardada: {url[:50]}...")
                            # Emitir señal para actualizar la UI en tiempo real
                            self.logger.info(f"EMITIENDO SEÑAL url_saved_signal para serie ID {series['id']}: {url}")
                            self.url_saved_signal.emit(series['id'], url)
                            self.logger.info(f"Señal url_saved_signal emitida correctamente")
                        except Exception as save_error:
                            self.logger.error(f"Error guardando URL para '{series['name']}': {save_error}")
                            self.log_signal.emit(f"      ⚠️ URL encontrada pero error al guardar: {str(save_error)}")
                    else:
                        self.log_signal.emit(f"      ❌ No se encontró (marcada para no buscar más)")
                        
                except Exception as e:
                    self.logger.error(f"Error buscando '{series['name']}': {e}")
                    self.log_signal.emit(f"      ❌ Error en búsqueda: {str(e)}")
                    continue
                
                time.sleep(2)  # Esperar entre búsquedas
            
            self.log_signal.emit(f"   ✅ {found}/{total_series} URLs encontradas para series con episodios faltantes")
            
        except Exception as e:
            self.logger.error(f"Error en search_hispashare: {e}")
            raise

    def search_hispashare_for_new_series(self, driver):
        """Buscar y guardar URL de Hispashare para series nuevas que no tienen episodios faltantes.
        Evita duplicar con las que ya se buscan en search_hispashare()."""
        try:
            searcher = HispashareSearcher(driver, self.db)

            # Series sin URL en general
            all_without_url = self.db.get_series_without_hispashare_url()

            # Series con episodios faltantes sin URL (ya se gestionan en search_hispashare)
            missing_without_url = self.db.get_series_with_missing_episodes_without_url()
            missing_ids = {s['id'] for s in missing_without_url}

            # Filtrar para quedarse con las "nuevas" (sin episodios faltantes)
            series_list = [s for s in all_without_url if s['id'] not in missing_ids]

            if not series_list:
                self.log_signal.emit("   ℹ️ No hay series nuevas sin URL que necesiten búsqueda en Hispashare")
                return

            self.log_signal.emit(f"   📊 Series nuevas sin URL a buscar: {len(series_list)}")

            found = 0
            for i, series in enumerate(series_list, 1):
                query, year = self._get_hispashare_search_query(series)
                label = series.get('tmdb_name') or series.get('name')
                self.log_signal.emit(f"   🔎 [NUEVAS {i}/{len(series_list)}] Buscando: {label} -> '{query}'...")

                try:
                    url = searcher.search_and_select(
                        query,
                        year,
                        series['id']
                    )

                    if url:
                        try:
                            self.db.update_hispashare_url(series['id'], url)
                            cover_url = self.db.derive_hispashare_cover_url(url)
                            if cover_url:
                                self.db.update_hispashare_cover_url(series['id'], cover_url)
                            found += 1
                            self.logger.info(f"URL guardada para '{series['name']}': {url}")
                            self.log_signal.emit(f"      ✅ URL encontrada y guardada: {url[:50]}...")
                            self.url_saved_signal.emit(series['id'], url)
                        except Exception as save_error:
                            self.logger.error(f"Error guardando URL para '{series['name']}': {save_error}")
                            self.log_signal.emit(f"      ⚠️ URL encontrada pero error al guardar: {str(save_error)}")
                    else:
                        self.log_signal.emit("      ❌ No se encontró (marcada para no buscar más)")

                except Exception as e:
                    self.logger.error(f"Error buscando '{series['name']}': {e}")
                    self.log_signal.emit(f"      ❌ Error en búsqueda: {str(e)}")
                    continue

                time.sleep(2)  # pequeña pausa entre búsquedas

            self.log_signal.emit(f"   ✅ {found}/{len(series_list)} URLs encontradas para series nuevas sin episodios faltantes")

        except Exception as e:
            self.logger.error(f"Error en search_hispashare_for_new_series: {e}")
            raise

    def process_downloads(self, driver):
        """Procesar descargas de episodios faltantes - solo series que realmente tienen episodios faltantes"""
        try:
            # Obtener solo series con episodios faltantes que tienen URL de Hispashare
            series_with_urls = self.db.get_series_with_missing_episodes_and_url()
            
            if not series_with_urls:
                self.log_signal.emit("   ℹ️ No hay series con episodios faltantes y URLs para procesar descargas")
                return
            
            downloader = HispashareDownloader(driver, self.db)
            total_links = 0
            
            self.log_signal.emit(f"   📋 Procesando {len(series_with_urls)} series con episodios faltantes...")
            
            for series in series_with_urls:
                if not self.running:
                    break
                
                try:
                    self.log_signal.emit(f"   🔍 Procesando: {series['name']} (tiene episodios faltantes)")
                    
                    # Obtener enlaces ed2k para episodios faltantes
                    ed2k_links = downloader.download_missing_episodes(series['id'])
                    
                    if ed2k_links:
                        total_links += len(ed2k_links)
                        self.log_signal.emit(f"      ✅ {len(ed2k_links)} enlaces ed2k obtenidos para episodios faltantes")
                    else:
                        self.log_signal.emit(f"      ℹ️ No se encontraron enlaces para los episodios faltantes")
                    
                except Exception as e:
                    self.logger.error(f"Error procesando serie '{series['name']}': {e}")
                    self.log_signal.emit(f"      ❌ Error procesando: {str(e)}")
                    continue
                
                time.sleep(1)  # Pequeña pausa entre series
            
            if total_links > 0:
                self.log_signal.emit(f"   ✅ Total: {total_links} enlaces ed2k obtenidos para episodios faltantes")
            else:
                self.log_signal.emit("   ℹ️ No se obtuvieron enlaces ed2k en este ciclo")
            
        except Exception as e:
            self.logger.error(f"Error en process_downloads: {e}")
            raise
    
    def scan_new_content(self, driver):
        """Escanear nuevo contenido en Hispashare"""
        try:
            # Verificar si el barrido de contenido está habilitado
            if not getattr(Settings, 'CONTENT_SCAN_ENABLED', True):
                self.log_signal.emit("   ℹ️ Barrido de nuevo contenido deshabilitado en configuración")
                return
            
            scanner = ContentScanner(driver, self.db)
            
            # 1. Escanear páginas de Hispashare
            self.log_signal.emit("   📄 Escaneando páginas de Hispashare...")
            new_content = scanner.scan_new_content()
            
            if not new_content:
                self.log_signal.emit("   ℹ️ No se encontró nuevo contenido que cumpla los filtros")
                return
            
            self.log_signal.emit(f"   ✅ {len(new_content)} elementos de nuevo contenido encontrados")
            
            # 2. Procesar contenido encontrado
            self.log_signal.emit("   🎬 Procesando nuevo contenido...")
            ed2k_links = scanner.process_new_content(new_content)
            
            if ed2k_links:
                self.log_signal.emit(f"   ✅ {len(ed2k_links)} enlaces ed2k obtenidos del nuevo contenido")
            else:
                self.log_signal.emit("   ℹ️ No se obtuvieron enlaces del nuevo contenido")
            
        except Exception as e:
            self.logger.error(f"Error en scan_new_content: {e}")
            self.log_signal.emit(f"   ❌ Error en barrido de contenido: {str(e)}")
            # No relanzar la excepción para no interrumpir el ciclo completo
            pass

    def stop(self):
        """Detener el scheduler"""
        self.logger.info("Deteniendo scheduler...")
        self.running = False
        
        # Cerrar driver activo si existe
        if self.current_driver:
            try:
                self.logger.info("Cerrando driver de Selenium...")
                self.current_driver.quit()
                self.current_driver = None
                self.logger.info("Driver cerrado correctamente")
            except Exception as e:
                self.logger.error(f"Error cerrando driver en stop(): {e}")
        
        self.logger.info("Scheduler marcado para detenerse")
