"""
Launcher para distribuição via PyInstaller no Mac.
Empacota o Streamlit + dependências em um .app double-click.

Para criar o .app: execute build_mac.sh em um Mac.
"""
import sys
import os
import shutil
import threading
import webbrowser
import time
from pathlib import Path


def bundle_path(name: str = "") -> Path:
    """Caminho dentro do bundle PyInstaller (somente leitura)."""
    try:
        base = Path(sys._MEIPASS)
    except AttributeError:
        base = Path(__file__).parent
    return base / name if name else base


def data_dir() -> Path:
    """Diretório gravável para dados do usuário (pedidos, similares, cache)."""
    if getattr(sys, "frozen", False):
        # App bundle → ~/Documents/ToqueDeCor/
        d = Path.home() / "Documents" / "ToqueDeCor"
    else:
        d = Path(__file__).parent
    d.mkdir(parents=True, exist_ok=True)
    return d


def _init_user_files():
    """Copia arquivos iniciais do bundle para a pasta de dados se não existirem."""
    dest = data_dir()
    for fname in ("pedidos.json", "similares.json"):
        dst = dest / fname
        if not dst.exists():
            src = bundle_path(fname)
            if src.exists():
                shutil.copy2(src, dst)
            else:
                dst.write_text("[]", encoding="utf-8")


def _open_browser():
    time.sleep(8)
    webbrowser.open("http://localhost:8502")


if __name__ == "__main__":
    _init_user_files()

    # Informa ao app.py e db.py onde salvar dados do usuário
    os.environ["TOQUEDECOR_DATA_DIR"] = str(data_dir())

    threading.Thread(target=_open_browser, daemon=True).start()

    import streamlit.web.bootstrap as bootstrap

    bootstrap.run(
        str(bundle_path("app.py")),
        False,   # is_hello (Streamlit >= 1.35)
        [],      # args
        {
            "server.port":              8502,
            "server.headless":          True,
            "browser.gatherUsageStats": False,
            "server.fileWatcherType":   "none",
        },
    )
