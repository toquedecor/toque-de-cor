"""
importar_catalogo.py — Toque de Cor

Lê o Excel (todas as UFs), enriquece com dados do MySQL CITEL
e salva tudo na tabela `catalogo` do Supabase.

Uso:
  python importar_catalogo.py                          # usa Excel padrão
  python importar_catalogo.py "caminho/para/tabela.xlsx"
"""

import sys
import os
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

STATES = ["RN", "BA", "PE", "AL", "PB"]
BATCH  = 500   # linhas por requisição ao Supabase

# Colunas consideradas para detectar alterações
_COMPARE_COLS = ["descricao", "embalagem", "cor", "preco",
                 "cod_citel", "descricao_db", "marca", "grupo", "desc_final"]


def _get_sb():
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def _ler_uf(path: str, uf: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=f"Tabela {uf}", header=0)
    df.columns = ["UF", "COD_SKU", "DESCRICAO", "EMBALAGEM", "COR", "PRECO"]
    df["COD_SKU"]   = df["COD_SKU"].astype(str).str.strip()
    df["DESCRICAO"] = df["DESCRICAO"].fillna("").astype(str).str.strip()
    df["COR"]       = df["COR"].fillna("").astype(str).str.strip()
    df["EMBALAGEM"] = df["EMBALAGEM"].fillna("").astype(str).str.strip()
    df["PRECO"]     = pd.to_numeric(df["PRECO"], errors="coerce").fillna(0.0)
    df = df[df["EMBALAGEM"] != ""].reset_index(drop=True)
    df["LINHA"] = range(1, len(df) + 1)
    return df


def _enriquecer(df: pd.DataFrame, db: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    if not db.empty:
        merge_cols = ["COD_FAB", "COD_CITEL", "DESCRICAO_DB", "MARCA"]
        if "GRUPO" in db.columns:
            merge_cols.append("GRUPO")
        result = result.merge(
            db[merge_cols], left_on="COD_SKU", right_on="COD_FAB", how="left"
        ).drop(columns=["COD_FAB"])
        result["COD_CITEL"]    = result["COD_CITEL"].fillna("").astype(str)
        result["MARCA"]        = result["MARCA"].fillna("").astype(str)
        result["DESCRICAO_DB"] = result["DESCRICAO_DB"].fillna("").astype(str)
        result["GRUPO"]        = result.get("GRUPO", pd.Series("", index=result.index)).fillna("").astype(str)
    else:
        result["COD_CITEL"] = result["MARCA"] = result["DESCRICAO_DB"] = result["GRUPO"] = ""

    result["DESCRICAO_DB"] = np.where(
        result["DESCRICAO_DB"] != "", result["DESCRICAO_DB"], result["DESCRICAO"]
    )
    result["DESC_FINAL"] = np.where(
        result["COR"] != "",
        result["DESCRICAO_DB"] + " — " + result["COR"],
        result["DESCRICAO_DB"],
    )
    return result


def _buscar_citel(skus: list) -> pd.DataFrame:
    """Consulta o MySQL CITEL para enriquecer os dados."""
    try:
        from db import query_items
        print("  Consultando MySQL CITEL...")
        df = query_items(skus)
        print(f"  {len(df)} SKUs encontrados no CITEL.")
        return df
    except Exception as e:
        print(f"  ⚠️  CITEL indisponível ({e}) — importando sem enriquecimento.")
        return pd.DataFrame(columns=["COD_FAB", "COD_CITEL", "DESCRICAO_DB", "MARCA", "GRUPO"])


def _fetch_existing(sb, uf: str) -> dict:
    """Busca todos os registros atuais desta UF do Supabase."""
    cols = ",".join(["cod_sku"] + _COMPARE_COLS)
    PAGE = 1000
    rows = []
    offset = 0
    while True:
        r = (
            sb.table("catalogo")
            .select(cols)
            .eq("uf", uf)
            .range(offset, offset + PAGE - 1)
            .execute()
        )
        batch = r.data or []
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return {r["cod_sku"]: r for r in rows}


def _fetch_existing_new_client(uf: str) -> tuple:
    """Thread-safe: cria novo cliente Supabase para uso em thread-pool."""
    sb = _get_sb()
    return uf, _fetch_existing(sb, uf)


def _fetch_all_parallel() -> dict:
    """Busca dados existentes de TODAS as UFs em paralelo — 5x mais rápido."""
    result = {uf: {} for uf in STATES}
    with ThreadPoolExecutor(max_workers=len(STATES)) as executor:
        futures = {executor.submit(_fetch_existing_new_client, uf): uf for uf in STATES}
        for future in as_completed(futures):
            uf, existing = future.result()
            result[uf] = existing
            print(f"    [{uf}] {len(existing)} registros existentes")
    return result


def _row_changed(old: dict, new: dict) -> bool:
    """Retorna True se algum campo relevante mudou."""
    for col in _COMPARE_COLS:
        if col == "preco":
            try:
                if abs(float(old.get(col) or 0) - float(new.get(col) or 0)) > 0.0001:
                    return True
            except (TypeError, ValueError):
                pass
        else:
            if str(old.get(col, "") or "").strip() != str(new.get(col, "") or "").strip():
                return True
    return False


def _upload_uf(sb, uf: str, df: pd.DataFrame, existing: dict) -> dict:
    """
    Faz diff inteligente:
      - Upsert nas linhas novas ou alteradas
      - Delete nas linhas que sumiram do Excel
    Recebe `existing` pré-buscado (via _fetch_all_parallel).
    Retorna dict com estatísticas do diff.
    """
    agora = datetime.now(timezone.utc).isoformat()
    existing_skus = set(existing.keys())

    # 2. Monta novos dados
    new_data: dict = {}
    for _, row in df.iterrows():
        sku = str(row["COD_SKU"])
        new_data[sku] = {
            "uf":           uf,
            "linha":        int(row["LINHA"]),
            "cod_sku":      sku,
            "descricao":    str(row["DESCRICAO"]),
            "embalagem":    str(row["EMBALAGEM"]),
            "cor":          str(row["COR"]),
            "preco":        float(row["PRECO"]),
            "cod_citel":    str(row["COD_CITEL"]),
            "descricao_db": str(row["DESCRICAO_DB"]),
            "marca":        str(row["MARCA"]),
            "grupo":        str(row["GRUPO"]),
            "desc_final":   str(row["DESC_FINAL"]),
            "atualizado_em": agora,
        }
    new_skus = set(new_data.keys())

    # 3. Diff
    to_insert  = new_skus - existing_skus
    to_delete  = existing_skus - new_skus
    to_update  = {
        sku for sku in new_skus & existing_skus
        if _row_changed(existing[sku], new_data[sku])
    }
    unchanged = len(new_skus & existing_skus) - len(to_update)

    # 4. Upsert (insert + update)
    rows_to_upsert = [new_data[sku] for sku in to_insert | to_update]
    if rows_to_upsert:
        batches = math.ceil(len(rows_to_upsert) / BATCH)
        for i in range(batches):
            lote = rows_to_upsert[i * BATCH : (i + 1) * BATCH]
            sb.table("catalogo").upsert(lote, on_conflict="uf,cod_sku").execute()
            pct = min(100, round((i + 1) / batches * 100))
            print(f"    [{uf}] Enviando... {pct}%", end="\r")

    # 5. Delete linhas removidas (em lotes de 100)
    if to_delete:
        dl = list(to_delete)
        for i in range(0, len(dl), 100):
            sb.table("catalogo").delete().eq("uf", uf).in_("cod_sku", dl[i:i+100]).execute()

    # 6. Coletar alterações de preço
    precos_alterados = []
    for sku in to_update:
        old_preco = float(existing[sku].get("preco") or 0)
        new_preco = float(new_data[sku]["preco"])
        if abs(old_preco - new_preco) > 0.0001:
            precos_alterados.append({
                "UF": uf,
                "SKU": sku,
                "COD CITEL": new_data[sku]["cod_citel"],
                "Descrição": new_data[sku]["desc_final"],
                "Embalagem": new_data[sku]["embalagem"],
                "Cor": new_data[sku]["cor"],
                "Preço Anterior": round(old_preco, 2),
                "Preço Atual": round(new_preco, 2),
            })

    # 7. Coletar novos vínculos CITEL (itens que agora têm cod_citel e antes não tinham)
    novos_citel = []
    for sku in to_insert | to_update:
        new_citel = new_data[sku]["cod_citel"]
        if new_citel and new_citel.strip():
            old_citel = existing.get(sku, {}).get("cod_citel", "")
            if not old_citel or not str(old_citel).strip():
                novos_citel.append({
                    "UF": uf,
                    "SKU": sku,
                    "COD CITEL": new_citel,
                    "Descrição": new_data[sku]["desc_final"],
                    "Embalagem": new_data[sku]["embalagem"],
                    "Cor": new_data[sku]["cor"],
                    "Preço": round(float(new_data[sku]["preco"]), 2),
                })

    stats = {
        "inseridos":       len(to_insert),
        "atualizados":     len(to_update),
        "removidos":       len(to_delete),
        "sem_alteracao":   unchanged,
        "total":           len(new_skus),
        "precos_alterados": precos_alterados,
        "novos_citel":     novos_citel,
    }
    print(
        f"    [{uf}] ✅ "
        f"+{stats['inseridos']} novos  "
        f"~{stats['atualizados']} atualizados  "
        f"-{stats['removidos']} removidos  "
        f"{stats['sem_alteracao']} sem alteração            "
    )
    return stats


def importar(excel_path: str) -> dict:
    """
    Importa a planilha para o Supabase.
    Retorna dict com resultado por UF.
    """
    if not Path(excel_path).exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {excel_path}")

    print(f"\n{'='*55}")
    print(f"  Importando: {Path(excel_path).name}")
    print(f"{'='*55}\n")

    sb = _get_sb()

    # 1. Lê todas as UFs e coleta todos os SKUs únicos
    print("1. Lendo Excel...")
    dfs: dict[str, pd.DataFrame] = {}
    all_skus: set = set()
    for uf in STATES:
        print(f"   Lendo aba Tabela {uf}...", end=" ")
        dfs[uf] = _ler_uf(excel_path, uf)
        all_skus.update(dfs[uf]["COD_SKU"].tolist())
        print(f"{len(dfs[uf])} linhas")

    # 2. Enriquece com CITEL (uma única consulta para todos os SKUs)
    print(f"\n2. Enriquecendo com dados CITEL ({len(all_skus)} SKUs únicos)...")
    db_df = _buscar_citel(list(all_skus))

    # 3. Busca dados existentes no Supabase em paralelo (5× mais rápido)
    print("\n3. Buscando dados atuais no Supabase (paralelo)...")
    existing_all = _fetch_all_parallel()

    # 4. Calcula diff e envia para Supabase
    print("\n4. Comparando e enviando para Supabase...")
    resultado: dict[str, dict] = {}
    totais = {"inseridos": 0, "atualizados": 0, "removidos": 0, "sem_alteracao": 0, "total": 0}
    for uf in STATES:
        df_rich = _enriquecer(dfs[uf], db_df)
        stats = _upload_uf(sb, uf, df_rich, existing_all[uf])
        resultado[uf] = stats
        for k in totais:
            totais[k] += stats.get(k, 0)

    # 5. Atualiza registro de importação
    from db_supabase import set_config
    _BR_TZ = timezone(timedelta(hours=-3))
    agora_fmt = datetime.now(_BR_TZ).strftime("%d/%m/%Y %H:%M")
    set_config("ultima_importacao", agora_fmt)
    set_config("excel_nome", Path(excel_path).name)
    set_config("catalogo_no_supabase", "true")
    set_config("excel_path", excel_path)

    print(f"\n{'='*55}")
    print(f"  ✅ Importação concluída — {totais['total']} produtos")
    print(f"  +{totais['inseridos']} novos  ~{totais['atualizados']} atualizados  -{totais['removidos']} removidos  {totais['sem_alteracao']} sem alteração")
    print(f"  Data: {agora_fmt}")
    print(f"{'='*55}\n")

    # resultado[uf] = {inseridos, atualizados, removidos, sem_alteracao, total}
    return resultado


if __name__ == "__main__":
    excel = sys.argv[1] if len(sys.argv) > 1 else "Tabela SW Suvinil Geral.xlsx"
    importar(excel)
