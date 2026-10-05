"""Nombres de carpeta/archivo que Windows no puede abrir a través de SMB.

Un nombre válido en el NAS (Linux) pero no en Windows, como "Muertos S.L."
(punto o espacio al final) o uno con ':' '?' '"'..., llega por Samba con un
nombre corto inventado ("MKZ9R6~H") y Windows lo trata como dañado. Escanearlo
crea una ficha con ese nombre inventado que nunca encuentra nada en TMDB.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Nombre 8.3 que genera Samba: 6 caracteres, '~', 1 carácter (y extensión opcional).
_SAMBA_MANGLED = re.compile(r"^[A-Z0-9_]{6}~[A-Z0-9_](\.[A-Z0-9_]{1,3})?$")

_warned: set[str] = set()


def is_unreadable_name(path) -> bool:
    """True si la entrada no se puede usar desde Windows; avisa una vez en el log."""
    name = path.name
    if not (name.endswith((".", " ")) or _SAMBA_MANGLED.match(name)):
        return False
    key = str(path)
    if key not in _warned:
        _warned.add(key)
        logger.warning(
            f"Omitida '{name}' en {path.parent}: Windows no puede leer ese nombre "
            f"(termina en punto/espacio o lleva caracteres no válidos). Renómbrala desde el NAS."
        )
    return True
