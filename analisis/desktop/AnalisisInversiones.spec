# PyInstaller: empaqueta el servidor + el dashboard compilado en una carpeta con AnalisisInversiones(.exe).
#
#   cd analisis
#   (cd frontend && npm ci && npm run build)
#   pip install -r backend/requirements.txt -r desktop/requirements.txt
#   pyinstaller --noconfirm desktop/AnalisisInversiones.spec
#   → dist/AnalisisInversiones/   (en GitHub Actions se comprime como .zip)
#
# Modo carpeta (onedir) y no un solo .exe: arranca mucho más rápido porque no se descomprime en cada inicio.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent  # analisis/

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend_dist"),
    (str(ROOT / "config.yaml"), "."),
    (str(ROOT / ".env.example"), "."),
]
binaries = []
hiddenimports = collect_submodules("app") + [
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
]
# Paquetes con datos o módulos que PyInstaller no descubre solo.
for pkg in ("exchange_calendars", "yfinance", "curl_cffi", "tzdata", "webview"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # p. ej. pywebview no instalado en una prueba local
        continue
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(ROOT / "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "respx", "tkinter.test", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AnalisisInversiones",
    console=False,  # sin ventana negra de consola
    icon=str(ROOT / "desktop" / "icon.ico") if (ROOT / "desktop" / "icon.ico").exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AnalisisInversiones")
