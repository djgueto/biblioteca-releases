# Utilidades de mantenimiento

Se ejecutan a mano desde la carpeta del proyecto, con la app parada, sobre la
base de datos configurada. No forman parte de la app.

| Script | Para qué |
|---|---|
| `audit_hispashare_matches.py` | Revisa las fichas de Hispashare asignadas recalculando su puntuación con el título que se eligió en su día. |
| `audit_hispashare_matches_live.py` | Lo mismo para las que no tienen título guardado: vuelve a buscar en Hispashare (abre el navegador). |
| `dedupe_hispashare_matches.py` | Detecta y deshace fichas de Hispashare repetidas entre series. |
