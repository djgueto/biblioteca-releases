# -*- coding: utf-8 -*-
"""
Raspado de páginas de categoría de wolfmax4k.com para descubrimiento
de contenido y obtención de URLs de series/películas.
Usa Selenium (Chrome headless) para superar el bloqueo ISP del sitio.
"""
import re
import time
import logging
from difflib import SequenceMatcher
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

from core.wolfmax.downloader import (
    _make_driver, normalize_url, split_season_title, CONTENT_URL_RE,
)

# ---------------------------------------------------------------------------
# Mapa: categoría de la app → [(url_listado_wolfmax, calidad_height)]
# El sitio filtra por calidad con ?calidad=<etiqueta> y pagina con ?pagina=N.
# La calidad_height es None cuando el listado no filtra por resolución
# (se deduce entonces de la etiqueta de cada archivo: "4K", "HDTV-1080p"...).
# ---------------------------------------------------------------------------
WOLFMAX_CATEGORY_URLS: dict[str, list[tuple[str, int | None]]] = {
    'movies': [
        ('https://wolfmax4k.com/peliculas?calidad=4K', 2160),
        ('https://wolfmax4k.com/peliculas?calidad=BluRay-1080p', 1080),
    ],
    'series': [
        ('https://wolfmax4k.com/series?calidad=4K', 2160),
        ('https://wolfmax4k.com/series?calidad=HDTV-1080p', 1080),
        ('https://wolfmax4k.com/series?calidad=BluRay-1080p', 1080),
    ],
    'cartoons': [
        ('https://wolfmax4k.com/animaciones', None),
    ],
    'animated_movies': [
        ('https://wolfmax4k.com/animaciones', None),
    ],
    'documentaries': [
        ('https://wolfmax4k.com/documentales', None),
    ],
    'docuseries': [
        ('https://wolfmax4k.com/documentales', None),
    ],
}

_STOP_WORDS = frozenset({
    'el', 'la', 'los', 'las', 'un', 'una', 'de', 'del', 'en', 'con', 'por',
    'the', 'a', 'an', 'of', 'in', 'and', 'or', 'to', 'y',
})

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    text = (text or '').lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # Los dígitos se conservan aunque sean de un solo carácter: son los que
    # distinguen secuelas/precuelas ("Toy Story" vs "Toy Story 2"). Solo se
    # descartan letras sueltas sin valor semántico.
    return ' '.join(
        t for t in text.split() if t not in _STOP_WORDS and (len(t) > 1 or t.isdigit())
    )


def _digit_tokens(tokens: set[str]) -> set[str]:
    return {t for t in tokens if t.isdigit()}


def _title_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a_tok = set(a.split())
    b_tok = set(b.split())
    # Si algún título lleva número de secuela/precuela ("2", "3"...) y el otro
    # lleva uno distinto (o ninguno), nunca es el mismo contenido, por muy
    # parecido que sea el resto del título (p. ej. "Toy Story" vs "Toy Story 2").
    if _digit_tokens(a_tok) != _digit_tokens(b_tok):
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    if a_tok and b_tok:
        overlap = len(a_tok & b_tok) / len(a_tok | b_tok)  # Jaccard real
        return max(seq, overlap * 0.9)
    return seq


def _clean_title(title: str) -> str:
    """Elimina temporada, sufijos de calidad/idioma y año del título wolfmax.
    'Ted Lasso - 4ª Temporada [480p]' → 'Ted Lasso'."""
    title = split_season_title(title)[0]
    title = re.sub(r'\s*\[.*?\]', '', title)        # [4K], [1080p] etc.
    title = re.sub(r'\s*\(\d{4}\)\s*$', '', title)  # año al final: (2026)
    return title.strip(' .-')


def _infer_quality_from_label(raw: str) -> int | None:
    """Calidad a partir de una etiqueta del sitio: '4K', 'BluRay-1080p',
    'HDTV-720p', o el sufijo del título de temporada ('[480p]')."""
    r = (raw or '').lower()
    if '2160' in r or re.search(r'\b4k\b', r):
        return 2160
    if '1080' in r:
        return 1080
    if '720' in r:
        return 720
    if '480' in r:
        return 480
    return None


class WolfmaxCategoryScanner:
    """
    Rasca las páginas de categoría de wolfmax4k.com para descubrir
    contenido y obtener sus URLs con calidad conocida.
    Usa un único driver Chrome por sesión de escaneo.
    """

    def __init__(self, max_pages: int = 3):
        self.max_pages = max_pages

    def scan_categories(
        self,
        app_categories: list[str] | None = None,
    ) -> list[dict]:
        """
        Rasca las categorías configuradas y devuelve lista de items encontrados.
        Cada item: {title, wolfmax_url, quality_height, app_category}
        """
        cats_to_scan = app_categories or list(WOLFMAX_CATEGORY_URLS.keys())
        all_items = []
        seen_urls: set[str] = set()

        driver = _make_driver()
        try:
            for cat in cats_to_scan:
                entries = WOLFMAX_CATEGORY_URLS.get(cat, [])
                for base_url, quality_h in entries:
                    items = self._scrape_category(driver, base_url, quality_h, cat)
                    for item in items:
                        if item['wolfmax_url'] not in seen_urls:
                            seen_urls.add(item['wolfmax_url'])
                            all_items.append(item)
        finally:
            driver.quit()

        logger.info(f'wolfmax category scan: {len(all_items)} items en {len(cats_to_scan)} categorías')
        return all_items

    def find_best_match(
        self,
        title: str,
        app_category: str,
        threshold: float = 0.55,
        year: int | None = None,
    ) -> dict | None:
        """
        Busca el mejor match en wolfmax para un título dado,
        usando el buscador de los listados de la categoría correspondiente.

        Si se conoce `year`, descarta candidatos con año distinto conocido:
        el título por sí solo no basta para distinguir remakes/homónimos
        (p. ej. "Dune" 1984 vs "Dune" 2021).
        """
        entries = WOLFMAX_CATEGORY_URLS.get(app_category, [])
        if not entries:
            entries = WOLFMAX_CATEGORY_URLS.get('movies' if 'movie' in app_category else 'series', [])

        query_norm = _normalize(title)
        best: dict | None = None
        best_score = 0.0

        driver = _make_driver()
        try:
            for base_url, quality_h in entries:
                # Buscador del propio listado (?q=): encuentra títulos antiguos
                # que no aparecen en las primeras páginas de novedades.
                sep = '&' if '?' in base_url else '?'
                search_url = f'{base_url}{sep}q={quote_plus(title)}'
                items = self._scrape_page(driver, search_url, quality_h, app_category)
                for item in items:
                    if year and item.get('year') and item['year'] != year:
                        continue
                    score = _title_score(query_norm, _normalize(item['title']))
                    if score > best_score:
                        best_score = score
                        best = {**item, 'score': score}
        finally:
            driver.quit()

        if best and best_score >= threshold:
            return best
        return None

    def _scrape_category(
        self,
        driver,
        base_url: str,
        quality_h: int | None,
        app_category: str,
    ) -> list[dict]:
        """Rasca todas las páginas de una URL de categoría wolfmax."""
        items = []
        sep = '&' if '?' in base_url else '?'
        for page_num in range(1, self.max_pages + 1):
            url = base_url if page_num == 1 else f'{base_url}{sep}pagina={page_num}'
            page_items = self._scrape_page(driver, url, quality_h, app_category)
            if not page_items:
                break
            items.extend(page_items)
            if len(page_items) < 5:
                break
        return items

    def _scrape_page(
        self,
        driver,
        url: str,
        quality_h: int | None,
        app_category: str,
    ) -> list[dict]:
        """Carga una página de categoría wolfmax con Chrome y extrae items."""
        try:
            driver.get(url)
            time.sleep(4)  # esperar renderizado JS y bypas bloqueo ISP
        except Exception as e:
            logger.warning(f'wolfmax selenium load error {url}: {e}')
            return []

        page_title = driver.title.lower()
        if '404' in page_title or 'not found' in page_title:
            return []

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        items = []
        seen: set[str] = set()

        # Cada título es un <article class="wolf-card"> con el enlace a la
        # ficha en .wolf-card-title y la lista de archivos (versiones de la
        # película o últimos episodios de la temporada) en li.wolf-card-file,
        # cada uno con su etiqueta de calidad en .wolf-card-format.
        for card in soup.select('article.wolf-card'):
            main = card.select_one('.wolf-card-title a[href]')
            if not main:
                continue
            raw_title = main.get_text(' ', strip=True)
            title = _clean_title(raw_title)
            if not title or len(title) < 2:
                continue

            year = None
            for span in card.select('.wolf-card-meta span'):
                if re.fullmatch(r'\d{4}', span.get_text(strip=True)):
                    year = int(span.get_text(strip=True))
                    break
            if year is None:
                year_match = re.search(r'\((\d{4})\)', raw_title)
                year = int(year_match.group(1)) if year_match else None

            files = []
            for li in card.select('li.wolf-card-file'):
                fmt = li.select_one('a.wolf-card-format[href]')
                if fmt:
                    files.append((normalize_url(fmt['href']), fmt.get_text(' ', strip=True)))

            main_url = normalize_url(main['href'])
            m = CONTENT_URL_RE.match(main_url)
            if not m:
                continue

            if m.group(1) == 'pelicula':
                # Películas: cada versión (4K, BluRay-1080p...) tiene su propia
                # ficha /pelicula/<código>; se guarda la de la calidad buscada.
                versions = [(u, _infer_quality_from_label(lbl)) for u, lbl in files
                            if CONTENT_URL_RE.match(u)] or [(main_url, None)]
                if quality_h is not None:
                    versions = [(u, q) for u, q in versions if q in (quality_h, None)][:1]
                    versions = [(u, quality_h) for u, _ in versions]
            else:
                # Series/documentales/animación: ficha por temporada y calidad.
                q = quality_h
                if q is None:
                    labels = [lbl for _, lbl in files] + [split_season_title(raw_title)[2]]
                    q = next((h for h in map(_infer_quality_from_label, labels) if h), None)
                versions = [(main_url, q)]

            season = split_season_title(raw_title)[1]
            for href, q in versions:
                if href in seen:
                    continue
                seen.add(href)
                items.append({
                    'title': title,
                    'wolfmax_url': href,
                    'quality_height': q,
                    'app_category': app_category,
                    'year': year,
                    'season': season,
                })

        logger.debug(f'wolfmax scrape {url}: {len(items)} items')
        return items
