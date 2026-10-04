# PyInstaller: app de escritorio para Windows, macOS y Linux.
#
#   pip install -r requirements.txt -r packaging/desktop/requirements.txt
#   pyinstaller packaging/desktop/biblioteca.spec
#
# Resultado en dist/Biblioteca/ (en macOS además dist/Biblioteca.app).
# Necesita Google Chrome (o Chromium) instalado en el equipo: Selenium
# descarga solo el driver que le corresponde.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]

datas = [
    (str(ROOT / "web" / "static"), "web/static"),
    (str(ROOT / "core" / "wolfmax" / "gibberish-aes.js"), "core/wolfmax"),
]
# La CI escribe aquí la versión (tag): la app la usa para saber si hay otra más nueva.
if (ROOT / "VERSION").exists():
    datas.append((str(ROOT / "VERSION"), "."))

hidden = [
    # uvicorn carga estos módulos por nombre.
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    "pystray._win32" if sys.platform == "win32" else
    "pystray._darwin" if sys.platform == "darwin" else "pystray._xorg",
]

a = Analysis(
    [str(ROOT / "packaging" / "desktop" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "PyQt6", "PyQt5"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Biblioteca",
    console=False,  # sin ventana negra: se maneja desde el icono de la bandeja
)
coll = COLLECT(exe, a.binaries, a.datas, name="Biblioteca")

if sys.platform == "darwin":
    app = BUNDLE(coll, name="Biblioteca.app", bundle_identifier="org.biblioteca.app",
                 info_plist={"LSUIElement": True})  # solo icono en la barra de menús
