# Aplicación de Gestión de Biblioteca HispaShare

Sistema de gestión de biblioteca desarrollado con PyQt6 y Python, diseñado para administrar libros, usuarios y préstamos de manera eficiente.

## Características

- **Gestión de Libros**: Agregar, editar, eliminar y buscar libros
- **Gestión de Usuarios**: Administrar información de usuarios y miembros
- **Sistema de Préstamos**: Control de préstamos y devoluciones
- **Interfaz Gráfica**: Interfaz moderna y amigable desarrollada con PyQt6
- **Base de Datos**: Almacenamiento persistente con SQLite
- **Búsqueda Avanzada**: Filtros y búsquedas por múltiples criterios

## Requisitos del Sistema

- Python 3.8 o superior
- PyQt6
- SQLAlchemy
- Sistema operativo: Windows, macOS, Linux

## Instalación

### 1. Clonar el repositorio
```bash
git clone <url-del-repositorio>
cd "app gestion biblioteca hispashare"
```

### 2. Crear entorno virtual
```bash
python -m venv venv
```

### 3. Activar entorno virtual

**Windows:**
```bash
venv\Scripts\activate
```

**macOS/Linux:**
```bash
source venv/bin/activate
```

### 4. Instalar dependencias
```bash
pip install -r requirements.txt
```

## Uso

### Ejecutar la aplicación
```bash
python main.py
```

## Estructura del Proyecto

```
app gestion biblioteca hispashare/
├── src/                    # Código fuente principal
│   ├── models/            # Modelos de datos
│   ├── views/             # Interfaces de usuario (PyQt6)
│   ├── controllers/       # Lógica de negocio
│   ├── database/          # Gestión de base de datos
│   ├── utils/             # Utilidades y funciones auxiliares
│   └── resources/         # Recursos (iconos, imágenes, estilos)
├── tests/                 # Tests unitarios y de integración
├── docs/                  # Documentación
├── config/                # Archivos de configuración
├── venv/                  # Entorno virtual (no incluido en Git)
├── main.py               # Punto de entrada de la aplicación
├── requirements.txt      # Dependencias del proyecto
├── .gitignore           # Archivos ignorados por Git
└── README.md            # Este archivo
```

## Desarrollo

### Ejecutar tests
```bash
pytest tests/
```

### Formatear código
```bash
black src/
isort src/
```

### Verificar código
```bash
flake8 src/
mypy src/
```

## Contribuir

1. Fork el proyecto
2. Crear una rama para tu feature (`git checkout -b feature/nueva-funcionalidad`)
3. Commit tus cambios (`git commit -am 'Agregar nueva funcionalidad'`)
4. Push a la rama (`git push origin feature/nueva-funcionalidad`)
5. Crear un Pull Request

## Licencia

Este proyecto está bajo la Licencia MIT. Ver el archivo `LICENSE` para más detalles.

## Contacto

HispaShare Team - [contacto@hispashare.com]

Enlace del proyecto: [https://github.com/hispashare/gestion-biblioteca]