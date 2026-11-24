@echo off
REM ============================================================
REM  CDF Plus - Bot de ejecucion automatica (doble clic)
REM  El modo (paper / demo / real) se configura en el archivo .env
REM ============================================================
cd /d "%~dp0"

set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    py --version >nul 2>&1 && set "PY=py"
)
if not defined PY (
    echo [ERROR] No se encontro Python. Instalalo desde https://www.python.org/downloads/
    echo y marca "Add Python to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual...
    %PY% -m venv .venv
)
echo Verificando dependencias (incluye MetaTrader5)...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de dependencias.
    pause
    exit /b 1
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Se creo el archivo .env con BOT_MODE=paper (simulado). Editalo para cambiar de modo.
)

echo.
echo ===========================================================
echo  Bot CDF Plus en marcha. Cierra esta ventana o pulsa Ctrl+C
echo  para detenerlo. Panel de control: app -> pagina "Bot MT5"
echo ===========================================================
echo.
".venv\Scripts\python.exe" bot.py
pause
