"""Creación de Chrome para Selenium independiente de la plataforma.

En Windows se sigue usando Chrome + Selenium Manager/webdriver-manager como
hasta ahora. En Docker (Linux, a menudo ARM en un NAS) no hay Chrome de
Google ni sirve webdriver-manager: la imagen instala chromium y su driver y
los indica con CHROME_BIN y CHROMEDRIVER.
"""

from __future__ import annotations

import os

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


def system_chrome_configured() -> bool:
    return bool(os.getenv("CHROMEDRIVER"))


def new_system_chrome(options: Options) -> webdriver.Chrome:
    """Chrome con el binario/driver indicados por CHROME_BIN/CHROMEDRIVER."""
    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin
    # /dev/shm de los contenedores es de 64 MB: Chrome se cuelga sin esto.
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    return webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=options)
