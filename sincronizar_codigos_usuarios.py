"""
sincronizar_codigos_usuarios.py — Toque de Cor

Lê USUARIOS_TOQUE_DE_COR.xlsx e atualiza o campo `codigo` na tabela
`usuarios` do Supabase com o OPE_CODOPE (zero-padded para 3 dígitos).

A correspondência é feita pelo NOME (OPE_NOMOPE ↔ nome), ignorando
maiúsculas/minúsculas e espaços extras.

Uso:
  python sincronizar_codigos_usuarios.py
  python sincronizar_codigos_usuarios.py --dry-run   (apenas exibe o que seria feito)
"""

import sys
import os
import unicodedata
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

EXCEL_PATH   = os.path.join(os.path.dirname(__file__), "USUARIOS_TOQUE_DE_COR.xlsx")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://hevhowwfweobmihzvenf.supabase.co")
SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY", "")

DRY_RUN = "--dry-run" in sys.argv

HEADERS = {
    "apikey":        SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}


def _norm(text: str) -> str:
    """Normaliza string para comparação: sem acentos, maiúsculas, espaços extras."""
    nfd = unicodedata.normalize("NFD", str(text or "").strip().upper())
    return " ".join("".join(c for c in nfd if unicodedata.category(c) != "Mn").split())


def _listar_usuarios() -> list[dict]:
    """Busca todos os usuários do Supabase (paginado)."""
    usuarios = []
    PAGE = 1000
    offset = 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/usuarios",
            headers={**HEADERS, "Range": f"{offset}-{offset + PAGE - 1}",
                     "Range-Unit": "items"},
            params={"select": "id,usuario,nome,codigo"},
            timeout=15,
        )
        r.raise_for_status()
        batch = r.json()
        usuarios.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return usuarios


def _update_codigo(usuario_login: str, codigo: str) -> bool:
    """Atualiza o campo codigo de um usuário."""
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/usuarios",
        headers=HEADERS,
        params={"usuario": f"eq.{usuario_login}"},
        json={"codigo": codigo},
        timeout=10,
    )
    return r.status_code in (200, 204)


def main():
    print(f"\n{'='*55}")
    print("  Sincronizando códigos de operadores")
    if DRY_RUN:
        print("  *** DRY RUN — nenhuma alteração será salva ***")
    print(f"{'='*55}\n")

    # 1. Lê Excel
    if not os.path.exists(EXCEL_PATH):
        print(f"ERRO: planilha não encontrada em {EXCEL_PATH}")
        sys.exit(1)

    df = pd.read_excel(EXCEL_PATH)
    df["codigo_fmt"] = df["OPE_CODOPE"].astype(int).apply(lambda x: str(x).zfill(3))
    df["nome_norm"]  = df["OPE_NOMOPE"].apply(_norm)
    print(f"  {len(df)} operadores na planilha.\n")

    # 2. Busca usuários do Supabase
    print("  Buscando usuários no Supabase...")
    try:
        usuarios = _listar_usuarios()
    except Exception as e:
        print(f"  ERRO ao buscar usuários: {e}")
        sys.exit(1)
    print(f"  {len(usuarios)} usuários encontrados.\n")

    # Índice por nome normalizado
    usuarios_por_nome: dict[str, list[dict]] = {}
    for u in usuarios:
        key = _norm(u.get("nome", ""))
        usuarios_por_nome.setdefault(key, []).append(u)

    # 3. Faz a correspondência e atualiza
    atualizados = 0
    sem_mudanca = 0
    nao_encontrados = []

    for _, row in df.iterrows():
        codigo   = row["codigo_fmt"]
        nome_key = row["nome_norm"]
        matches  = usuarios_por_nome.get(nome_key, [])

        if not matches:
            nao_encontrados.append(f"  [NÃO ENCONTRADO] {row['OPE_CODOPE']:>4} | {row['OPE_NOMOPE']}")
            continue

        for u in matches:
            atual = str(u.get("codigo") or "").strip()
            if atual == codigo:
                sem_mudanca += 1
                continue
            print(f"  [{u['usuario']}] {row['OPE_NOMOPE'][:35]:<35} {atual or '---':>4} -> {codigo}")
            if not DRY_RUN:
                ok = _update_codigo(u["usuario"], codigo)
                if ok:
                    atualizados += 1
                else:
                    print(f"    AVISO: falha ao atualizar {u['usuario']}")
            else:
                atualizados += 1

    # 4. Relatório
    print(f"\n{'='*55}")
    print(f"  Atualizados : {atualizados}")
    print(f"  Sem mudança : {sem_mudanca}")
    if nao_encontrados:
        print(f"\n  Não encontrados no sistema ({len(nao_encontrados)}):")
        for m in nao_encontrados:
            print(m)
    print(f"{'='*55}\n")
    if DRY_RUN:
        print("  *** DRY RUN concluído — nenhuma alteração foi salva ***\n")


if __name__ == "__main__":
    main()
