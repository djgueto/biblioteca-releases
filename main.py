import sys
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow
from core.database.db_manager import DatabaseManager
from config.settings import Settings
from utils.logger import setup_logger
from utils.db_backup import backup_sqlite_db

def main():
    # Configurar logger
    logger = setup_logger()
    logger.info("Iniciando Series Manager...")
    
    # Inicializar base de datos
    backup_path = backup_sqlite_db(Settings.DB_PATH)
    if backup_path:
        logger.info(f"Backup DB creado: {backup_path}")
    db = DatabaseManager()
    db.initialize_database()
    
    # Crear aplicación Qt
    app = QApplication(sys.argv)
    app.setApplicationName("Series Manager")
    app.setOrganizationName("SeriesManager")
    
    # Crear y mostrar ventana principal
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
