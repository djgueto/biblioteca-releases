FROM python:3.11-slim-bookworm

# chromium + su driver: Hispashare y Wolfmax se navegan con Selenium.
# libmediainfo0v5: detección de resolución real de los vídeos (pymediainfo).
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium chromium-driver libmediainfo0v5 tini tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER=/usr/bin/chromedriver \
    SELENIUM_HEADLESS=true \
    APP_ENV_FILE=/config/.env \
    DB_PATH=/config/series_manager.db \
    COVER_CACHE_DIR=/config/cache/covers \
    LOG_FILE=/config/series_manager.log \
    BACKUPS_DIR=/config/backups \
    TORRENT_WATCH_DIR=/watch \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=9797

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config ./config
COPY core ./core
COPY utils ./utils
COPY web ./web
COPY scripts/wolfmax_extractor/gibberish-aes.js ./scripts/wolfmax_extractor/

VOLUME ["/config"]
EXPOSE 9797
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:9797/api/health', timeout=4)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "web"]
