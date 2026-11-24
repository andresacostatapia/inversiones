@echo off
REM ============================================================
REM  CDF Plus - Arranque con doble clic en Windows
REM  1a vez: crea el entorno e instala dependencias (tarda unos minutos)
REM  Siguientes veces: arranca la app directamente
REM ============================================================
cd /d "%~dp0"

REM Detectar Python (python o py)
set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    py --version >nul 2>&1 && set "PY=py"
)
if not defined PY (
    echo.
    echo [ERROR] No se encontro Python.
    echo Instalalo desde https://www.python.org/downloads/
    echo y marca la casilla "Add Python to PATH" durante la instalacion.
    echo.
    pause
    exit /b 1
)

REM Crear entorno virtual si no existe
if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
)

REM Instalar / actualizar dependencias
echo Instalando dependencias (solo tarda la primera vez)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Fallo la instalacion de dependencias. Revisa tu conexion a Internet.
    pause
    exit /b 1
)

echo.
echo Iniciando CDF Plus en http://localhost:8501  (cierra esta ventana para detenerla)
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
pause
