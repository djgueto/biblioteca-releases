import logging
import sys
from PyQt6.QtCore import QObject, pyqtSignal
from datetime import datetime


class QtLogHandler(logging.Handler, QObject):
    """Handler personalizado para emitir logs a Qt"""
    log_signal = pyqtSignal(str)
    
    def __init__(self):
        logging.Handler.__init__(self)
        QObject.__init__(self)
        
        # Formato
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%H:%M:%S'
        )
        self.setFormatter(formatter)
    
    def emit(self, record):
        """Emitir log formateado"""
        msg = self.format(record)
        
        # Añadir emoji según nivel
        if record.levelno >= logging.ERROR:
            msg = f"❌ {msg}"
        elif record.levelno >= logging.WARNING:
            msg = f"⚠️ {msg}"
        elif record.levelno >= logging.INFO:
            msg = f"ℹ️ {msg}"
        else:
            msg = f"🔍 {msg}"
        
        self.log_signal.emit(msg)


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
    
    # Handler para archivo
    file_handler = logging.FileHandler('series_manager.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    # Reducir verbosidad de librerías externas
    logging.getLogger('selenium').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('WDM').setLevel(logging.WARNING)
    
    logger.info("=" * 60)
    logger.info("Series Manager iniciado")
    logger.info("=" * 60)
    
    return logger