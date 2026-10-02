@echo off
setlocal
title WolfMax4K Downloader

if "%~1"=="" (
    echo.
    echo  Uso: arrastra una URL encima de este archivo
    echo  O ejecuta: descargar.bat https://wolfmax4k.com/...
    echo.
    set /p URL="Pega la URL de wolfmax4k.com: "
) else (
    set URL=%~1
)

echo.
echo [*] Analizando: %URL%
echo.

python "%~dp0wolfmax_dl.py" "%URL%" -o "%USERPROFILE%\Downloads"

echo.
pause
