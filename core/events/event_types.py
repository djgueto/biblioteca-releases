SERIES_UPDATED = "SERIES_UPDATED"
TMDB_CHANGED = "TMDB_CHANGED"
SOURCE_MATCH_CHANGED = "SOURCE_MATCH_CHANGED"
MISSING_EPISODES_REBUILT = "MISSING_EPISODES_REBUILT"
SOURCE_OFFER_UPDATED = "SOURCE_OFFER_UPDATED"

# Eventos de tareas en segundo plano (core/jobs). Todos llevan "source" en el
# payload con el nombre del job que los publica.
TASK_PROGRESS = "TASK_PROGRESS"  # {source, percent, message}
TASK_LOG = "TASK_LOG"  # {source, message}
TASK_FINISHED = "TASK_FINISHED"  # {source, result: dict}
TASK_ERROR = "TASK_ERROR"  # {source, error}
SOURCE_URL_SAVED = "SOURCE_URL_SAVED"  # {source, series_id, url}
TMDB_MATCHED = "TMDB_MATCHED"  # {source, series_id, match}
