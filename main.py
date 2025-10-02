import sys
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow
from core.database.db_manager import DatabaseManager
from utils.logger import setup_logger

def main():
    # Configurar logger
    logger = setup_logger()
    logger.info("Iniciando Series Manager...")
    
    # Inicializar base de datos
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