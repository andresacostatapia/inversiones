"""App de escritorio: arranca el servidor local y lo muestra en una ventana propia.

- Los datos (config.yaml, .env, base de datos, registros) viven en la carpeta del usuario, no junto al programa:
  así una versión nueva del .exe no borra tu cartera.
    Windows: %LOCALAPPDATA%\\AnalisisInversiones
    macOS:   ~/Library/Application Support/AnalisisInversiones
    Linux:   ~/.local/share/analisis-inversiones
- El servidor solo escucha en 127.0.0.1 (nada queda expuesto a la red).
- Ventana: pywebview (WebView2 en Windows). Si no está disponible, se abre el navegador y una ventanita para cerrar.

Uso:  AnalisisInversiones.exe            (doble clic)
      AnalisisInversiones.exe --smoke    (prueba: arranca, verifica la API y el dashboard, y sale)
      AnalisisInversiones.exe --home DIR (otra carpeta de datos)
      AnalisisInversiones.exe --headless (solo el servidor, para usarlo desde el navegador; Ctrl+C para salir)
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_NAME = "AnalisisInversiones"
TITLE = "Análisis de acciones y ETF"
PREFERRED_PORT = 8765


def bundle_dir() -> Path:
    """Dónde están los recursos: dentro del paquete de PyInstaller o en el repositorio."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]  # analisis/


def default_home() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "analisis-inversiones"


def prepare_home(home: Path, res: Path) -> None:
    """Primera vez: copia config.yaml y la plantilla .env a la carpeta del usuario. Nunca sobrescribe."""
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(exist_ok=True)
    for name in ("config.yaml", ".env.example"):
        src, dst = res / name, home / name
        if src.exists() and not dst.exists():
            shutil.copyfile(src, dst)
    if not (home / ".env").exists() and (home / ".env.example").exists():
        shutil.copyfile(home / ".env.example", home / ".env")


def setup_logging(home: Path) -> None:
    handler = logging.handlers.RotatingFileHandler(home / "logs" / "app.log", maxBytes=2_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    if sys.stderr is not None:  # en el .exe sin consola stderr es None
        root.addHandler(logging.StreamHandler())
    root.setLevel(logging.INFO)


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def our_server_on(port: int) -> bool:
    """¿Ya hay una instancia de esta app escuchando en ese puerto?"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            return "disclaimer" in json.loads(r.read().decode())
    except Exception:
        return False


def pick_port() -> int:
    if port_free(PREFERRED_PORT):
        return PREFERRED_PORT
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(url: str, timeout: float = 90) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def start_server(port: int, start_poller: bool):
    import uvicorn

    from app.main import create_app  # importar recién ahora: lee ANALISIS_HOME

    app = create_app(start_poller=start_poller)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False))
    t = threading.Thread(target=server.run, name="servidor", daemon=True)
    t.start()
    return server, t


def show_window(url: str, home: Path) -> None:
    try:
        import webview

        webview.create_window(TITLE, url, width=1440, height=920, min_size=(380, 600), text_select=True)
        webview.start()
        return
    except Exception as e:  # sin WebView2 u otro problema: navegador + ventanita para cerrar
        logging.warning("ventana propia no disponible (%s); se usa el navegador", e)
    import webbrowser

    webbrowser.open(url)
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title(TITLE)
        tk.Label(root, text=f"La app está corriendo en\n{url}\n\nTus datos: {home}", padx=24, pady=16,
                 justify="left").pack()
        tk.Button(root, text="Abrir en el navegador", command=lambda: webbrowser.open(url)).pack(pady=4)
        tk.Button(root, text="Cerrar la app", command=root.destroy).pack(pady=(4, 16))
        root.mainloop()
    except Exception:
        while True:  # último recurso: seguir sirviendo hasta que se cierre el proceso
            time.sleep(3600)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog=APP_NAME, description=TITLE)
    p.add_argument("--home", type=Path, default=None, help="carpeta de datos (por defecto, la del usuario)")
    p.add_argument("--smoke", action="store_true", help="prueba de arranque sin ventana ni red")
    p.add_argument("--headless", action="store_true", help="solo el servidor, sin ventana (usar el navegador)")
    p.add_argument("--port", type=int, default=None)
    args = p.parse_args(argv)

    res = bundle_dir()
    home = (args.home or default_home()).resolve()
    prepare_home(home, res)
    setup_logging(home)
    os.environ["ANALISIS_HOME"] = str(home)
    dist = res / ("frontend_dist" if getattr(sys, "frozen", False) else "frontend/dist")
    os.environ.setdefault("ANALISIS_FRONTEND_DIST", str(dist))
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(res / "backend"))
    logging.info("inicio: datos en %s, recursos en %s", home, res)

    if not args.smoke and not args.headless and not args.port and our_server_on(PREFERRED_PORT):
        # Ya está abierta: mostrar esa misma instancia en vez de levantar otra sobre la misma base de datos.
        show_window(f"http://127.0.0.1:{PREFERRED_PORT}", home)
        return 0

    port = args.port or pick_port()
    url = f"http://127.0.0.1:{port}"
    server, thread = start_server(port, start_poller=not args.smoke)
    if not wait_ready(url):
        logging.error("el servidor no respondió en %s", url)
        return 2

    if args.smoke:
        with urllib.request.urlopen(url + "/api/status", timeout=10) as r:
            status = json.loads(r.read().decode())
        try:
            with urllib.request.urlopen(url + "/", timeout=10) as r:
                page = r.read().decode()
        except Exception as e:  # sin dashboard compilado: la API anda, pero no hay página
            logging.error("dashboard no disponible: %s", e)
            page = ""
        ok = "disclaimer" in status and "<div id=\"root\">" in page
        result = json.dumps({"ok": ok, "home": str(home), "url": url, "dashboard": "<div id=\"root\">" in page})
        (home / "smoke.json").write_text(result, encoding="utf-8")  # el .exe sin consola no tiene stdout
        print(result)
        server.should_exit = True
        thread.join(timeout=10)
        return 0 if ok else 3

    if args.headless:
        print(f"Servidor en {url} (datos en {home}). Ctrl+C para salir.", flush=True)
        try:
            while thread.is_alive():
                thread.join(timeout=1)
        except KeyboardInterrupt:
            pass
    else:
        show_window(url, home)
    server.should_exit = True
    thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
