"""
watch_citel.py — Toque de Cor

Roda em segundo plano enquanto o computador estiver ligado.
Verifica o CADITE a cada INTERVALO_MIN minutos e sincroniza
automaticamente com o Supabase quando detecta qualquer alteração
(novo produto, descrição/marca/grupo alterado, produto removido).

Iniciado automaticamente pelo ToqueDeCor_SyncCITEL.bat no login do Windows.
Log em sync_citel.log (mesmo arquivo do sync_citel_supabase.py).
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Intervalo de verificação em minutos
INTERVALO_MIN = 15

# Diretório do projeto
BASE_DIR = Path(__file__).parent

LOG_FILE = BASE_DIR / "sync_citel.log"


def log(msg: str):
    ts = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    linha = f"[{ts}] {msg}"
    print(linha, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except Exception:
        pass


def run_sync():
    """Chama main() do sync_citel_supabase e loga o resultado."""
    try:
        import sync_citel_supabase as scs
        ok, msg = scs.main(force=False)
        if ok:
            log(f"[watcher] Sync OK: {msg}")
        else:
            log(f"[watcher] Sync ignorado: {msg}")
    except Exception as e:
        log(f"[watcher] Erro inesperado: {e}")


if __name__ == "__main__":
    log(f"[watcher] Iniciado — verificando CADITE a cada {INTERVALO_MIN} min.")

    # Roda uma vez imediatamente ao iniciar
    run_sync()

    while True:
        time.sleep(INTERVALO_MIN * 60)
        run_sync()
