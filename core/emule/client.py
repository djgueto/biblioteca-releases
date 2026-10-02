import logging
import re
import time
from typing import List, Dict, Any

import requests

from config.settings import Settings


class EmuleClient:
    """Cliente para integración con el servidor web de aMule/eMule."""

    def __init__(self, url: str | None = None, username: str | None = None, password: str | None = None):
        self.logger = logging.getLogger(__name__)
        self.settings = Settings()
        self.url = url
        self.username = username
        self.password = password

    def _get_config(self) -> Dict[str, Any]:
        emule_config = self.settings.get_emule_config()
        return {
            'url': (self.url if self.url is not None else emule_config.get('url', '')).strip(),
            'username': (self.username if self.username is not None else emule_config.get('username', 'admin')).strip() or 'admin',
            'password': self.password if self.password is not None else emule_config.get('password', '')
        }

    def _build_url(self, path: str) -> str:
        base_url = self._get_config()['url'].rstrip('/')
        if not path.startswith('/'):
            path = f'/{path}'
        return f"{base_url}{path}"

    def _login(self, session: requests.Session | None = None) -> tuple[bool, requests.Session, str]:
        session = session or requests.Session()
        config = self._get_config()
        base_url = config['url'].rstrip('/')
        password = config['password']

        if not base_url:
            return False, session, 'URL del servidor web no configurada'

        if password is None or str(password) == "":
            return False, session, 'La contraseña del servidor web de aMule está vacía'

        try:
            session.get(f"{base_url}/", timeout=10)
            response = session.post(
                f"{base_url}/",
                data={'pass': password, 'submit': 'Submit'},
                timeout=10,
            )
        except requests.exceptions.ConnectionError:
            return False, session, 'No se pudo conectar con aMule. Verifica que esté ejecutándose y el servidor web habilitado.'
        except requests.exceptions.Timeout:
            return False, session, 'Timeout al conectar con aMule'
        except requests.exceptions.RequestException as exc:
            return False, session, f'Error de red al conectar con aMule: {exc}'

        body = response.text or ''
        has_session_cookie = 'amuleweb_session_id' in session.cookies.get_dict()
        login_failed = 'Enter password' in body
        redirected = 'amuleweb-main-dload.php' in body

        if response.status_code == 200 and not login_failed and (has_session_cookie or redirected):
            return True, session, 'Conexión exitosa con aMule'

        return False, session, 'aMule rechazó la contraseña del servidor web'

    def _extract_ed2k_metadata(self, ed2k_link: str) -> Dict[str, str]:
        match = re.match(r'ed2k://\|file\|([^|]+)\|\d+\|([A-Fa-f0-9]{32})\|', ed2k_link)
        if not match:
            return {'filename': '', 'hash': ''}
        return {
            'filename': match.group(1),
            'hash': match.group(2)
        }

    def _queue_contains_link(
        self,
        session: requests.Session,
        link_meta: Dict[str, str],
        timeout: int = 30,
        attempts: int = 4,
        wait_seconds: float = 1.2,
    ) -> bool:
        filename = link_meta['filename']
        file_hash = link_meta['hash']

        for attempt in range(1, attempts + 1):
            dload_response = session.get(self._build_url('/amuleweb-main-dload.php'), timeout=timeout)
            page_text = dload_response.text or ''
            found = (
                dload_response.status_code == 200 and
                (
                    (filename and filename in page_text) or
                    (file_hash and file_hash in page_text)
                )
            )
            if found:
                return True
            if attempt < attempts:
                time.sleep(wait_seconds)
        return False

    def _response_suggests_success(self, response_text: str, link_meta: Dict[str, str]) -> bool:
        text = (response_text or '').lower()
        filename = (link_meta.get('filename') or '').lower()
        file_hash = (link_meta.get('hash') or '').lower()

        success_tokens = [
            'download',
            'downloading',
            'transfer',
            'added',
            'añad',
            'cola',
            'queue',
        ]
        error_tokens = [
            'enter password',
            'wrong password',
            'incorrect password',
            'invalid',
            'malformed',
            'error',
        ]

        if any(token in text for token in error_tokens):
            return False
        if filename and filename in text:
            return True
        if file_hash and file_hash in text:
            return True
        return any(token in text for token in success_tokens)

    def _add_ed2k_link_with_session(self, session: requests.Session, ed2k_link: str, verify_queue: bool = True) -> Dict[str, Any]:
        link_meta = self._extract_ed2k_metadata(ed2k_link)

        try:
            response = session.post(
                self._build_url('/footer.php'),
                data={
                    'ed2klink': ed2k_link,
                    'selectcat': 'all',
                    'Submit': 'Download link',
                },
                timeout=20,
            )
        except requests.exceptions.Timeout:
            return {
                'success': False,
                'error': 'Timeout al enviar enlace a aMule'
            }
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'error': f'Error de red al añadir enlace a aMule: {str(e)}'
            }

        self.logger.debug(f"Respuesta HTTP footer.php: {response.status_code}")
        response_text = response.text or ''

        if response.status_code != 200:
            return {
                'success': False,
                'error': f'HTTP {response.status_code} al enviar enlace a aMule'
            }

        if not verify_queue:
            return {
                'success': True,
                'message': 'Enlace enviado a aMule correctamente'
            }

        try:
            found_in_queue = self._queue_contains_link(session, link_meta, timeout=20)
        except requests.exceptions.Timeout:
            return {
                'success': False,
                'error': 'Timeout verificando la cola de aMule'
            }
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'error': f'Error verificando la cola de aMule: {str(e)}'
            }

        if found_in_queue:
            return {
                'success': True,
                'message': 'Enlace añadido a aMule correctamente'
            }

        if self._response_suggests_success(response_text, link_meta):
            return {
                'success': True,
                'message': 'Enlace enviado a aMule; la cola no se pudo verificar de inmediato',
                'verified': False,
            }

        return {
            'success': False,
            'error': 'aMule no confirmó el enlace en la cola de descargas'
        }

    def get_active_download_page(self) -> str | None:
        """Descarga una vez el HTML de la cola de descargas activa de aMule.

        Se usa para comprobar en bloque, sin una petición por enlace, si un
        enlace histórico sigue realmente en curso antes de reintentarlo.
        Devuelve None si aMule no está disponible o falla el login.
        """
        try:
            if not self.is_configured():
                return None
            success, session, message = self._login()
            if not success:
                self.logger.warning(f"No se pudo verificar la cola de aMule: {message}")
                return None
            response = session.get(self._build_url('/amuleweb-main-dload.php'), timeout=15)
            if response.status_code != 200:
                return None
            return response.text or ''
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Error consultando la cola de aMule: {e}")
            return None

    def is_link_active_in_queue(self, ed2k_link: str, queue_page_text: str) -> bool:
        """Comprueba si `ed2k_link` aparece en el HTML de cola ya descargado
        (ver `get_active_download_page`), por nombre de archivo o hash."""
        if not queue_page_text:
            return False
        link_meta = self._extract_ed2k_metadata(ed2k_link)
        filename = link_meta['filename']
        file_hash = link_meta['hash']
        return bool((filename and filename in queue_page_text) or (file_hash and file_hash in queue_page_text))

    def is_configured(self) -> bool:
        """Verificar si aMule está configurado correctamente."""
        try:
            emule_config = self._get_config()
            return bool(emule_config.get('url'))
        except Exception:
            return False

    def test_connection(self) -> Dict[str, Any]:
        """Probar la conexión con el servidor web de aMule."""
        try:
            if not self.is_configured():
                return {
                    'success': False,
                    'error': 'aMule no está configurado correctamente'
                }

            success, session, message = self._login()
            if not success:
                self.logger.error(message)
                return {
                    'success': False,
                    'error': message
                }

            response = session.get(self._build_url('/amuleweb-main-dload.php'), timeout=10)
            if response.status_code == 200 and 'DOWNLOAD' in response.text.upper():
                self.logger.info("Conexión con aMule establecida correctamente")
                return {
                    'success': True,
                    'message': 'Conexión exitosa con aMule'
                }

            error_msg = f"Conexión establecida pero la interfaz devolvió HTTP {response.status_code}"
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
        Añadir un enlace ed2k a aMule.

        Args:
            ed2k_link: El enlace ed2k a añadir.
            category: Categoría lógica, actualmente no usada por el formulario web de aMule.

        Returns:
            Dict con el resultado de la operación.
        """
        try:
            if not self.is_configured():
                return {
                    'success': False,
                    'error': 'aMule no está configurado correctamente'
                }

            if not ed2k_link.startswith('ed2k://'):
                return {
                    'success': False,
                    'error': 'El enlace no es un enlace ed2k válido'
                }

            success, session, message = self._login()
            if not success:
                self.logger.error(message)
                return {
                    'success': False,
                    'error': message
                }

            result = self._add_ed2k_link_with_session(session, ed2k_link, verify_queue=True)
            if result['success']:
                self.logger.info(f"Enlace ed2k añadido correctamente a aMule: {ed2k_link[:80]}...")
            else:
                self.logger.warning(f"aMule no confirmó el enlace: {ed2k_link[:80]}")
            return result

        except Exception as e:
            error_msg = f"Error al añadir enlace ed2k: {str(e)}"
            self.logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }

    def add_multiple_ed2k_links(self, ed2k_links: List[str], category: int = 0) -> Dict[str, Any]:
        """
        Añadir múltiples enlaces ed2k a aMule.

        Args:
            ed2k_links: Lista de enlaces ed2k.
            category: Categoría lógica en la llamada.

        Returns:
            Dict con estadísticas del resultado.
        """
        if not ed2k_links:
            return {
                'success': True,
                'added': 0,
                'failed': 0,
                'total': 0,
                'added_links': [],
                'failed_links': [],
                'message': 'No hay enlaces para procesar'
            }

        added_count = 0
        failed_count = 0
        errors = []
        added_links = []
        failed_links = []

        self.logger.info(f"Procesando {len(ed2k_links)} enlaces ed2k...")
        self.logger.debug(f"Enlaces a procesar: {[link[:50] + '...' if len(link) > 50 else link for link in ed2k_links]}")

        success, session, message = self._login()
        if not success:
            self.logger.error(message)
            return {
                'success': False,
                'added': 0,
                'failed': len(ed2k_links),
                'total': len(ed2k_links),
                'success_rate': 0.0,
                'errors': [message],
                'added_links': [],
                'failed_links': list(ed2k_links),
                'message': 'No se pudo añadir ningún enlace a aMule'
            }

        for i, link in enumerate(ed2k_links, 1):
            try:
                self.logger.debug(f"Procesando enlace {i}/{len(ed2k_links)}: {link[:80]}...")
                verify_queue = (i == 1 or i == len(ed2k_links) or i % 10 == 0)

                result = self._add_ed2k_link_with_session(session, link.strip(), verify_queue=verify_queue)
                if not result['success'] and 'Timeout' in result.get('error', ''):
                    self.logger.warning(f"Reintentando enlace {i} tras timeout...")
                    success, session, message = self._login()
                    if success:
                        result = self._add_ed2k_link_with_session(session, link.strip(), verify_queue=verify_queue)
                    else:
                        result = {
                            'success': False,
                            'error': message
                        }

                if result['success']:
                    added_count += 1
                    added_links.append(link)
                    self.logger.info(f"Enlace {i}/{len(ed2k_links)} añadido correctamente a aMule")
                else:
                    failed_count += 1
                    failed_links.append(link)
                    error_msg = f"Enlace {i}: {result.get('error', 'Error desconocido')}"
                    errors.append(error_msg)
                    self.logger.warning(error_msg)

                if i < len(ed2k_links):
                    import time
                    time.sleep(0.5)

            except Exception as e:
                failed_count += 1
                failed_links.append(link)
                error_msg = f"Enlace {i}: Error inesperado - {str(e)}"
                errors.append(error_msg)
                self.logger.error(error_msg)

        success_rate = (added_count / len(ed2k_links)) * 100 if ed2k_links else 0

        result = {
            'success': added_count > 0,
            'added': added_count,
            'failed': failed_count,
            'total': len(ed2k_links),
            'success_rate': round(success_rate, 1),
            'errors': errors[:10],
            'added_links': added_links,
            'failed_links': failed_links
        }

        if added_count > 0:
            result['message'] = f"Se añadieron {added_count}/{len(ed2k_links)} enlaces a aMule"
            self.logger.info(f"Proceso completado exitosamente: {added_count} añadidos, {failed_count} fallidos")
        else:
            if errors:
                result['message'] = f"No se pudo añadir ningún enlace a aMule. {errors[0]}"
            else:
                result['message'] = "No se pudo añadir ningún enlace a aMule"
            self.logger.warning(f"Proceso completado con errores: {added_count} añadidos, {failed_count} fallidos")

        return result

    def extract_ed2k_links_from_text(self, text: str) -> List[str]:
        """
        Extraer enlaces ed2k de un texto.

        Args:
            text: Texto que contiene enlaces ed2k.

        Returns:
            Lista de enlaces ed2k encontrados.
        """
        pattern = r'ed2k://\|file\|[^|]+\|\d+\|[A-Fa-f0-9]{32}\|(?:/)?'

        links = re.findall(pattern, text)

        clean_links = []
        for link in links:
            link = link.strip()
            if link and link.startswith('ed2k://'):
                clean_links.append(link)

        self.logger.info(f"Extraídos {len(clean_links)} enlaces ed2k del texto")
        return clean_links
