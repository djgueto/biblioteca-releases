import sys
import traceback
from PyQt6.QtWidgets import QApplication, QMessageBox
from ui.main_window import MainWindow
from core.database.db_manager import DatabaseManager
from config.settings import Settings
from utils.logger import setup_logger
from utils.db_backup import backup_sqlite_db


def _install_excepthook(logger):
    def _handle_uncaught(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.error(
            "Excepción no capturada: %s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        QMessageBox.critical(
            None,
            "Error inesperado",
            f"Ha ocurrido un error inesperado:\n\n{exc_value}\n\n"
            "Se ha registrado en series_manager.log.",
        )

    sys.excepthook = _handle_uncaught


def main():
    # Configurar logger
    logger = setup_logger()
    logger.info("Iniciando Series Manager...")

    # Crear aplicación Qt primero para poder mostrar errores de arranque
    app = QApplication(sys.argv)
    app.setApplicationName("Series Manager")
    app.setOrganizationName("SeriesManager")
    _install_excepthook(logger)

    # Inicializar base de datos
    try:
        backup_path = backup_sqlite_db(Settings.DB_PATH)
        if backup_path:
            logger.info(f"Backup DB creado: {backup_path}")
        db = DatabaseManager()
        db.initialize_database()
    except Exception as e:
        logger.error(f"Error inicializando la base de datos: {e}", exc_info=True)
        QMessageBox.critical(
            None,
            "Error al iniciar",
            f"No se pudo inicializar la base de datos:\n\n{e}\n\n"
            f"Revisa que '{Settings.DB_PATH}' sea accesible (¿unidad de red disponible?).",
        )
        sys.exit(1)

    # Crear y mostrar ventana principal
    window = MainWindow()
    window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
