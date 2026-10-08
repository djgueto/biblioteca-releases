"""Firma los paquetes de una versión (lo ejecuta la CI al publicar).

    UPDATE_SIGNING_KEY=<clave privada Ed25519, base64> python sign_release.py paquetes/

Escribe en esa carpeta:
- SHA256SUMS: la huella de cada paquete ("<sha256>  <nombre>" por línea).
- SHA256SUMS.sig: la firma Ed25519 (base64) de SHA256SUMS.

La app (web/updates.py) comprueba la firma con la clave pública que lleva
dentro y la huella del paquete descargado antes de instalarlo. La clave
privada solo existe como secreto en GitHub.
"""

import base64
import hashlib
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SUMS, SIG = "SHA256SUMS", "SHA256SUMS.sig"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(folder: str) -> None:
    key = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
    if not key:
        sys.exit("Falta UPDATE_SIGNING_KEY: una versión sin firmar no se podría instalar")
    signer = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key))
    root = Path(folder)
    packages = sorted(p for p in root.iterdir() if p.is_file() and p.name not in (SUMS, SIG))
    if not packages:
        sys.exit(f"No hay paquetes en {root}")
    sums = "".join(f"{sha256(p)}  {p.name}\n" for p in packages).encode()
    (root / SUMS).write_bytes(sums)
    (root / SIG).write_text(base64.b64encode(signer.sign(sums)).decode() + "\n")
    print(sums.decode(), end="")


if __name__ == "__main__":
    main(sys.argv[1])
