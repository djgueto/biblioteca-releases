#!/bin/sh
# Con PUID/PGID (como en las imágenes de linuxserver.io) la app corre con ese
# usuario: así los archivos que crea o mueve en el NAS tienen el dueño de
# siempre y no root. Sin ellos, corre como root.
set -e

mkdir -p /config

if [ -n "$PUID" ] && [ "$(id -u)" = "0" ]; then
    PGID="${PGID:-$PUID}"
    chown "$PUID:$PGID" /config
    # Chromium necesita un HOME escribible para su perfil.
    export HOME=/config
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

exec "$@"
