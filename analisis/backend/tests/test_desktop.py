"""Lanzador de escritorio: carpeta de datos del usuario y prueba de arranque."""

import importlib.util
import json
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "desktop" / "launcher.py"


@pytest.fixture(scope="module")
def launcher():
    spec = importlib.util.spec_from_file_location("launcher", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prepare_home_copies_defaults_once(launcher, tmp_path):
    res = Path(__file__).resolve().parents[2]
    home = tmp_path / "home"
    launcher.prepare_home(home, res)
    assert (home / "config.yaml").exists() and (home / ".env").exists() and (home / "data").is_dir()
    (home / "config.yaml").write_text("watchlist: [SPY]\n", encoding="utf-8")
    launcher.prepare_home(home, res)  # no sobrescribe lo que el usuario cambió
    assert (home / "config.yaml").read_text(encoding="utf-8") == "watchlist: [SPY]\n"


def test_pick_port_returns_free_port(launcher):
    port = launcher.pick_port()
    assert launcher.port_free(port)


def test_smoke_run_from_source(launcher, tmp_path, monkeypatch):
    # setenv registra el estado original: al terminar, monkeypatch deja el entorno como estaba
    # (main() fija estas variables para el resto del proceso).
    monkeypatch.setenv("ANALISIS_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("ANALISIS_FRONTEND_DIST", str(tmp_path / "sin-dashboard"))
    from app import config

    config.get_config.cache_clear()
    config.get_secrets.cache_clear()
    rc = launcher.main(["--smoke", "--home", str(tmp_path / "h")])
    config.get_config.cache_clear()
    config.get_secrets.cache_clear()
    assert rc in (0, 3)  # 3 = la API respondió pero no hay dashboard compilado (frontend/dist)
    result = json.loads((tmp_path / "h" / "smoke.json").read_text(encoding="utf-8"))
    assert result["url"].startswith("http://127.0.0.1:")
    assert (tmp_path / "h" / "data" / "analisis.db").exists()
