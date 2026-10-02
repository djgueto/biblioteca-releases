# -*- coding: utf-8 -*-
"""
Búsqueda de contenido 4K en wolfmax4k.com.
Usa el buscador de los listados de cada categoría (?q=), filtrados por calidad.
"""
import logging

from core.wolfmax.category_scanner import WolfmaxCategoryScanner
from config.settings import Settings

logger = logging.getLogger(__name__)


class WolfmaxSearcher:
    """Busca contenido en wolfmax4k.com."""

    def search(
        self,
        title: str,
        year: int | None = None,
        app_category: str = 'movies',
        prefer_4k: bool = True,
    ) -> tuple[str, float, int | None] | None:
        """
        Busca `title` en wolfmax4k.com.

        Returns:
            (wolfmax_url, score, quality_height) o None si no supera el threshold.
        """
        threshold = getattr(Settings, 'WOLFMAX_MATCH_THRESHOLD', 0.55)
        scanner = WolfmaxCategoryScanner()
        match = scanner.find_best_match(title, app_category, threshold=threshold, year=year)
        if not match:
            return None
        logger.info(f'wolfmax search: "{title}" → {match["wolfmax_url"]} (score={match["score"]:.2f})')
        return match['wolfmax_url'], match['score'], match.get('quality_height')
