# -*- coding: utf-8 -*-
"""
Descargador wolfmax4k.com integrado como módulo.

Flujo actual del sitio (rediseño 2026-09):
  1. POST /api/descargas {action: generate, code, tabla} → challenge
  2. Prueba de trabajo SHA-256 (sha256(challenge + nonce) con N nibbles a 0)
  3. POST /api/descargas {action: validate, challenge, nonce} → download_url
     - external=false → URL directa /torrents/<tabla>/<archivo>.torrent
     - external=true  → enlacito.com/s.php?i=...; esa página expone
       `var link_out` (sin formulario de espera) que, descifrado con
       GibberishAES, da la URL directa /torrents/... en wolfmax4k.com
  4. Se descarga el .torrent con fetch() dentro del navegador: wolfmax4k.com
     no es accesible con requests (bloqueo ISP / certificado interceptado).
"""
import re
import json
import time
import base64
import hashlib
import logging
from curl_cffi import requests as cffi_requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pathlib import Path
from urllib.parse import urlparse, unquote, quote_plus
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

from core.browser import new_system_chrome, system_chrome_configured

# Librería AES que usa la propia web de Wolfmax para descifrar sus enlaces.
_GIBBERISH_JS = Path(__file__).with_name('gibberish-aes.js')
_REAL_KEY = 'fee631d2cffda38a78b96ee6d2dfb43a'

BASE_URL = 'https://wolfmax4k.com'
_API_BASE = 'https://wolftorrent.com'
_API_URL = _API_BASE + '/api/descargas'

# Sección del sitio → valor `tabla` que espera /api/descargas
_SECTION_TABLA = {
    'pelicula': 'peliculas',
    'serie': 'series',
    'documental': 'documentales',
    'animacion': 'animaciones',
    'telenovela': 'telenovelas',
}
_SECTIONS_RE = '|'.join(_SECTION_TABLA)

# Ficha de temporada/película: /serie/3bu8vw, /pelicula/rywt7n
CONTENT_URL_RE = re.compile(rf'^https://wolfmax4k\.com/({_SECTIONS_RE})/([a-z0-9]+)/?$')
# Ficha de episodio: /serie/episodio/5w7pb8
EPISODE_URL_RE = re.compile(rf'^https://wolfmax4k\.com/({_SECTIONS_RE})/episodio/([a-z0-9]+)/?$')

_EP_LABEL_RE = re.compile(r'(\d{1,2})x(\d{1,3})(?:\s*al\s*(?:\d{1,2}x)?(\d{1,3}))?', re.IGNORECASE)
_SEASON_TITLE_RE = re.compile(r'^(.*?)\s*-?\s*(\d{1,2})\s*[ªº]?\s*Temporada\b\s*(.*)$', re.IGNORECASE)

_POW_DIFFICULTY = 3

_POST_JS = r"""
const done = arguments[arguments.length - 1];
fetch('/api/descargas', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: arguments[0]})
  .then(async r => { let d = null; try { d = await r.json(); } catch (_) {} done({status: r.status, data: d}); },
        e => done({status: 0, error: String(e)}));
"""

_FETCH_B64_JS = r"""
const done = arguments[arguments.length - 1];
fetch(arguments[0], {credentials: 'include'}).then(async r => {
  const buf = new Uint8Array(await r.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  done({status: r.status, b64: btoa(s)});
}, e => done({status: 0, error: String(e)}));
"""

logger = logging.getLogger(__name__)


class WolfmaxRateLimited(RuntimeError):
    """El sitio ha aplicado su límite de descargas por hora."""

    def __init__(self, wait_minutes: int):
        super().__init__(f'wolfmax: límite de descargas alcanzado, esperar {wait_minutes} min')
        self.wait_minutes = wait_minutes


class WolfmaxVerificationRequired(RuntimeError):
    """El sitio exige una verificación de persona (captcha/Turnstile) para
    descargar: ninguna descarga automática va a funcionar."""

    def __init__(self):
        super().__init__('wolfmax exige verificación de persona (Cloudflare Turnstile) para descargar')


def _make_driver():
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    driver = new_system_chrome(opts) if system_chrome_configured() else webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd(
        'Page.addScriptToEvaluateOnNewDocument',
        {'source': 'Object.defineProperty(navigator,"webdriver",{get:()=>undefined});'},
    )
    driver.set_script_timeout(60)
    return driver


def normalize_url(href: str) -> str:
    """Convierte hrefs relativos/protocol-relative a URL absoluta sin barra final."""
    href = (href or '').strip()
    if href.startswith('//'):
        href = 'https:' + href
    elif href.startswith('/'):
        href = BASE_URL + href
    return href.rstrip('/')


def is_current_url(url: str) -> bool:
    """True si la URL tiene el formato del sitio actual (las del diseño
    anterior, p. ej. /movie/267205 o /series/4k-2160p/x, ya no existen)."""
    url = normalize_url(url)
    return bool(CONTENT_URL_RE.match(url) or EPISODE_URL_RE.match(url))


def parse_episode_label(label: str) -> tuple[int, list[int]] | None:
    """'Episodio 1x06 al 1x09.' → (1, [6, 7, 8, 9]); '4x08' → (4, [8])."""
    m = _EP_LABEL_RE.search(label or '')
    if not m:
        return None
    season, first = int(m.group(1)), int(m.group(2))
    last = int(m.group(3)) if m.group(3) else first
    if last < first or last - first > 50:
        last = first
    return season, list(range(first, last + 1))


def split_season_title(title: str) -> tuple[str, int | None, str]:
    """'Ted Lasso - 4ª Temporada [480p]' → ('Ted Lasso', 4, '[480p]')."""
    m = _SEASON_TITLE_RE.match((title or '').strip())
    if not m:
        return (title or '').strip(), None, ''
    return m.group(1).strip(' -'), int(m.group(2)), m.group(3).strip()


def _solve_pow(challenge: str, difficulty: int = _POW_DIFFICULTY) -> int:
    full, half = difficulty // 2, difficulty % 2
    nonce = 0
    while True:
        h = hashlib.sha256(f'{challenge}{nonce}'.encode()).digest()
        if not any(h[:full]) and (not half or (h[full] & 0xF0) == 0):
            return nonce
        nonce += 1


def _ensure_on_site(driver):
    if not (driver.current_url or '').startswith(BASE_URL):
        driver.get(BASE_URL + '/')
        time.sleep(3)


def _api_post(driver, payload: dict) -> dict:
    res = driver.execute_async_script(_POST_JS, json.dumps(payload))
    data = res.get('data') or {}
    if data.get('status') == 'limit_exceeded':
        raise WolfmaxRateLimited(int(data.get('wait_minutes') or 60))
    if data.get('status') in ('captcha_required', 'turnstile_required'):
        # Desde 2026-10-10 el sitio pide Cloudflare Turnstile (verificación
        # de persona) para descargar. No se intenta saltar: se avisa y el
        # proveedor deja de descargar en este ciclo.
        raise WolfmaxVerificationRequired()
    if res.get('status') != 200 or not data.get('success'):
        raise RuntimeError(
            f"wolfmax /api/descargas {payload.get('action')}: HTTP {res.get('status')} "
            f"{data.get('error') or res.get('error') or ''}".strip()
        )
    return data


def _request_download_url(driver, code: str, tabla: str) -> tuple[str, bool]:
    """Pide al API el enlace de descarga. Devuelve (url, external)."""
    _ensure_on_site(driver)
    gen = _api_post(driver, {'action': 'generate', 'code': code, 'tabla': tabla})
    nonce = _solve_pow(gen['challenge'])
    val = _api_post(driver, {'action': 'validate', 'challenge': gen['challenge'], 'nonce': nonce})
    return val['download_url'], val.get('external') is True


def _enlacito_link_out(driver, enlacito_url: str) -> str:
    """Abre enlacito desde una página de wolfmax (necesita el Referer del
    sitio de origen) y lee `link_out` del HTML."""
    _ensure_on_site(driver)
    driver.execute_script('window.location.href = arguments[0];', enlacito_url)
    for _ in range(15):
        time.sleep(1)
        m = re.search(r'var\s+link_out\s*=\s*["\']([^"\']+)["\']', driver.page_source)
        if m:
            return m.group(1)
    text = BeautifulSoup(driver.page_source, 'html.parser').get_text(' ', strip=True)[:150]
    raise RuntimeError(f'No se encontró link_out en enlacito: {text}')


def _decrypt_link_out(driver, link_out):
    if not _GIBBERISH_JS.exists():
        raise RuntimeError(f'Falta gibberish-aes.js en {_GIBBERISH_JS}')
    gibberish_js = _GIBBERISH_JS.read_text(encoding='utf-8')
    driver.get('about:blank')
    time.sleep(0.3)
    driver.execute_script(gibberish_js)
    js = f"""
    try {{
        var inner = atob("{link_out}");
        var url   = GibberishAES.dec(inner, "{_REAL_KEY}");
        return JSON.stringify({{ok: true, url: url}});
    }} catch(e) {{
        try {{
            var url2 = GibberishAES.dec("{link_out}", "{_REAL_KEY}");
            return JSON.stringify({{ok: true, url: url2}});
        }} catch(e2) {{
            return JSON.stringify({{ok: false, err: String(e)}});
        }}
    }}
    """
    raw = driver.execute_script(js)
    result = json.loads(raw)
    if not result.get('ok'):
        raise RuntimeError(f'Descifrado fallido: {result.get("err")}')
    return result['url']


def _resolve_torrent_url(driver, wolfmax_url: str) -> str:
    url = normalize_url(wolfmax_url)
    m = EPISODE_URL_RE.match(url) or CONTENT_URL_RE.match(url)
    if not m:
        raise RuntimeError(f'URL wolfmax con formato no reconocido (¿del diseño antiguo?): {url}')
    section, code = m.group(1), m.group(2)
    download_url, external = _request_download_url(driver, code, _SECTION_TABLA[section])
    if external:
        download_url = _decrypt_link_out(driver, _enlacito_link_out(driver, download_url))
    return download_url


def _fetch_torrent(driver, torrent_url: str) -> tuple[str, bytes]:
    """Descarga el .torrent con fetch() desde el propio navegador.
    Devuelve (nombre de archivo, contenido)."""
    _ensure_on_site(driver)
    res = driver.execute_async_script(_FETCH_B64_JS, torrent_url)
    if res.get('status') != 200:
        raise RuntimeError(f"Descarga del torrent fallida: HTTP {res.get('status')} {res.get('error') or ''}".strip())
    content = base64.b64decode(res['b64'])
    if not content.startswith(b'd'):
        raise RuntimeError('La respuesta no es un archivo .torrent válido')

    filename = unquote(Path(urlparse(torrent_url).path).name) or 'descarga.torrent'
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    return filename, content


def _download_torrent(driver, torrent_url: str, output_dir) -> Path:
    filename, content = _fetch_torrent(driver, torrent_url)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_bytes(content)
    return out_path

# ---------------------------------------------------------------------------
# Flujo de descarga con curl_cffi (sin Selenium) + Turnstile automático
# ---------------------------------------------------------------------------

def _decrypt_gibberish(link_out: str, passphrase: str) -> str:
    """Descifra un enlace cifrado con GibberishAES (AES-256-CBC OpenSSL)."""
    try:
        raw = base64.b64decode(base64.b64decode(link_out))
    except Exception:
        raw = base64.b64decode(link_out)
    if raw[:8] != b'Salted__':
        raise RuntimeError('Formato OpenSSL no reconocido')
    salt, ct = raw[8:16], raw[16:]
    d0 = hashlib.md5(passphrase.encode() + salt).digest()
    d1 = hashlib.md5(d0 + passphrase.encode() + salt).digest()
    d2 = hashlib.md5(d1 + passphrase.encode() + salt).digest()
    decryptor = Cipher(algorithms.AES(d0 + d1), modes.CBC(d2)).decryptor()
    pt = decryptor.update(ct) + decryptor.finalize()
    return pt[:-pt[-1]].decode('utf-8')


def _cffi_generate(code: str, tabla: str) -> dict:
    resp = cffi_requests.post(
        _API_URL, json={'action': 'generate', 'code': code, 'tabla': tabla},
        impersonate='chrome', timeout=15)
    data = resp.json()
    if not data.get('success'):
        raise RuntimeError(f'wolfmax generate: {data}')
    return data


def _cffi_validate(challenge: str, nonce: int, turnstile_token: str | None = None) -> dict:
    payload: dict = {'action': 'validate', 'challenge': challenge, 'nonce': nonce}
    if turnstile_token:
        payload['cf-turnstile-response'] = turnstile_token
    resp = cffi_requests.post(_API_URL, json=payload, impersonate='chrome', timeout=15)
    data = resp.json()
    if data.get('status') == 'limit_exceeded':
        raise WolfmaxRateLimited(int(data.get('wait_minutes') or 60))
    if data.get('status') in ('captcha_required', 'turnstile_required'):
        raise WolfmaxVerificationRequired()
    if not data.get('success'):
        raise RuntimeError(f'wolfmax validate: {data}')
    return data


def _cffi_resolve_torrent_url(wolfmax_url: str, solver=None) -> str:
    url = normalize_url(wolfmax_url)
    m = EPISODE_URL_RE.match(url) or CONTENT_URL_RE.match(url)
    if not m:
        raise RuntimeError(f'URL wolfmax no reconocida: {url}')
    section, code = m.group(1), m.group(2)
    gen = _cffi_generate(code, _SECTION_TABLA[section])
    nonce = _solve_pow(gen['challenge'])
    token = None
    verification = gen.get('verification')
    if verification and verification.get('provider') == 'turnstile':
        if not solver:
            raise WolfmaxVerificationRequired()
        token = solver.solve(verification)
        if not token:
            raise WolfmaxVerificationRequired()
    val = _cffi_validate(gen['challenge'], nonce, token)
    download_url = val['download_url']
    if val.get('external'):
        resp = cffi_requests.get(
            download_url, impersonate='chrome', timeout=15,
            headers={'Referer': _API_BASE + '/'})
        m_lo = re.search(r'var\s+link_out\s*=\s*["\']([^"\']+)["\']', resp.text)
        if not m_lo:
            raise RuntimeError('link_out no encontrado en enlacito')
        download_url = _decrypt_gibberish(m_lo.group(1), _REAL_KEY)
    return download_url


def _cffi_fetch_torrent(torrent_url: str) -> tuple[str, bytes]:
    resp = cffi_requests.get(
        torrent_url, impersonate='chrome', timeout=30,
        headers={'Referer': _API_BASE + '/'})
    if resp.status_code != 200:
        raise RuntimeError(f'Descarga torrent: HTTP {resp.status_code}')
    content = resp.content
    if not content.startswith(b'd'):
        raise RuntimeError('La respuesta no es un .torrent válido')
    filename = unquote(Path(urlparse(torrent_url).path).name) or 'descarga.torrent'
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    return filename, content


def _collect_episodes(soup, seen: set[str]) -> list[dict]:
    episodes = []
    for row in soup.select('#episodios .wolf-episode'):
        a = row.find('a', href=True)
        if not a:
            continue
        url = normalize_url(a['href'])
        if not EPISODE_URL_RE.match(url) or url in seen:
            continue
        seen.add(url)
        label = a.get_text(' ', strip=True)
        parsed = parse_episode_label(label)
        episodes.append({
            'url': url,
            'label': label,
            'season': parsed[0] if parsed else None,
            'episodes': parsed[1] if parsed else [],
        })
    return episodes


class WolfmaxDownloader:
    """Descarga archivos .torrent de wolfmax4k.com al directorio de staging configurado."""
    def __init__(self, solver=None):
        self._solver = solver
    # get_episodes, get_episode_links, get_download_url → sin cambios
    
    def get_episodes(self, series_url: str, all_seasons: bool = True, max_search_pages: int = 3) -> list[dict]:
        """
        Devuelve los episodios de una ficha de temporada como dicts
        {url, label, season, episodes}. El sitio publica una ficha por
        temporada y calidad; con `all_seasons` se buscan también las demás
        temporadas de la misma serie con la misma etiqueta de calidad.
        """
        series_url = normalize_url(series_url)
        m = CONTENT_URL_RE.match(series_url)
        if not m:
            raise RuntimeError(f'URL de serie wolfmax no reconocida: {series_url}')
        section = m.group(1)

        driver = _make_driver()
        try:
            driver.get(series_url)
            time.sleep(4)
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            seen: set[str] = set()
            episodes = _collect_episodes(soup, seen)
            if not all_seasons:
                return episodes

            h1 = soup.select_one('.wolf-detail-info h1')
            base, season, suffix = split_season_title(h1.get_text(' ', strip=True) if h1 else '')
            if not season or not base:
                return episodes

            from core.wolfmax.category_scanner import _normalize
            base_norm, suffix_norm = _normalize(base), _normalize(suffix)
            sibling_urls = []
            listing = f'{BASE_URL}/{_SECTION_TABLA[section]}'
            for page in range(1, max_search_pages + 1):
                driver.get(f'{listing}?q={quote_plus(base)}&pagina={page}')
                time.sleep(3)
                page_soup = BeautifulSoup(driver.page_source, 'html.parser')
                cards = page_soup.select('article.wolf-card .wolf-card-title a[href]')
                for a in cards:
                    url = normalize_url(a['href'])
                    c_base, c_season, c_suffix = split_season_title(a.get_text(' ', strip=True))
                    if (url != series_url and c_season and CONTENT_URL_RE.match(url)
                            and _normalize(c_base) == base_norm and _normalize(c_suffix) == suffix_norm
                            and url not in sibling_urls):
                        sibling_urls.append(url)
                if len(cards) < 20:
                    break

            for url in sibling_urls:
                driver.get(url)
                time.sleep(3)
                episodes.extend(_collect_episodes(BeautifulSoup(driver.page_source, 'html.parser'), seen))
            return episodes
        finally:
            driver.quit()

    def get_episode_links(self, series_url: str) -> list[str]:
        """Devuelve las URLs de episodios de la serie (todas las temporadas)."""
        return [ep['url'] for ep in self.get_episodes(series_url)]

    def get_download_url(self, wolfmax_url: str) -> str:
        """Devuelve la URL directa del .torrent sin descargarlo."""
        driver = _make_driver()
        try:
            return _resolve_torrent_url(driver, wolfmax_url)
        finally:
            driver.quit()

    def fetch_torrent(self, wolfmax_url: str) -> tuple[str, bytes]:
        torrent_url = _cffi_resolve_torrent_url(wolfmax_url, self._solver)
        logger.info('wolfmax: obteniendo %s', torrent_url)
        return _cffi_fetch_torrent(torrent_url)

    def download_to_staging(self, wolfmax_url: str, staging_dir: str) -> Path:
        filename, content = self.fetch_torrent(wolfmax_url)
        out_dir = Path(staging_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        out_path.write_bytes(content)
        logger.info('wolfmax: descargado → %s', out_path)
        return out_path
