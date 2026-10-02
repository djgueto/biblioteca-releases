# -*- coding: utf-8 -*-
"""
wolfmax_dl.py — Descargador automatico de wolfmax4k.com
Uso: python wolfmax_dl.py <url_wolfmax> [-o directorio] [--only-url]
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import argparse, os, re, json, time, requests, urllib3
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

urllib3.disable_warnings()

GIBBERISH_JS = Path(__file__).with_name('gibberish-aes.js')
# Clave derivada del api_key "w0lfm4x4k1" por el codigo ofuscado de enlacito.com
REAL_KEY = 'fee631d2cffda38a78b96ee6d2dfb43a'


# ---------------------------------------------------------------------------
# Selenium helpers
# ---------------------------------------------------------------------------

def _make_driver():
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',
        {'source': 'Object.defineProperty(navigator,"webdriver",{get:()=>undefined});'})
    return driver


def _get_enlacito_url(driver, wolfmax_url):
    """Carga la pagina wolfmax (requiere Chrome para pasar el bloqueo ISP)
    y devuelve el enlace a enlacito.com."""
    print('[1] Cargando pagina wolfmax...', flush=True)
    driver.get(wolfmax_url)
    time.sleep(4)
    for a in driver.find_elements(By.TAG_NAME, 'a'):
        href = a.get_attribute('href') or ''
        if 'enlacito.com' in href:
            return href
    return None


# ---------------------------------------------------------------------------
# enlacito.com flow (requests, sin Selenium para evitar deteccion bot)
# ---------------------------------------------------------------------------

def _enlacito_to_link_out(enlacito_url, referer):
    """GET s.php -> POST form -> devuelve link_out cifrado."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36'),
        'Accept-Language': 'es-ES,es;q=0.9',
    })
    session.verify = False

    r1 = session.get(enlacito_url, timeout=20, headers={
        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.9',
        'Referer': referer,
        'Upgrade-Insecure-Requests': '1',
    })

    soup1 = BeautifulSoup(r1.content, 'html.parser')
    form = soup1.find('form')
    if not form:
        raise RuntimeError('enlacito.com no devolvio el formulario de espera')

    form_action = form.get('action', '').split('#')[0]
    hidden = {i.get('name'): i.get('value', '') for i in form.find_all('input')}

    r2 = session.post(form_action, data=hidden, timeout=30, headers={
        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.9',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Referer': enlacito_url,
        'Origin': 'https://enlacito.com',
        'Upgrade-Insecure-Requests': '1',
    })
    html2 = r2.content.decode('utf-8', errors='replace')

    m = re.search(r'var\s+link_out\s*=\s*["\']([^"\']+)["\']', html2)
    if not m:
        raise RuntimeError('No se encontro link_out en la pagina de enlacito')
    return m.group(1)


# ---------------------------------------------------------------------------
# Descifrado GibberishAES (ejecutado en el navegador para maxima fiabilidad)
# ---------------------------------------------------------------------------

def _decrypt_link_out(driver, link_out):
    """Ejecuta GibberishAES.dec() en Chrome usando la clave real."""
    if not GIBBERISH_JS.exists():
        raise RuntimeError(f'Falta gibberish-aes.js en {GIBBERISH_JS}')

    gibberish_js = GIBBERISH_JS.read_text(encoding='utf-8')
    driver.get('about:blank')
    time.sleep(0.3)
    driver.execute_script(gibberish_js)

    # link_out = outer_base64(GibberishAES_ciphertext)
    # atob() decodifica el outer base64; link_out y REAL_KEY son ASCII seguros en JS strings
    js = f"""
    try {{
        var inner = atob("{link_out}");
        var url   = GibberishAES.dec(inner, "{REAL_KEY}");
        return JSON.stringify({{ok: true, url: url}});
    }} catch(e) {{
        try {{
            var url2 = GibberishAES.dec("{link_out}", "{REAL_KEY}");
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


# ---------------------------------------------------------------------------
# Descarga del archivo
# ---------------------------------------------------------------------------

def _download_with_chrome(url, output_dir):
    """Descarga usando Chrome (necesario cuando el ISP bloquea la URL)."""
    import glob as globmod, threading

    out_dir = str(Path(output_dir).resolve())
    prefs = {
        'download.default_directory': out_dir,
        'download.prompt_for_download': False,
        'download.directory_upgrade': True,
        'safebrowsing.enabled': False,
    }
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    opts.add_experimental_option('prefs', prefs)
    driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',
        {'source': 'Object.defineProperty(navigator,"webdriver",{get:()=>undefined});'})

    try:
        # Listar archivos antes de descargar
        before = set(globmod.glob(str(Path(output_dir) / '*')))
        driver.get(url)
        # Esperar hasta que aparezca un archivo nuevo (max 120s)
        deadline = time.time() + 120
        out_path = None
        while time.time() < deadline:
            time.sleep(1)
            after = set(globmod.glob(str(Path(output_dir) / '*')))
            new_files = after - before
            # Ignorar archivos .crdownload (en progreso) y .tmp
            completed = [f for f in new_files
                         if not f.endswith('.crdownload') and not f.endswith('.tmp')]
            if completed:
                out_path = Path(completed[0])
                # Esperar a que el .crdownload desaparezca si aun existe
                partial = out_path.with_suffix('.crdownload')
                if partial.exists():
                    while partial.exists() and time.time() < deadline:
                        time.sleep(1)
                break
        return out_path
    finally:
        driver.quit()


def _download_file(url, output_dir):
    """Descarga la URL al directorio y devuelve la ruta del archivo."""
    parsed = urlparse(url)
    hostname = parsed.hostname or ''
    filename = os.path.basename(parsed.path) or 'descarga'
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    out_path = Path(output_dir) / filename
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    print(f'[*] Archivo: {filename}', flush=True)

    # Intentar descarga directa primero
    headers = {
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36'),
        'Referer': 'https://enlacito.com/',
    }
    try:
        r = requests.get(url, stream=True, verify=False,
                         headers=headers, timeout=20)
        r.raise_for_status()
        total = int(r.headers.get('content-length', 0))
        if total:
            print(f'[*] Tamano: {total / 1024 / 1024:.1f} MB', flush=True)
        downloaded = 0
        with open(out_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    mb = downloaded // (1024 * 1024)
                    tmb = total // (1024 * 1024)
                    print(f'\r    {pct}%  ({mb} MB / {tmb} MB)  ',
                          end='', flush=True)
        print(flush=True)
        return out_path
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 502:
            print('[*] Dominio bloqueado, usando Chrome para descargar...', flush=True)
        else:
            raise

    # Descarga via Chrome (dominio bloqueado por ISP)
    out_path = _download_with_chrome(url, output_dir)
    if out_path is None:
        raise RuntimeError('Timeout esperando que Chrome complete la descarga')
    return out_path


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

# Patron de URL de episodio/pelicula individual
_EPISODE_RE = re.compile(
    r'https?://wolfmax4k\.com/(?:serie-online-4k|movie|serie-online|pelicula)/\d+',
    re.IGNORECASE)


def get_episode_links(series_url):
    """
    Dado un URL de pagina de serie (con lista de episodios), devuelve
    la lista ordenada de URLs de episodios individuales.
    Devuelve lista vacia si la pagina no tiene episodios listados.
    """
    driver = _make_driver()
    try:
        driver.get(series_url)
        time.sleep(4)
        seen, links = set(), []
        for a in driver.find_elements(By.TAG_NAME, 'a'):
            href = a.get_attribute('href') or ''
            if _EPISODE_RE.match(href) and href not in seen:
                seen.add(href)
                links.append(href)
        return links
    finally:
        driver.quit()


def get_download_url(wolfmax_url):
    """
    Devuelve la URL directa de descarga para un episodio/pelicula.
    No descarga el archivo.
    """
    driver = _make_driver()
    try:
        enlacito_url = _get_enlacito_url(driver, wolfmax_url)
        if not enlacito_url:
            raise RuntimeError('No se encontro boton de descarga en la pagina')
        link_out = _enlacito_to_link_out(enlacito_url, wolfmax_url)
        return _decrypt_link_out(driver, link_out)
    finally:
        driver.quit()


def download(wolfmax_url, output_dir=None):
    """
    Descarga el archivo de un episodio/pelicula.
    Devuelve la ruta del archivo descargado.
    """
    if output_dir is None:
        output_dir = Path.home() / 'Downloads'

    driver = _make_driver()
    try:
        enlacito_url = _get_enlacito_url(driver, wolfmax_url)
        if not enlacito_url:
            raise RuntimeError('No se encontro boton de descarga')
        print(f'[2] Enlacito: {enlacito_url[:70]}...', flush=True)

        link_out = _enlacito_to_link_out(enlacito_url, wolfmax_url)
        print('[3] link_out obtenido, descifrando...', flush=True)

        download_url = _decrypt_link_out(driver, link_out)
    finally:
        driver.quit()

    print(f'[4] URL: {download_url}', flush=True)
    out_path = _download_file(download_url, output_dir)
    print(f'\n[OK] Guardado en: {out_path}', flush=True)
    return out_path


def download_series(series_url, output_dir=None):
    """
    Descarga todos los episodios de una pagina de serie.
    Devuelve lista de rutas descargadas.
    """
    if output_dir is None:
        output_dir = Path.home() / 'Downloads'

    print(f'[*] Buscando episodios en: {series_url}', flush=True)
    episode_links = get_episode_links(series_url)

    if not episode_links:
        raise RuntimeError('No se encontraron episodios en esta pagina')

    print(f'[*] {len(episode_links)} episodio(s) encontrado(s):', flush=True)
    for i, ep in enumerate(episode_links, 1):
        print(f'    {i}. {ep}', flush=True)
    print(flush=True)

    results = []
    for i, ep_url in enumerate(episode_links, 1):
        print(f'{"="*60}', flush=True)
        print(f'[Ep {i}/{len(episode_links)}] {ep_url}', flush=True)
        try:
            out_path = download(ep_url, output_dir)
            results.append((ep_url, out_path, None))
        except Exception as e:
            print(f'[!] Error en episodio {i}: {e}', flush=True)
            results.append((ep_url, None, str(e)))
        # Pausa breve entre episodios para no saturar el servidor
        if i < len(episode_links):
            time.sleep(3)

    print(f'\n{"="*60}', flush=True)
    print(f'RESUMEN: {sum(1 for _, p, _ in results if p)} / {len(results)} descargados', flush=True)
    for ep_url, out_path, err in results:
        status = f'OK -> {out_path}' if out_path else f'ERROR: {err}'
        print(f'  {ep_url[-30:]}  {status}', flush=True)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Descargador automatico de wolfmax4k.com',
        epilog=('Ejemplos:\n'
                '  python wolfmax_dl.py https://wolfmax4k.com/movie/267205\n'
                '  python wolfmax_dl.py https://wolfmax4k.com/series/4k-2160p/satisfaccion-garantizada\n'
                '  python wolfmax_dl.py https://wolfmax4k.com/movie/267205 -o D:/Pelis\n'
                '  python wolfmax_dl.py https://wolfmax4k.com/movie/267205 --only-url'),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('url', help='URL de wolfmax4k.com (episodio, pelicula o serie completa)')
    parser.add_argument('-o', '--output', default=None,
                        help='Directorio de destino (por defecto: ~/Downloads)')
    parser.add_argument('--only-url', action='store_true',
                        help='Solo mostrar la URL directa sin descargar (solo para episodios)')
    args = parser.parse_args()

    # Detectar si es pagina de serie (no es un episodio individual)
    is_episode = bool(_EPISODE_RE.match(args.url))

    if args.only_url:
        if not is_episode:
            print('[!] --only-url solo funciona con URLs de episodio/pelicula individuales')
            sys.exit(1)
        url = get_download_url(args.url)
        print(url)
    elif is_episode:
        download(args.url, args.output)
    else:
        # Pagina de serie con lista de episodios
        download_series(args.url, args.output)


if __name__ == '__main__':
    main()
