import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def setup_logger():
    """Configurar logging para la aplicación"""
    
    # Logger raíz
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # Handler para consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # Handler para archivo (con rotación para que no crezca indefinidamente)
    from config.settings import Settings

    os.makedirs(os.path.dirname(Settings.LOG_FILE), exist_ok=True)
    file_handler = RotatingFileHandler(
        Settings.LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB por archivo
        backupCount=5,  # hasta 5 archivos de respaldo (series_manager.log.1 .. .5)
        encoding='utf-8',
    )
    # INFO: el nivel DEBUG generaba ~28k líneas por sesión (10 MB) en una unidad de red
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    # Reducir verbosidad de librerías externas
    logging.getLogger('selenium').setLevel(logging.WARNING)
    logging.getLogger('selenium.webdriver.remote.remote_connection').setLevel(logging.ERROR)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)
    logging.getLogger('WDM').setLevel(logging.WARNING)
    
    logger.info("=" * 60)
    logger.info("Series Manager iniciado")
    logger.info("=" * 60)
    
    return logger
