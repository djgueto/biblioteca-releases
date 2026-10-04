"""Lanzador de escritorio (Windows, macOS, Linux).

Arranca el servidor web en segundo plano, abre el navegador y deja un icono
en la bandeja del sistema con "Abrir Biblioteca" y "Salir". Es el punto de
entrada de la app empaquetada con PyInstaller (ver biblioteca.spec).

Sin bandeja disponible (pystray no instalado, o Linux sin bandeja), el
servidor sigue funcionando igual: se abre el navegador y se cierra con Ctrl+C
o cerrando la ventana.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path

# App sin consola (Windows): no hay stdout/stderr y uvicorn los necesita.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))

# Al ejecutar desde el código fuente (python packaging/desktop/launcher.py).
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.__main__ import local_url, serve, wait_and_open  # noqa: E402

# Por defecto solo desde este equipo; WEB_HOST=0.0.0.0 para abrirlo a la red.
HOST = os.getenv("WEB_HOST", "127.0.0.1")
PORT = int(os.getenv("WEB_PORT", "9797"))
URL = local_url(HOST, PORT)
# WEB_OPEN_BROWSER=false: no abrir el navegador al arrancar (p. ej. al iniciar sesión).
OPEN_BROWSER = os.getenv("WEB_OPEN_BROWSER", "true").lower() != "false"


def open_when_ready():
    if OPEN_BROWSER:
        wait_and_open(URL)


def _icon_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(245, 185, 66, 255))
    for x, h in ((14, 36), (27, 36), (40, 32)):
        d.rectangle((x, 46 - h + 4, x + 9, 50), fill=(26, 20, 5, 255))
    return img


def run_tray(server: threading.Thread) -> bool:
    try:
        import pystray
    except Exception:
        return False

    def open_ui(_icon=None, _item=None):
        webbrowser.open(URL)

    def quit_app(icon, _item=None):
        icon.stop()
        os._exit(0)  # uvicorn corre en otro hilo: se cierra todo el proceso

    icon = pystray.Icon("biblioteca", _icon_image(), "Biblioteca", menu=pystray.Menu(
        pystray.MenuItem("Abrir Biblioteca", open_ui, default=True),
        pystray.MenuItem(f"Dirección: {URL}", None, enabled=False),
        pystray.MenuItem("Salir", quit_app),
    ))
    threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        icon.run()
    except Exception:
        return False
    return True


def main():
    server = threading.Thread(target=serve, args=(HOST, PORT), name="servidor", daemon=True)
    server.start()
    if not run_tray(server):
        threading.Thread(target=open_when_ready, daemon=True).start()
        try:
            server.join()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
