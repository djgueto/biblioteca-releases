from PyQt6.QtCore import QThread, pyqtSignal
import time
import logging
from datetime import datetime

from core.auth.hispashare_login import HispashareAuth
from core.library.scanner import LibraryScanner
from core.tmdb.api_client import TMDBClient
from core.tmdb.auto_matcher import TMDBAutoMatcher
from core.hispashare.searcher import HispashareSearcher
from core.database.db_manager import DatabaseManager
from config.settings import Settings


class SchedulerThread(QThread):
    progress_signal = pyqtSignal(int, str)
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)
    url_saved_signal = pyqtSignal(int, str)  # Nueva señal: series_id, url
    
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
            
            # 2. Escanear biblioteca
            self.progress_signal.emit(15, "📁 Escaneando biblioteca...")
            self.log_signal.emit("📁 Escaneando biblioteca local...")
            series_list = self.scan_library()
            self.log_signal.emit(f"   ✅ {len(series_list)} series encontradas")
            
            # 3. Obtener series de la base de datos para vincular con TMDB
            self.progress_signal.emit(35, "🎬 Vinculando con TMDB...")
            self.log_signal.emit("🎬 Vinculando series con TMDB...")
            db_series_list = self.db.get_all_series()
            self.match_tmdb(db_series_list)
            
            # 4. Detectar episodios faltantes
            self.progress_signal.emit(55, "🔍 Detectando faltantes...")
            self.log_signal.emit("🔍 Detectando episodios faltantes...")
            missing_count = self.detect_missing()
            self.log_signal.emit(f"   ✅ {missing_count} episodios faltantes detectados")
            
            # 5. Buscar en Hispashare
            self.progress_signal.emit(75, "🔎 Buscando en Hispashare...")
            self.log_signal.emit("🔎 Buscando URLs en Hispashare...")
            self.search_hispashare(driver)
            
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
            scanner = LibraryScanner(Settings.SERIES_PATH)
            series_list = scanner.scan()
            
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
                # Obtener episodios de TMDB
                tmdb_episodes = tmdb_client.get_all_episodes(series['tmdb_id'])
                
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
    
    def search_hispashare(self, driver):
        """Buscar series en Hispashare"""
        try:
            searcher = HispashareSearcher(driver)
            series_list = self.db.get_series_without_hispashare_url()
            
            found = 0
            total_series = len(series_list)
            
            self.log_signal.emit(f"   📊 Total de series a buscar: {total_series}")
            
            for i, series in enumerate(series_list, 1):
                self.log_signal.emit(f"   🔎 [{i}/{total_series}] Buscando: {series['name']}...")
                
                try:
                    url = searcher.search_and_select(
                        series['name'],
                        series.get('year')
                    )
                    
                    if url:
                        # Guardar URL inmediatamente cuando se encuentra
                        try:
                            self.db.update_hispashare_url(series['id'], url)
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
                        self.log_signal.emit(f"      ❌ No se encontró")
                        
                except Exception as e:
                    self.logger.error(f"Error buscando '{series['name']}': {e}")
                    self.log_signal.emit(f"      ❌ Error en búsqueda: {str(e)}")
                    continue
                
                time.sleep(2)  # Esperar entre búsquedas
            
            self.log_signal.emit(f"   ✅ {found}/{total_series} URLs encontradas y guardadas en Hispashare")
            
        except Exception as e:
            self.logger.error(f"Error en search_hispashare: {e}")
            raise
    
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