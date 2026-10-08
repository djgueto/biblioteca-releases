"""python -m web  →  servidor en http://0.0.0.0:9797

Opciones (o variables WEB_HOST / WEB_PORT):
  --host 127.0.0.1   solo accesible desde este equipo
  --port 9797
  --open             abre el navegador cuando el servidor está listo
"""

import argparse
import os
import threading
import time
import urllib.request
import webbrowser


def wait_and_open(url: str, timeout: float = 60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{url}/api/health", timeout=2)
            webbrowser.open(url)
            return
        except OSError:
            time.sleep(0.5)


def local_url(host: str, port: int) -> str:
    return f"http://{'127.0.0.1' if host in ('0.0.0.0', '::', '') else host}:{port}"


def serve(host: str, port: int, open_browser: bool = False):
    from config.settings import DATA_DIR
    from utils.logger import setup_logger

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    setup_logger()
    import uvicorn

    from web import runtime

    # Para Ajustes → Acceso: si escucha en la red o solo en este equipo.
    os.environ["BIBLIOTECA_BOUND_HOST"] = host
    os.environ["WEB_PORT"] = str(port)
    if os.getenv("BIBLIOTECA_RESTARTED"):
        runtime.wait_port_free(port)  # el proceso anterior aún lo está soltando

    # Una base de datos importada desde la web se aplica aquí, antes de
    # que nada la abra (ver core/database/transfer.py).
    from config.settings import Settings
    from core.database.transfer import apply_pending_import

    apply_pending_import(Settings.DB_PATH)

    from web.app import create_app

    if open_browser:
        threading.Thread(target=wait_and_open, args=(local_url(host, port),), daemon=True).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


def main():
    parser = argparse.ArgumentParser(prog="python -m web", description="Biblioteca: servidor web")
    parser.add_argument("--host", default=os.getenv("WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("WEB_PORT", "9797")))
    parser.add_argument("--open", action="store_true", help="abrir el navegador al arrancar")
    args = parser.parse_args()
    serve(args.host, args.port, args.open)


if __name__ == "__main__":
    main()
