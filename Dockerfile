FROM python:3.11-slim-bookworm

# chromium + su driver: Hispashare y Wolfmax se navegan con Selenium.
# libmediainfo0v5: detección de resolución real de los vídeos (pymediainfo).
# Hay paquetes para amd64 y arm64, así que la misma imagen sirve en PC y NAS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium chromium-driver libmediainfo0v5 tini tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER=/usr/bin/chromedriver \
    SELENIUM_HEADLESS=true \
    APP_DATA_DIR=/config \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=9797

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config ./config
COPY core ./core
COPY utils ./utils
COPY web ./web
COPY packaging/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ARG VERSION=dev
ENV APP_VERSION=$VERSION

VOLUME ["/config"]
EXPOSE 9797
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9797/api/health', timeout=4)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["python", "-m", "web"]
