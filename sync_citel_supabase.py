"""
sync_citel_supabase.py — Toque de Cor

Sincroniza CADITE + CADMAR + CADGRU do MySQL CITEL para a tabela
citel_itens no Supabase.

Executado automaticamente pelo GitHub Actions a cada hora.
Detecta alterações na CADITE antes de sincronizar (skip se sem mudanças).
Força sync completo quando SYNC_FORCE=true (disparado pelo admin na importação).

Uso manual: python sync_citel_supabase.py [--force]
"""

import os
import sys
from datetime import datetime, timezone, timedelta

import pymysql
import pymysql.cursors
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

BATCH = 500   # linhas por upsert no Supabase
MAX_HORAS_SEM_SYNC = 12  # força sync mesmo sem mudanças se passou X horas

# ── Conexão MySQL CITEL ───────────────────────────────────────────────────────
def _citel_conn():
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        database=os.environ["MYSQL_DATABASE"],
        connect_timeout=30,
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


# ── Re-enriquecimento do catálogo após sync CITEL ─────────────────────────────
def _reenrich_catalogo(sb, citel_lookup: dict) -> int:
    """
    Atualiza as colunas CITEL (cod_citel, marca, grupo, descricao_db, desc_final)
    no catálogo para todos os SKUs presentes em citel_lookup.
    Chamado após cada sync bem-sucedido para garantir que novos itens CITEL
    apareçam no catálogo sem precisar subir um novo Excel.

    citel_lookup = {cod_fab: {cod_citel, marca, grupo, descricao_db}}
    Retorna o número de linhas do catálogo efetivamente atualizadas.
    """
    if not citel_lookup:
        return 0

    skus = list(citel_lookup.keys())
    updated = 0

    # Busca linhas do catálogo para esses SKUs em lotes de 500
    PAGE = 500
    for i in range(0, len(skus), PAGE):
        batch_skus = skus[i:i + PAGE]
        r = (
            sb.table("catalogo")
            .select("id,cod_sku,descricao,cor,cod_citel,descricao_db,marca,grupo,desc_final")
            .in_("cod_sku", batch_skus)
            .execute()
        )
        rows = r.data or []

        to_update = []
        for row in rows:
            citel = citel_lookup.get(str(row["cod_sku"]).strip())
            if not citel:
                continue

            new_cod_citel    = str(citel.get("cod_citel") or "").strip()
            new_marca        = str(citel.get("marca") or "").strip()
            new_grupo        = str(citel.get("grupo") or "").strip()
            # descricao_db: usa o do CITEL; se vazio, mantém descricao original como fallback
            new_descricao_db = str(citel.get("descricao_db") or "").strip() or str(row.get("descricao") or "").strip()
            cor              = str(row.get("cor") or "").strip()
            new_desc_final   = (new_descricao_db + " — " + cor) if cor else new_descricao_db

            # Só inclui no batch se algum campo realmente mudou
            if (str(row.get("cod_citel") or "").strip()    != new_cod_citel    or
                    str(row.get("marca") or "").strip()        != new_marca        or
                    str(row.get("grupo") or "").strip()        != new_grupo        or
                    str(row.get("descricao_db") or "").strip() != new_descricao_db or
                    str(row.get("desc_final") or "").strip()   != new_desc_final):
                to_update.append({
                    "id":           row["id"],
                    "cod_citel":    new_cod_citel,
                    "marca":        new_marca,
                    "grupo":        new_grupo,
                    "descricao_db": new_descricao_db,
                    "desc_final":   new_desc_final,
                })

        if to_update:
            for j in range(0, len(to_update), BATCH):
                sb.table("catalogo").upsert(to_update[j:j + BATCH]).execute()
            updated += len(to_update)

    return updated


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


# ── Detecção de mudanças ──────────────────────────────────────────────────────
_FP_KEY      = "citel_fingerprint"
_TS_KEY      = "citel_ultimo_sync"
_CONFIG_TBL  = "configuracoes"


def _fingerprint_citel() -> str:
    """
    Fingerprint do CADITE que detecta:
    - Novos produtos (COUNT)
    - Produtos removidos (COUNT)
    - Alteracoes em descricao, marca ou grupo (checksum via SUM+LENGTH)
    """
    conn = _citel_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*)                                        AS cnt,
                    MAX(CAST(ITE_CODFAB AS CHAR))                   AS max_fab,
                    SUM(LENGTH(COALESCE(ITE_DESITE, '')))           AS sum_desc,
                    SUM(LENGTH(COALESCE(
                        (SELECT MAR_DESMAR FROM CADMAR WHERE MAR_CODMAR = c.ITE_CODMAR), ''
                    )))                                             AS sum_mar,
                    SUM(LENGTH(COALESCE(
                        (SELECT GRU_DESGRU FROM CADGRU WHERE GRU_CODGRU = c.ITE_CODGRU), ''
                    )))                                             AS sum_gru
                FROM CADITE c
            """)
            r = cur.fetchone()
        return f"{r['cnt']}|{r['max_fab']}|{r['sum_desc']}|{r['sum_mar']}|{r['sum_gru']}"
    finally:
        conn.close()


def _get_config(sb, chave: str) -> str:
    r = sb.table(_CONFIG_TBL).select("valor").eq("chave", chave).execute()
    return (r.data[0]["valor"] if r.data else "") or ""


def _set_config(sb, chave: str, valor: str) -> None:
    sb.table(_CONFIG_TBL).upsert({"chave": chave, "valor": valor}).execute()


def _precisa_sincronizar(sb, force: bool) -> tuple[bool, str]:
    """Retorna (True, motivo) se o sync deve rodar."""
    if force:
        return True, "forçado pela importação de planilha"

    # Verifica última execução (segurança: sincroniza sempre após MAX_HORAS_SEM_SYNC)
    ultimo_ts = _get_config(sb, _TS_KEY)
    if ultimo_ts:
        try:
            dt_ult = datetime.fromisoformat(ultimo_ts)
            if datetime.now(timezone.utc) - dt_ult > timedelta(hours=MAX_HORAS_SEM_SYNC):
                return True, f"mais de {MAX_HORAS_SEM_SYNC}h desde o último sync"
        except ValueError:
            pass

    # Compara fingerprint do CADITE com o último sync
    print("  Verificando mudanças no CADITE...")
    try:
        fp_atual = _fingerprint_citel()
    except Exception as e:
        return False, f"ERRO ao checar fingerprint: {e}"

    fp_salvo = _get_config(sb, _FP_KEY)
    if fp_atual != fp_salvo:
        return True, f"CADITE alterado  ({fp_salvo or 'sem registro'} → {fp_atual})"

    return False, f"CADITE sem mudanças ({fp_atual})"


# ── Main ──────────────────────────────────────────────────────────────────────
def main(force: bool = False) -> tuple[bool, str]:
    """
    Executa o sync CITEL → Supabase.
    Retorna (sucesso: bool, mensagem: str).
    Pode ser chamado inline (ex: do admin.py) sem risco de sys.exit().
    """
    print(f"[{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] Verificando CITEL -> Supabase...")

    # 1. Conecta ao Supabase
    try:
        sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    except Exception as e:
        return False, f"Supabase indisponível: {e}"

    # 2. Decide se precisa sincronizar
    deve, motivo = _precisa_sincronizar(sb, force)
    if not deve:
        print(f"  >>  Nada a fazer -- {motivo}.")
        print(f"[{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] Concluido sem alteracoes.")
        return True, f"Sem alteracoes -- {motivo}"

    print(f"  >  Sincronizando -- {motivo}.")

    # 3. Busca dados do CITEL
    print("  Conectando ao MySQL CITEL...")
    try:
        rows = _fetch_citel()
    except Exception as e:
        msg = f"MySQL CITEL inacessível: {e}"
        print(f"  AVISO: {msg}")
        print("  Sync ignorado — execute localmente onde o CITEL é acessível.")
        return False, msg
    print(f"  {len(rows)} registros encontrados no CITEL.")

    # 4. Garante que a tabela existe
    try:
        sb.table("citel_itens").select("cod_fab").limit(1).execute()
    except Exception as e:
        return False, f"Tabela citel_itens não encontrada no Supabase: {e}"

    # 5. Upsert
    print(f"  Enviando para Supabase (lotes de {BATCH})...")
    _upsert_supabase(sb, list(rows))

    # 6. Remove obsoletos
    skus_citel = {str(r["cod_fab"]).strip() for r in rows}
    removidos = _remove_obsoletos(sb, skus_citel)

    # 7. Re-enriquece catálogo com os dados CITEL atualizados
    print("  Atualizando catálogo com dados CITEL novos/alterados...")
    citel_lookup = {
        str(r["cod_fab"]).strip(): {
            "cod_citel":    str(r.get("cod_citel") or "").strip(),
            "marca":        str(r.get("marca") or "").strip(),
            "grupo":        str(r.get("grupo") or "").strip(),
            "descricao_db": str(r.get("descricao_db") or "").strip(),
        }
        for r in rows
    }
    n_cat = _reenrich_catalogo(sb, citel_lookup)
    if n_cat:
        print(f"  {n_cat} linhas do catálogo atualizadas com dados CITEL.")

    # 8. Salva fingerprint e timestamp do sync
    fp_novo = _fingerprint_citel()
    _set_config(sb, _FP_KEY, fp_novo)
    _set_config(sb, _TS_KEY, datetime.now(timezone.utc).isoformat())

    n_dedup = len({str(r["cod_fab"]).strip() for r in rows})
    resumo = f"{n_dedup} produtos sincronizados, {removidos} removidos" + (f", {n_cat} linhas do catálogo atualizadas" if n_cat else "")
    print(f"  Sync concluido: {resumo}.")
    print(f"[{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] Pronto!")
    return True, resumo



if __name__ == "__main__":
    _force = "--force" in sys.argv or os.environ.get("SYNC_FORCE", "").lower() == "true"
    ok, msg = main(force=_force)
    sys.exit(0 if ok else 1)
