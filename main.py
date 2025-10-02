#!/usr/bin/env python3
"""
Aplicación de Gestión de Biblioteca HispaShare
==============================================

Punto de entrada principal de la aplicación.
"""

import sys
from PyQt6.QtWidgets import QApplication
from src.views.main_window import MainWindow


def main():
    """Función principal de la aplicación."""
    app = QApplication(sys.argv)
    app.setApplicationName("Gestión Biblioteca HispaShare")
    app.setApplicationVersion("1.0.0")
    
    # Crear y mostrar la ventana principal
    window = MainWindow()
    window.show()
    
    # Ejecutar la aplicación
    sys.exit(app.exec())


if __name__ == "__main__":
    main()