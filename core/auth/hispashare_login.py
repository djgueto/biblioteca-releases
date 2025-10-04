from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import logging
import time


class HispashareAuth:
    def __init__(self, username, password, headless=False):
        self.username = username
        self.password = password
        self.headless = headless
        self.logger = logging.getLogger(__name__)
        self.driver = None
        self.base_url = "https://www.hispashare.org"
    
    def init_driver(self):
        """Inicializar driver de Selenium"""
        options = Options()
        
        if self.headless:
            options.add_argument('--headless=new')
        
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        # Desactivar notificaciones
        prefs = {
            "profile.default_content_setting_values.notifications": 2
        }
        options.add_experimental_option("prefs", prefs)
        
        try:
            # Usar webdriver-manager para ChromeDriver
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.implicitly_wait(10)
        except Exception as e:
            self.logger.error(f"Error inicializando ChromeDriver: {e}")
            # Intentar con driver del sistema si existe
            try:
                self.logger.info("Intentando usar ChromeDriver del sistema...")
                self.driver = webdriver.Chrome(options=options)
                self.driver.implicitly_wait(10)
            except Exception as e2:
                self.logger.error(f"Error con ChromeDriver del sistema: {e2}")
                raise Exception(f"No se pudo inicializar ChromeDriver: {e}")
    
    def login(self):
        """Iniciar sesión en Hispashare"""
        try:
            if not self.driver:
                self.init_driver()
            
            self.logger.info("Accediendo a Hispashare...")
            self.driver.get(self.base_url)
            
            # Esperar a que cargue la página
            time.sleep(2)
            
            # Rellenar formulario de login directamente (ya estamos en la página de login)
            self.logger.info("Rellenando formulario de login...")
            username_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            username_field.clear()
            username_field.send_keys(self.username)
            
            password_field = self.driver.find_element(By.NAME, "password")
            password_field.clear()
            password_field.send_keys(self.password)
            
            # Esperar un momento para asegurar que los campos estén completados
            time.sleep(1)
            
            # Buscar y hacer clic en el botón de "Iniciar sesión"
            try:
                # Intentar encontrar el botón por el texto "Iniciar sesión"
                submit_button = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[@class='BUTTON' and contains(., 'Iniciar sesión')]"))
                )
                submit_button.click()
                self.logger.info("Botón 'Iniciar sesión' pulsado correctamente")
            except:
                # Fallback: intentar con otros selectores posibles
                try:
                    submit_button = self.driver.find_element(By.CSS_SELECTOR, "a.BUTTON")
                    submit_button.click()
                    self.logger.info("Botón de login encontrado con selector alternativo")
                except:
                    # Último recurso: ejecutar el JavaScript directamente
                    self.driver.execute_script("form_login.submit();")
                    self.logger.info("Formulario enviado mediante JavaScript")
            
            # Esperar a que se complete el login
            time.sleep(3)
            
            # Verificar login exitoso
            if self.is_logged_in():
                self.logger.info("Login exitoso en Hispashare")
                return True
            else:
                self.logger.error("Login fallido - credenciales incorrectas")
                return False
                
        except Exception as e:
            self.logger.error(f"Error en login: {e}", exc_info=True)
            return False
    
    def is_logged_in(self):
        """Verificar si la sesión está iniciada"""
        try:
            # Buscar elementos que solo aparecen cuando estás logueado
            # Por ejemplo, el nombre de usuario o un enlace de "Cerrar sesión"
            self.driver.find_element(By.LINK_TEXT, "Cerrar sesión")
            return True
        except:
            return False
    
    def close(self):
        """Cerrar el navegador"""
        if self.driver:
            self.driver.quit()
            self.driver = None