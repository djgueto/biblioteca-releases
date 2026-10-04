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
import time
import webbrowser
from pathlib import Path

# App sin consola (Windows): no hay stdout/stderr y uvicorn los necesita.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))

os.environ["BIBLIOTECA_DESKTOP"] = "1"

# Al ejecutar desde el código fuente (python packaging/desktop/launcher.py).
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import Settings  # noqa: E402
from web import runtime  # noqa: E402
from web.__main__ import local_url, serve, wait_and_open  # noqa: E402

# Solo desde este equipo, salvo que en Ajustes → Acceso se haya permitido la
# red local (o WEB_HOST lo fuerce).
HOST = os.getenv("WEB_HOST") or ("0.0.0.0" if Settings.WEB_ALLOW_LAN else "127.0.0.1")
PORT = int(os.getenv("WEB_PORT", "9797"))
URL = local_url(HOST, PORT)
# WEB_OPEN_BROWSER=false: no abrir el navegador al arrancar (p. ej. al iniciar sesión).
OPEN_BROWSER = os.getenv("WEB_OPEN_BROWSER", "true").lower() != "false" and not os.getenv("BIBLIOTECA_RESTARTED")


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

    def restart_app(icon, _item=None):
        icon.stop()
        runtime.restart(delay=0)

    lan = [f"http://{ip}:{PORT}" for ip in runtime.lan_addresses()] if runtime.listening_on_network(HOST) else []
    items = [pystray.MenuItem("Abrir Biblioteca", open_ui, default=True)]
    items += [pystray.MenuItem(f"Desde el móvil: {u}", None, enabled=False) for u in lan[:2]]
    if not lan:
        items.append(pystray.MenuItem("Solo desde este PC (Ajustes → Acceso)", None, enabled=False))

    def install_update(_icon=None, _item=None):
        from web import updates
        try:
            updates.install()
        except RuntimeError:
            webbrowser.open(URL + "/#/system")

    def update_label(_item):
        from web import updates
        return f"⬆ Actualizar a {updates.state.latest}"

    def update_available(_item):
        from web import updates
        return updates.state.available and not updates.state.installing

    items.append(pystray.MenuItem(update_label, install_update, visible=update_available))
    items += [pystray.MenuItem("Reiniciar", restart_app), pystray.MenuItem("Salir", quit_app)]
    icon = pystray.Icon("biblioteca", _icon_image(), "Biblioteca", menu=pystray.Menu(*items))

    def refresh_menu():
        # El aviso de actualización aparece cuando la comprobación termina.
        while True:
            time.sleep(60)
            try:
                icon.update_menu()
            except Exception:
                pass

    threading.Thread(target=refresh_menu, daemon=True).start()
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
