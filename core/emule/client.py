import requests
import logging
from urllib.parse import quote
from typing import List, Optional, Dict, Any
from config.settings import Settings

class EmuleClient:
    """Cliente para integración con el servidor web de eMule"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.settings = Settings()
        
    def is_configured(self) -> bool:
        """Verificar si eMule está configurado correctamente"""
        try:
            emule_config = self.settings.get_emule_config()
            # URL y username son obligatorios, password puede estar vacío
            return all([
                emule_config.get('url'),
                emule_config.get('username')
            ])
        except Exception:
            return False
    
    def test_connection(self) -> Dict[str, Any]:
        """Probar la conexión con el servidor web de eMule"""
        try:
            if not self.is_configured():
                return {
                    'success': False,
                    'error': 'eMule no está configurado correctamente'
                }
            
            emule_config = self.settings.get_emule_config()
            url = emule_config['url'].rstrip('/')
            username = emule_config['username']
            password = emule_config['password']
            
            # Hacer una petición simple para probar la conexión
            response = requests.get(
                f"{url}/",
                auth=(username, password),
                timeout=10
            )
            
            if response.status_code == 200:
                self.logger.info("Conexión con eMule establecida correctamente")
                return {
                    'success': True,
                    'message': 'Conexión exitosa con eMule'
                }
            else:
                error_msg = f"Error de conexión: HTTP {response.status_code}"
                self.logger.error(error_msg)
                return {
                    'success': False,
                    'error': error_msg
                }
                
        except requests.exceptions.ConnectionError:
            error_msg = "No se pudo conectar con eMule. Verifica que esté ejecutándose y el servidor web habilitado."
            self.logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
        except requests.exceptions.Timeout:
            error_msg = "Timeout al conectar con eMule"
            self.logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
        except Exception as e:
            error_msg = f"Error inesperado: {str(e)}"
            self.logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
    
    def add_ed2k_link(self, ed2k_link: str, category: int = 0) -> Dict[str, Any]:
        """
        Añadir un enlace ed2k a eMule
        
        Args:
            ed2k_link: El enlace ed2k a añadir
            category: Categoría en eMule (0 = sin categoría)
            
        Returns:
            Dict con el resultado de la operación
        """
        try:
            if not self.is_configured():
                return {
                    'success': False,
                    'error': 'eMule no está configurado correctamente'
                }
            
            if not ed2k_link.startswith('ed2k://'):
                return {
                    'success': False,
                    'error': 'El enlace no es un enlace ed2k válido'
                }
            
            emule_config = self.settings.get_emule_config()
            base_url = emule_config['url'].rstrip('/')
            username = emule_config['username']
            password = emule_config['password']
            
            # Codificar el enlace ed2k
            encoded_link = quote(ed2k_link, safe='')
            
            # Construir la URL de la petición usando el formato correcto de eMule
            # Formato: http://server:port/?w=password&p=password&cat=category&c=ed2klink
            url = f"{base_url}/?w=password&p={password}&cat={category}&c={encoded_link}"
            
            # Log de debug para la URL construida
            self.logger.debug(f"🔗 URL construida para eMule: {url}")
            
            # Enviar la petición
            try:
                response = requests.get(
                    url,
                    auth=(username, password),
                    timeout=30
                )
            except requests.exceptions.RequestException as e:
                error_msg = f"Error de red al añadir enlace a eMule: {str(e)}"
                self.logger.error(error_msg)
                return {
                    'success': False,
                    'error': error_msg
                }
            
            # Log detallado de la respuesta
            self.logger.debug(f"📡 Respuesta HTTP: {response.status_code}")
            self.logger.debug(f"📄 Contenido de respuesta: {response.text[:200]}...")
            
            if response.status_code == 200:
                # Verificar si la respuesta contiene indicadores de éxito
                response_text = response.text.lower()
                # Buscar indicadores específicos de eMule
                success_indicators = [
                    'result="ok"',  # Respuesta XML de éxito
                    'enlace añadido',  # Mensaje en español
                    'enlace aÃ±adido',  # Mensaje con encoding
                    'link added',  # Mensaje en inglés
                    'added',
                    'añadido'
                ]
                
                if any(indicator in response_text for indicator in success_indicators):
                    self.logger.info(f"✅ Enlace ed2k añadido exitosamente: {ed2k_link[:50]}...")
                    return {
                        'success': True,
                        'message': 'Enlace añadido a eMule correctamente'
                    }
                else:
                    self.logger.warning(f"⚠️ Respuesta HTTP 200 pero contenido sospechoso: {response.text[:100]}...")
                    return {
                        'success': False,
                        'error': 'Respuesta inesperada de eMule'
                    }
            else:
                error_msg = f"Error al añadir enlace: HTTP {response.status_code}"
                self.logger.error(error_msg)
                return {
                    'success': False,
                    'error': error_msg
                }
                
        except Exception as e:
            error_msg = f"Error al añadir enlace ed2k: {str(e)}"
            self.logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
    
    def add_multiple_ed2k_links(self, ed2k_links: List[str], category: int = 0) -> Dict[str, Any]:
        """
        Añadir múltiples enlaces ed2k a eMule
        
        Args:
            ed2k_links: Lista de enlaces ed2k
            category: Categoría en eMule
            
        Returns:
            Dict con estadísticas del resultado
        """
        if not ed2k_links:
            return {
                'success': True,
                'added': 0,
                'failed': 0,
                'total': 0,
                'message': 'No hay enlaces para procesar'
            }
        
        added_count = 0
        failed_count = 0
        errors = []
        
        self.logger.info(f"Procesando {len(ed2k_links)} enlaces ed2k...")
        self.logger.debug(f"Enlaces a procesar: {[link[:50] + '...' if len(link) > 50 else link for link in ed2k_links]}")
        
        for i, link in enumerate(ed2k_links, 1):
            try:
                self.logger.debug(f"Procesando enlace {i}/{len(ed2k_links)}: {link[:80]}...")
                result = self.add_ed2k_link(link.strip(), category)
                
                if result['success']:
                    added_count += 1
                    self.logger.info(f"✅ Enlace {i}/{len(ed2k_links)} añadido correctamente a eMule")
                else:
                    failed_count += 1
                    error_msg = f"❌ Enlace {i}: {result.get('error', 'Error desconocido')}"
                    errors.append(error_msg)
                    self.logger.warning(error_msg)
                
                # Pequeña pausa entre enlaces para no sobrecargar eMule
                if i < len(ed2k_links):
                    import time
                    time.sleep(0.5)
                    
            except Exception as e:
                failed_count += 1
                error_msg = f"❌ Enlace {i}: Error inesperado - {str(e)}"
                errors.append(error_msg)
                self.logger.error(error_msg)
        
        success_rate = (added_count / len(ed2k_links)) * 100 if ed2k_links else 0
        
        result = {
            'success': added_count > 0,
            'added': added_count,
            'failed': failed_count,
            'total': len(ed2k_links),
            'success_rate': round(success_rate, 1),
            'errors': errors[:10]  # Limitar a 10 errores para no saturar
        }
        
        if added_count > 0:
            result['message'] = f"Se añadieron {added_count}/{len(ed2k_links)} enlaces a eMule"
            self.logger.info(f"🎉 Proceso completado exitosamente: {added_count} añadidos, {failed_count} fallidos")
        else:
            result['message'] = "No se pudo añadir ningún enlace a eMule"
            self.logger.warning(f"⚠️ Proceso completado con errores: {added_count} añadidos, {failed_count} fallidos")
        
        return result
    
    def extract_ed2k_links_from_text(self, text: str) -> List[str]:
        """
        Extraer enlaces ed2k de un texto
        
        Args:
            text: Texto que contiene enlaces ed2k
            
        Returns:
            Lista de enlaces ed2k encontrados
        """
        import re
        
        # Patrón para encontrar enlaces ed2k
        pattern = r'ed2k://\|file\|[^|]+\|\d+\|[A-Fa-f0-9]{32}\|(?:/)?'
        
        links = re.findall(pattern, text)
        
        # Limpiar y validar enlaces
        clean_links = []
        for link in links:
            link = link.strip()
            if link and link.startswith('ed2k://'):
                clean_links.append(link)
        
        self.logger.info(f"Extraídos {len(clean_links)} enlaces ed2k del texto")
        return clean_links