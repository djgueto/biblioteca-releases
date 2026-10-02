from __future__ import annotations

import logging

from config.settings import Settings
from core.services.providers.base import DownloadHandle, SourceOffer
from core.services.quality import Quality
from core.wolfmax.downloader import WolfmaxDownloader, WolfmaxRateLimited, is_current_url
from core.wolfmax.searcher import WolfmaxSearcher

logger = logging.getLogger(__name__)


class WolfmaxSourceProvider:
    """Envuelve WolfmaxSearcher/WolfmaxDownloader (que ya gestionan su propio
    driver Selenium por llamada) para exponerlos como SourceProvider."""

    name = "wolfmax"

    def __init__(self, db_manager):
        self.db = db_manager
        self._searcher = WolfmaxSearcher()
        self._downloader = WolfmaxDownloader()

    def find_candidate(self, item: dict, *, content_mode: str = "episodic") -> SourceOffer | None:
        title = (item.get("tmdb_name") or item.get("name") or "").strip()
        if not title:
            return None
        app_category = item.get("content_category") or "movies"
        result = self._searcher.search(title, item.get("year"), app_category=app_category)
        if not result:
            return None
        url, score, quality_height = result
        return SourceOffer(
            provider=self.name,
            source_url=url,
            title=title,
            quality=Quality(resolution_height=quality_height),
            match_score=float(score or 0),
        )

    def refresh_offer(self, item: dict, source_url: str) -> SourceOffer | None:
        # URLs del diseño antiguo del sitio (/movie/123, /series/4k-2160p/x)
        # ya no existen: marcarlas no disponibles hasta que
        # scan_wolfmax_categories guarde la URL nueva.
        if not source_url or not is_current_url(source_url):
            return None
        # Wolfmax no tiene una forma barata de "revisitar" una única URL sin
        # re-escanear la categoría (eso ya lo hace scan_wolfmax_categories
        # cada ciclo, que además alimenta source_offers vía el dual-write en
        # DatabaseManager.save_wolfmax_source). Las URLs nuevas no llevan la
        # calidad en el path, así que se reutiliza la guardada en el escaneo.
        height = None
        stored = self.db.get_wolfmax_source_for_series(item["id"]) if item.get("id") else None
        if stored and stored.get("source_url") == source_url and stored.get("match_score"):
            height = round(float(stored["match_score"]) * 2160)
        return SourceOffer(
            provider=self.name,
            source_url=source_url,
            title=item.get("tmdb_name") or item.get("name", ""),
            quality=Quality(resolution_height=height),
            match_score=1.0,
        )

    def resolve_download(
        self, item: dict, offer: SourceOffer, *, missing_only: bool = True
    ) -> DownloadHandle | None:
        staging_dir = getattr(Settings, "WOLFMAX_STAGING_DIR", "").strip()
        if not staging_dir:
            return None

        series_id = item.get("id")
        episodic_cats = self.db.get_episodic_categories()
        is_episodic = item.get("content_category") in episodic_cats

        if is_episodic:
            episodes = self._downloader.get_episodes(offer.source_url)
            if missing_only and series_id:
                # El sitio publica fichas por temporada con todos sus
                # episodios: descargar solo los que faltan en la biblioteca.
                missing = {
                    (m.get("season"), m.get("episode"))
                    for m in self.db.get_missing_episodes_for_series(series_id)
                }
                episodes = [
                    ep for ep in episodes
                    if any((ep["season"], n) in missing for n in ep["episodes"])
                ]
            episode_urls = [ep["url"] for ep in episodes]
            if not episode_urls:
                return None
            new_urls = self.db.filter_new_wolfmax_urls(episode_urls)
        else:
            new_urls = self.db.filter_new_wolfmax_urls([offer.source_url])

        if not new_urls:
            return None

        downloaded = []
        for ep_url in new_urls:
            try:
                out_path = self._downloader.download_to_staging(ep_url, staging_dir)
                self.db.mark_wolfmax_urls_done([ep_url], series_id=series_id)
                downloaded.append(str(out_path))
            except WolfmaxRateLimited as e:
                logger.warning(str(e))
                break
            except Exception as e:
                logger.error(f"wolfmax resolve_download error {ep_url}: {e}")

        if not downloaded:
            return None
        return DownloadHandle(
            provider=self.name,
            kind="direct_http",
            payload={"downloaded": downloaded, "staging_dir": staging_dir},
        )
