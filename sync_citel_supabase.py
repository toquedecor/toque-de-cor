"""
sync_citel_supabase.py — Toque de Cor

Sincroniza CADITE + CADMAR + CADGRU do MySQL CITEL para a tabela
citel_itens no Supabase.

Executado automaticamente pelo GitHub Actions diariamente às 06:00 UTC.
Pode ser rodado manualmente: python sync_citel_supabase.py
"""

import os
import sys
from datetime import datetime, timezone

import pymysql
import pymysql.cursors
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

BATCH = 500   # linhas por upsert no Supabase

# ── Conexão MySQL CITEL ───────────────────────────────────────────────────────
def _citel_conn():
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        database=os.environ["MYSQL_DATABASE"],
        connect_timeout=15,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _fetch_citel() -> list[dict]:
    """Busca todos os produtos do CITEL com MARCA e GRUPO."""
    sql = """
        SELECT
            CAST(c.ITE_CODFAB  AS CHAR) AS cod_fab,
            c.ITE_CODITE                AS cod_citel,
            c.ITE_DESITE                AS descricao_db,
            COALESCE(m.MAR_DESMAR, '')  AS marca,
            COALESCE(g.GRU_DESGRU, '')  AS grupo
        FROM CADITE c
        LEFT JOIN CADMAR m ON c.ITE_CODMAR = m.MAR_CODMAR
        LEFT JOIN CADGRU g ON c.ITE_CODGRU = g.GRU_CODGRU
    """
    conn = _citel_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        return rows
    finally:
        conn.close()


# ── Upsert no Supabase ────────────────────────────────────────────────────────
def _upsert_supabase(sb, rows: list[dict]) -> None:
    agora = datetime.now(timezone.utc).isoformat()
    seen: set = set()
    deduped: list[dict] = []
    for row in rows:
        row["atualizado_em"] = agora
        row["cod_fab"]       = str(row["cod_fab"]).strip()
        row["cod_citel"]     = str(row.get("cod_citel") or "").strip()
        row["descricao_db"]  = str(row.get("descricao_db") or "").strip()
        row["marca"]         = str(row.get("marca") or "").strip()
        row["grupo"]         = str(row.get("grupo") or "").strip()
        if row["cod_fab"] not in seen:
            seen.add(row["cod_fab"])
            deduped.append(row)
    rows = deduped

    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        sb.table("citel_itens").upsert(batch, on_conflict="cod_fab").execute()
        print(f"  Upsert {i + len(batch)}/{len(rows)}", end="\r")
    print()


# ── Remover SKUs que sumiram do CITEL ─────────────────────────────────────────
def _remove_obsoletos(sb, skus_citel: set) -> int:
    """Remove registros que não existem mais no CITEL."""
    PAGE = 1000
    offset = 0
    removidos = 0
    while True:
        r = (
            sb.table("citel_itens")
            .select("cod_fab")
            .range(offset, offset + PAGE - 1)
            .execute()
        )
        batch = r.data or []
        obsoletos = [row["cod_fab"] for row in batch if row["cod_fab"] not in skus_citel]
        if obsoletos:
            sb.table("citel_itens").delete().in_("cod_fab", obsoletos).execute()
            removidos += len(obsoletos)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return removidos


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] Iniciando sync CITEL → Supabase...")

    # 1. Busca dados do CITEL
    print("  Conectando ao MySQL CITEL...")
    try:
        rows = _fetch_citel()
    except Exception as e:
        print(f"  ERRO ao conectar ao CITEL: {e}")
        sys.exit(1)
    print(f"  {len(rows)} registros encontrados no CITEL.")

    # 2. Conecta ao Supabase
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    # 3. Garante que a tabela existe
    try:
        sb.table("citel_itens").select("cod_fab").limit(1).execute()
    except Exception:
        print("  Tabela citel_itens não encontrada — crie-a no Supabase primeiro.")
        sys.exit(1)

    # 4. Upsert
    print(f"  Enviando para Supabase (lotes de {BATCH})...")
    _upsert_supabase(sb, list(rows))

    # 5. Remove obsoletos
    skus_citel = {str(r["cod_fab"]).strip() for r in rows}
    removidos = _remove_obsoletos(sb, skus_citel)

    print(f"  Sync concluído: {len(rows)} upserts, {removidos} removidos.")
    print(f"[{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] Pronto!")


if __name__ == "__main__":
    main()
