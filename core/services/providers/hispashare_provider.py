from __future__ import annotations

from config.settings import Settings
from core.hispashare.downloader import HispashareDownloader
from core.hispashare.searcher import HispashareSearcher
from core.services.providers.base import DownloadHandle, SourceOffer
from core.services.quality import Quality


class HispashareSourceProvider:
    """Envuelve HispashareSearcher/HispashareDownloader (sin reimplementar su
    scraping) para exponerlos a través del contrato SourceProvider genérico.
    Reutiliza el driver Selenium+login que el scheduler ya crea una vez por
    ciclo -- debe construirse dentro de execute_cycle(), nunca en __init__,
    porque el scheduler cierra ese driver al final de cada ciclo."""

    name = "hispashare"
    # Sabe buscar y descargar películas sueltas (novedades y mejoras).
    supports_movies = True

    def __init__(self, driver, db_manager):
        self.driver = driver
        self.db = db_manager
        self._searcher = HispashareSearcher(driver, db_manager)
        self._downloader = HispashareDownloader(driver, db_manager)

    def find_candidate(self, item: dict, *, content_mode: str = "episodic") -> SourceOffer | None:
        query = (item.get("tmdb_name") or item.get("name") or "").strip()
        if not query:
            return None
        result = self._searcher.search_best_result(
            query, item.get("year"), item.get("id"), content_mode=content_mode
        )
        url = (result or {}).get("url")
        if not url:
            return None
        return SourceOffer(
            provider=self.name,
            source_url=url,
            title=result.get("title", ""),
            quality=Quality(),  # desconocida hasta refresh_offer/get_best_quality
            match_score=float(result.get("score") or 0),
            cover_url=self.db.derive_hispashare_cover_url(url) or None,
        )

    def refresh_offer(self, item: dict, source_url: str) -> SourceOffer | None:
        if not source_url:
            return None
        quality = self._downloader.get_best_quality(source_url)
        if quality is None:
            return None
        return SourceOffer(
            provider=self.name,
            source_url=source_url,
            title=item.get("tmdb_name") or item.get("name", ""),
            quality=quality,
            match_score=1.0,
            cover_url=self.db.derive_hispashare_cover_url(source_url) or None,
        )

    def resolve_download(
        self, item: dict, offer: SourceOffer, *, missing_only: bool = True
    ) -> DownloadHandle | None:
        series_id = item.get("id")
        if series_id is None:
            return None
        if Settings.get_category_content_mode(item.get("content_category") or "series") == "single_file":
            return self._resolve_movie(item, offer, upgrade=not missing_only)
        if missing_only:
            links = self._downloader.download_missing_episodes(series_id)
        else:
            links = self._downloader.download_upgrade_episodes(series_id)
        if not links:
            return None
        return DownloadHandle(provider=self.name, kind="ed2k", payload={"ed2k_links": links})

    def _resolve_movie(self, item: dict, offer: SourceOffer | None, *, upgrade: bool) -> DownloadHandle | None:
        """Película/documental: la mejor sección de calidad de su ficha (con el
        filtro de tamaño de películas del barrido de Hispashare) → eMule."""
        from core.hispashare.content_scanner import ContentScanner
        from core.repositories.grab_history_repository import GrabHistoryRepository
        from core.repositories.item_quality_repository import ItemQualityRepository
        from core.services.quality_profile import QualityProfile

        series_id = item["id"]
        url = (offer.source_url if offer and offer.provider == self.name else "") or item.get("hispashare_url") or ""
        if not url:
            return None
        history = GrabHistoryRepository(self.db)
        if history.recently_grabbed_item(series_id, Settings.GRAB_RETRY_DAYS):
            return None
        if upgrade:
            local = ItemQualityRepository(self.db).get(series_id)
            quality = offer.quality if offer else None
            if local is None or quality is None or not QualityProfile.load(self.db).is_upgrade(local, quality):
                return None
        category = item.get("content_category") or "movies"
        links = ContentScanner(self.driver, self.db)._process_content_item({
            "url": url,
            "title": item.get("tmdb_name") or item.get("name") or "",
            # _is_movie() aplica el filtro de tamaño mínimo/máximo de películas.
            "genre": "Películas" if category in ("movies", "animated_movies") else "",
            "library_category": category,
        })
        if not links:
            return None
        self._downloader._save_ed2k_links(series_id, links, kind="upgrade" if upgrade else "missing")
        history.record(self.name, url, series_id, [], release_title=item.get("tmdb_name") or item.get("name") or "",
                       destination="eMule")
        return DownloadHandle(provider=self.name, kind="ed2k", payload={"ed2k_links": links})
