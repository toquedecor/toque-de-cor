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


# UF → empresa(s) no ITEGER
_UF_EMPRESAS = {
    "rn": ("017", "018", "019"),
    "ba": ("009",),
    "pe": ("001", "004", "005", "010", "011", "012", "013", "014", "015", "016"),
    "al": ("002", "006"),
    "pb": ("003", "007", "008"),
}


def _fetch_citel() -> list[dict]:
    """
    Busca todos os produtos do CITEL com MARCA, GRUPO, EMBALAGEM e PREÇO DE CUSTO por UF.

    Estratégia de 2 queries para evitar 5 subqueries correlacionadas lentas:
      1. CADITE + CADMAR + CADGRU + CADUNI  → dados cadastrais de cada item
      2. ITEGER (todas as empresas de todas as UFs) → preços por empresa
    Depois junta no Python: para cada item, pega o preço mais recente por UF.
    """
    conn = _citel_conn()
    try:
        # ── Query 1: Cadastro de itens ────────────────────────────────────────
        sql_cadite = """
            SELECT
                CAST(c.ITE_CODFAB AS CHAR) AS cod_fab,
                c.ITE_CODITE               AS cod_citel,
                c.ITE_DESITE               AS descricao_db,
                COALESCE(m.MAR_DESMAR, '') AS marca,
                COALESCE(g.GRU_DESGRU, '') AS grupo,
                COALESCE(u.UNI_SIGUNI, '') AS embalagem_db
            FROM CADITE c
            LEFT JOIN CADMAR m ON c.ITE_CODMAR = m.MAR_CODMAR
            LEFT JOIN CADGRU g ON c.ITE_CODGRU = g.GRU_CODGRU
            LEFT JOIN CADUNI u ON c.ITE_UNICOM  = u.UNI_CODUNI
        """
        with conn.cursor() as cur:
            cur.execute(sql_cadite)
            cadite_rows = cur.fetchall()

        # ── Query 2: Preços de custo por empresa ──────────────────────────────
        # Reúne todas as empresas de todas as UFs
        todas_empresas = sorted(set(
            e for emps in _UF_EMPRESAS.values() for e in emps
        ))
        codes_str = ",".join(f"'{e}'" for e in todas_empresas)
        sql_iteger = f"""
            SELECT ITE_CODITE, ITE_CODEMP, ITE_PRECUS, ITE_DTAULT
            FROM ITEGER
            WHERE ITE_CODEMP IN ({codes_str})
        """
        with conn.cursor() as cur:
            cur.execute(sql_iteger)
            iteger_rows = cur.fetchall()
    finally:
        conn.close()

    # ── Monta lookup: cod_citel → {empresa: (data, preco)} ───────────────────
    # Para cada item+empresa, guarda o registro com a data mais recente
    preco_lookup: dict[str, dict[str, tuple]] = {}  # {cod_citel: {emp: (dt, preco)}}
    for row in iteger_rows:
        cit  = str(row["ITE_CODITE"]).strip()
        emp  = str(row["ITE_CODEMP"]).strip()
        prec = float(row["ITE_PRECUS"] or 0)
        dt   = row["ITE_DTAULT"]  # pode ser None
        prev = preco_lookup.setdefault(cit, {}).get(emp)
        # Prefere data mais recente; None é "mais antigo" que qualquer data
        if prev is None or (dt is not None and (prev[0] is None or dt > prev[0])):
            preco_lookup[cit][emp] = (dt, prec)

    def _uf_preco(cod_citel: str, empresas: tuple) -> float:
        """Retorna o preço mais recente entre todas as empresas de uma UF."""
        cit_data = preco_lookup.get(str(cod_citel).strip(), {})
        melhor_dt: object = None
        melhor_preco: float = 0.0
        for emp in empresas:
            entry = cit_data.get(emp)
            if entry is None:
                continue
            dt, prec = entry
            if melhor_dt is None or (dt is not None and (melhor_dt is None or dt > melhor_dt)):
                melhor_dt = dt
                melhor_preco = prec
        return melhor_preco

    # ── Combina os dados ──────────────────────────────────────────────────────
    result = []
    for row in cadite_rows:
        cit = row["cod_citel"]
        row["preco_compra_rn"] = _uf_preco(cit, _UF_EMPRESAS["rn"])
        row["preco_compra_ba"] = _uf_preco(cit, _UF_EMPRESAS["ba"])
        row["preco_compra_pe"] = _uf_preco(cit, _UF_EMPRESAS["pe"])
        row["preco_compra_al"] = _uf_preco(cit, _UF_EMPRESAS["al"])
        row["preco_compra_pb"] = _uf_preco(cit, _UF_EMPRESAS["pb"])
        result.append(row)
    return result


# ── Upsert no Supabase ────────────────────────────────────────────────────────
def _upsert_supabase(sb, rows: list[dict]) -> None:
    agora = datetime.now(timezone.utc).isoformat()
    seen: set = set()
    deduped: list[dict] = []
    for row in rows:
        row["atualizado_em"]    = agora
        row["cod_fab"]          = str(row["cod_fab"]).strip()
        row["cod_citel"]        = str(row.get("cod_citel") or "").strip()
        row["descricao_db"]     = str(row.get("descricao_db") or "").strip()
        row["marca"]            = str(row.get("marca") or "").strip()
        row["grupo"]            = str(row.get("grupo") or "").strip()
        row["embalagem_db"]     = str(row.get("embalagem_db") or "").strip()
        row["preco_compra_rn"]  = float(row.get("preco_compra_rn") or 0)
        row["preco_compra_ba"]  = float(row.get("preco_compra_ba") or 0)
        row["preco_compra_pe"]  = float(row.get("preco_compra_pe") or 0)
        row["preco_compra_al"]  = float(row.get("preco_compra_al") or 0)
        row["preco_compra_pb"]  = float(row.get("preco_compra_pb") or 0)
        if row["cod_fab"] not in seen:
            seen.add(row["cod_fab"])
            deduped.append(row)
    rows = deduped

    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        sb.table("citel_itens").upsert(batch, on_conflict="cod_fab").execute()
        print(f"  Upsert {i + len(batch)}/{len(rows)}", end="\r")
    print()


# Mapeamento UF → campo de preço de custo no citel_lookup
_UF_PRECO_KEY = {
    "rn": "preco_compra_rn",
    "ba": "preco_compra_ba",
    "pe": "preco_compra_pe",
    "al": "preco_compra_al",
    "pb": "preco_compra_pb",
}


# ── Re-enriquecimento do catálogo após sync CITEL ─────────────────────────────
_BASE_CAT_COLS  = ("id,uf,cod_sku,linha,descricao,embalagem,cor,preco,"
                   "cod_citel,descricao_db,marca,grupo,desc_final")
_EXTRA_CAT_COLS = "embalagem_db,preco_compra"


def _get_citel_skus(sb) -> set:
    """Retorna conjunto de SKUs adicionados ao catálogo pelo sync CITEL (não do Excel)."""
    import json
    val = _get_config(sb, "citel_added_skus")
    if val:
        try:
            return set(json.loads(val))
        except Exception:
            pass
    return set()


def _save_citel_skus(sb, skus: set) -> None:
    """Persiste conjunto de SKUs de origem CITEL no Supabase."""
    import json
    _set_config(sb, "citel_added_skus", json.dumps(sorted(skus)))


def _add_novos_catalogo(sb, citel_lookup: dict, citel_skus: set) -> int:
    """
    Insere no catálogo produtos que existem no CITEL mas ainda não estão lá.
    Cria 1 linha por UF para cada produto novo, com preco = preco_compra.
    Atualiza citel_skus in-place com os novos SKUs adicionados.
    Retorna o número de SKUs novos inseridos.
    """
    PAGE = 1000
    existing_skus: set = set()
    offset = 0
    while True:
        r = (sb.table("catalogo")
             .select("cod_sku")
             .range(offset, offset + PAGE - 1)
             .execute())
        for row in (r.data or []):
            existing_skus.add(str(row["cod_sku"]).strip())
        if len(r.data or []) < PAGE:
            break
        offset += PAGE

    novos = [s for s in citel_lookup.keys() if s and s not in existing_skus]
    if not novos:
        return 0

    to_insert = []
    for sku in novos:
        citel    = citel_lookup[sku]
        desc_db  = str(citel.get("descricao_db") or "").strip()
        emb_db   = str(citel.get("embalagem_db") or "").strip()
        cod_cit  = str(citel.get("cod_citel") or "").strip()
        marca    = str(citel.get("marca") or "").strip()
        grupo    = str(citel.get("grupo") or "").strip()
        for uf_upper in ("RN", "BA", "PE", "AL", "PB"):
            preco_key    = _UF_PRECO_KEY.get(uf_upper.lower())
            preco_compra = float(citel.get(preco_key) or 0) if preco_key else 0.0
            to_insert.append({
                "uf":           uf_upper,
                "cod_sku":      sku,
                "linha":        999999,
                "descricao":    desc_db,
                "descricao_db": desc_db,
                "desc_final":   desc_db,
                "cor":          "",
                "preco":        preco_compra,
                "preco_compra": preco_compra,
                "cod_citel":    cod_cit,
                "marca":        marca,
                "grupo":        grupo,
                "embalagem":    emb_db,
                "embalagem_db": emb_db,
            })

    for i in range(0, len(to_insert), BATCH):
        sb.table("catalogo").insert(to_insert[i:i + BATCH]).execute()

    citel_skus.update(novos)
    return len(novos)


def _reenrich_catalogo(sb, citel_lookup: dict, citel_skus: set | None = None) -> int:
    """
    Atualiza as colunas CITEL no catálogo: cod_citel, marca, grupo, descricao_db,
    desc_final, embalagem_db, embalagem (se vazia) e preco_compra (por UF).

    Processa UF por UF para nunca exceder 1000 rows por SELECT (limite PostgREST).

    citel_lookup = {
        cod_fab: {
            cod_citel, marca, grupo, descricao_db, embalagem_db,
            preco_compra_rn, preco_compra_ba, preco_compra_pe,
            preco_compra_al, preco_compra_pb
        }
    }
    citel_skus: conjunto de SKUs adicionados pelo sync (não do Excel).
               Para esses itens, preco é atualizado junto com preco_compra.
    Retorna o número de linhas do catálogo efetivamente atualizadas.
    """
    _citel_skus = citel_skus or set()
    if not citel_lookup:
        return 0

    skus = [s for s in citel_lookup.keys() if s and s != "None"]
    updated = 0
    has_extra = True   # tenta com embalagem_db,preco_compra; cai p/ base se 42703

    # Processa UF por UF: cada batch de PAGE skus retorna no máximo PAGE rows
    # (1 por SKU por UF), evitando o limite de 1000 rows do PostgREST.
    PAGE = 500
    for uf_upper in ("RN", "BA", "PE", "AL", "PB"):
        uf_lower  = uf_upper.lower()
        preco_key = _UF_PRECO_KEY.get(uf_lower)
        for i in range(0, len(skus), PAGE):
            batch_skus = skus[i:i + PAGE]

            # Two-pass SELECT: com colunas extras → sem, se coluna não existir ainda
            rows: list[dict] = []
            for attempt in range(2):
                select_cols = (_BASE_CAT_COLS + "," + _EXTRA_CAT_COLS
                               if has_extra else _BASE_CAT_COLS)
                try:
                    r = (
                        sb.table("catalogo")
                        .select(select_cols)
                        .eq("uf", uf_upper)
                        .in_("cod_sku", batch_skus)
                        .execute()
                    )
                    rows = r.data or []
                    break
                except Exception as ex:
                    err = str(ex)
                    if ("42703" in err or "does not exist" in err) and has_extra:
                        has_extra = False
                        continue   # retry sem extras
                    rows = []
                    break

            to_update = []
            for row in rows:
                # Pula rows corrompidas (NOT NULL violadas por sync anterior)
                if not row.get("uf") or not row.get("cod_sku"):
                    continue

                citel = citel_lookup.get(str(row["cod_sku"]).strip())
                if not citel:
                    continue

                new_preco_compra  = float(citel.get(preco_key) or 0) if preco_key else 0.0

                new_embalagem_db  = str(citel.get("embalagem_db") or "").strip()
                old_embalagem     = str(row.get("embalagem") or "").strip()
                # Excel tem supremacia: só preenche embalagem se estiver vazia
                new_embalagem     = old_embalagem if old_embalagem else new_embalagem_db

                new_cod_citel     = str(citel.get("cod_citel") or "").strip()
                new_marca         = str(citel.get("marca") or "").strip()
                new_grupo         = str(citel.get("grupo") or "").strip()
                new_descricao_db  = (
                    str(citel.get("descricao_db") or "").strip()
                    or str(row.get("descricao") or "").strip()
                )
                cor               = str(row.get("cor") or "").strip()
                new_desc_final    = (new_descricao_db + " \u2014 " + cor) if cor else new_descricao_db

                old_preco_compra  = float(row.get("preco_compra") or 0) if has_extra else 0.0
                old_embalagem_db  = str(row.get("embalagem_db") or "").strip() if has_extra else ""

                # Supremacia da planilha: só atualiza preco se o item NÃO está na planilha
                # (linha=999999 ou linha=0 → adicionado pelo sync CITEL; linha real → veio do Excel)
                _linha = int(row.get("linha") or 0)
                is_citel_item = _linha == 999999 or _linha == 0
                old_preco     = float(row.get("preco") or 0)
                new_preco     = new_preco_compra if is_citel_item else old_preco

                changed = (
                    str(row.get("cod_citel") or "").strip()    != new_cod_citel    or
                    str(row.get("marca") or "").strip()        != new_marca        or
                    str(row.get("grupo") or "").strip()        != new_grupo        or
                    str(row.get("descricao_db") or "").strip() != new_descricao_db or
                    str(row.get("desc_final") or "").strip()   != new_desc_final   or
                    old_embalagem_db                           != new_embalagem_db or
                    old_embalagem                              != new_embalagem    or
                    abs(old_preco_compra - new_preco_compra)   > 0.0001           or
                    (is_citel_item and abs(old_preco - new_preco_compra) > 0.0001)
                )
                if changed:
                    to_update.append({
                        # Colunas NOT NULL — devem ser preservadas no upsert
                        "id":            row["id"],
                        "uf":            row["uf"],
                        "cod_sku":       row["cod_sku"],
                        "linha":         row.get("linha") or 0,
                        "descricao":     row.get("descricao") or "",
                        "cor":           row.get("cor") or "",
                        "preco":         new_preco,
                        # Colunas atualizadas pelo CITEL
                        "cod_citel":     new_cod_citel,
                        "marca":         new_marca,
                        "grupo":         new_grupo,
                        "descricao_db":  new_descricao_db,
                        "desc_final":    new_desc_final,
                        "embalagem_db":  new_embalagem_db,
                        "embalagem":     new_embalagem,
                        "preco_compra":  new_preco_compra,
                    })

            if to_update:
                for j in range(0, len(to_update), BATCH):
                    sb.table("catalogo").upsert(
                        to_update[j:j + BATCH], on_conflict="id"
                    ).execute()
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
    Fingerprint do CADITE + ITEGER que detecta:
    - Novos produtos (COUNT)
    - Produtos removidos (COUNT)
    - Alteracoes em descricao, marca ou grupo (checksum via SUM+LENGTH)
    - Alteracoes em precos de custo (SUM ITEGER.ITE_PRECUS)
    """
    todas_empresas = sorted(set(
        e for emps in _UF_EMPRESAS.values() for e in emps
    ))
    codes_str = ",".join(f"'{e}'" for e in todas_empresas)
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
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT CAST(ROUND(SUM(COALESCE(ITE_PRECUS, 0)), 0) AS UNSIGNED) AS sum_preco
                FROM ITEGER
                WHERE ITE_CODEMP IN ({codes_str})
            """)
            r2 = cur.fetchone()
        sum_preco = int(r2['sum_preco'] or 0) if r2 else 0
        return f"{r['cnt']}|{r['max_fab']}|{r['sum_desc']}|{r['sum_mar']}|{r['sum_gru']}|{sum_preco}"
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
            "cod_citel":       str(r.get("cod_citel") or "").strip(),
            "marca":           str(r.get("marca") or "").strip(),
            "grupo":           str(r.get("grupo") or "").strip(),
            "descricao_db":    str(r.get("descricao_db") or "").strip(),
            "embalagem_db":    str(r.get("embalagem_db") or "").strip(),
            "preco_compra_rn": float(r.get("preco_compra_rn") or 0),
            "preco_compra_ba": float(r.get("preco_compra_ba") or 0),
            "preco_compra_pe": float(r.get("preco_compra_pe") or 0),
            "preco_compra_al": float(r.get("preco_compra_al") or 0),
            "preco_compra_pb": float(r.get("preco_compra_pb") or 0),
        }
        for r in rows
    }

    # 7a. Carrega conjunto de SKUs de origem CITEL (adicionados pelo sync)
    citel_skus = _get_citel_skus(sb)

    # 7b. Adiciona novos produtos do CITEL que ainda não estão no catálogo
    print("  Verificando novos produtos no CITEL para adicionar ao catálogo...")
    n_novos = _add_novos_catalogo(sb, citel_lookup, citel_skus)
    if n_novos:
        print(f"  {n_novos} novos produtos adicionados ao catálogo.")
        _save_citel_skus(sb, citel_skus)

    # 7c. Reenriquece existentes (atualiza preco para itens de origem CITEL)
    n_cat = _reenrich_catalogo(sb, citel_lookup, citel_skus)
    if n_cat:
        print(f"  {n_cat} linhas do catálogo atualizadas com dados CITEL.")

    # 8. Salva fingerprint e timestamp do sync
    fp_novo = _fingerprint_citel()
    _set_config(sb, _FP_KEY, fp_novo)
    _set_config(sb, _TS_KEY, datetime.now(timezone.utc).isoformat())

    n_dedup = len({str(r["cod_fab"]).strip() for r in rows})
    resumo = (f"{n_dedup} produtos sincronizados, {removidos} removidos"
              + (f", {n_novos} novos no catálogo" if n_novos else "")
              + (f", {n_cat} linhas do catálogo atualizadas" if n_cat else ""))
    print(f"  Sync concluido: {resumo}.")
    print(f"[{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] Pronto!")
    return True, resumo



if __name__ == "__main__":
    _force = "--force" in sys.argv or os.environ.get("SYNC_FORCE", "").lower() == "true"
    ok, msg = main(force=_force)
    sys.exit(0 if ok else 1)
