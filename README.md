# Biblioteca

Gestor de series y películas al estilo de Sonarr/Radarr: vigila tu biblioteca,
la identifica con TMDB, detecta los episodios que faltan y los busca en
Hispashare, Wolfmax4k y DonTorrent, siempre con la mejor calidad disponible y
mejorando lo que ya tienes. Todo se maneja desde el navegador.

## Instalación

### Docker (NAS, servidor o PC con Linux)

Funciona en amd64 y arm64: Synology, QNAP, Unraid, TrueNAS, Raspberry Pi…

```yaml
# docker-compose.yml
services:
  biblioteca:
    image: ghcr.io/djgueto/biblioteca:latest
    restart: unless-stopped
    ports: ["9797:9797"]
    shm_size: "1gb"
    environment:
      TZ: Europe/Madrid
      PUID: "1000"   # dueño de tus archivos
      PGID: "1000"
    volumes:
      - ./config:/config            # configuración, base de datos y cachés
      - /ruta/a/tus/medios:/media   # tus series y películas
```

```sh
docker compose up -d
```

Abre `http://IP-DEL-EQUIPO:9797` y sigue el asistente. Si las bibliotecas
están en otro NAS por SMB, mira
[packaging/docker/docker-compose.nas-smb.yml](packaging/docker/docker-compose.nas-smb.yml).

### Windows, macOS y Linux (app de escritorio)

Descarga la versión de tu sistema en la página de *Releases*, descomprímela y
abre **Biblioteca**. Aparece un icono en la bandeja del sistema (o en la barra
de menús en macOS) y se abre el navegador. Necesita Google Chrome o Chromium
instalado: lo usa para entrar en Hispashare y Wolfmax.

En macOS, la primera vez: clic derecho → Abrir (la app no está firmada).

### Desde el código

Requiere Python 3.11 o superior y Google Chrome.

```sh
pip install -r requirements.txt
python -m web --open
```

## Primeros pasos

El asistente pide lo imprescindible:

1. **Clave de TMDB**: gratuita en [themoviedb.org](https://www.themoviedb.org/settings/api).
2. **Carpetas de la biblioteca**: series, películas, documentales…; cada
   subcarpeta es una serie o una película.
3. **Fuentes** (opcional): usuario de Hispashare, Wolfmax4k, DonTorrent.
4. **Cliente de descargas** (opcional): carpeta vigilada de tu cliente torrent
   (qBittorrent, Transmission…) y/o eMule/aMule.

Después, *Escanear biblioteca* y listo: el ciclo automático hace el resto.

## Dónde se guardan los datos

| Instalación | Carpeta |
|---|---|
| Docker | `/config` (el volumen) |
| Windows | `%APPDATA%\Biblioteca` |
| macOS | `~/Library/Application Support/Biblioteca` |
| Linux | `~/.config/biblioteca` |
| Desde el código, con un `.env` junto al código | la carpeta del código |

Se puede forzar otra con la variable `APP_DATA_DIR`. Conviene que la base de
datos esté en un disco local del equipo que ejecuta la app, no en una
carpeta de red: SQLite no se lleva bien con los cortes de red.

## Variables de entorno útiles

| Variable | Para qué |
|---|---|
| `APP_DATA_DIR` | carpeta de datos |
| `WEB_HOST` / `WEB_PORT` | dirección y puerto (por defecto `0.0.0.0:9797`; la app de escritorio usa `127.0.0.1`) |
| `WEB_SCHEDULER_AUTOSTART` | `false` para no arrancar el ciclo automático al iniciar |
| `WEB_PERIODIC_TASKS` | `false` para desactivar las tareas programadas (pruebas) |

## Estructura del código

```
config/      ajustes (.env) y carpeta de datos
core/        lógica: biblioteca, TMDB, fuentes, descargas, jobs (sin interfaz)
web/         servidor FastAPI
  routes/    API por áreas (biblioteca, ficha, ajustes, sistema…)
  static/    interfaz (Preact sin compilación)
packaging/   Docker y app de escritorio (PyInstaller)
scripts/     utilidades de mantenimiento puntuales
tests/       python -m unittest discover -s tests
```

## Publicar una versión

Crear un tag `vX.Y.Z` y subirlo: GitHub Actions publica la imagen Docker
(`latest` y `X.Y.Z`) y adjunta a la release las apps de Windows, macOS y Linux.
