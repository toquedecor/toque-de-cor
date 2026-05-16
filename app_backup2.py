import streamlit as st
import pandas as pd
import numpy as np
import json
from pathlib import Path
from db import query_items, test_connection, clear_disk_cache, get_cached_data

st.set_page_config(
    page_title="Toque de Cor — Simulador de Pedidos",
    page_icon="🎨",
    layout="wide",
)

# ── Constantes ──────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
STATES         = ["RN", "BA", "PE", "AL", "PB"]
SIMILARES_FILE = BASE_DIR / "similares.json"
DEFAULT_EXCEL  = BASE_DIR / "Tabela SW Suvinil Geral.xlsx"

# ── Session state ────────────────────────────────────────────────────────────────
if "similares" not in st.session_state:
    st.session_state.similares = (
        json.loads(SIMILARES_FILE.read_text("utf-8")) if SIMILARES_FILE.exists() else []
    )
if "excel_source" not in st.session_state:
    st.session_state.excel_source = str(DEFAULT_EXCEL) if DEFAULT_EXCEL.exists() else None

# ══════════════════════════════════════════════════════════════════════════════════
# FUNÇÕES CACHEADAS
#
# @st.cache_resource:
#   - Retorna o MESMO objeto em memória (sem cópia/serialização a cada render)
#   - Cada função computada 1x por argumento único; renderizações seguintes: ~0 ms
# ══════════════════════════════════════════════════════════════════════════════════

@st.cache_resource
def read_uf(path: str, uf: str) -> pd.DataFrame:
    """Lê e normaliza 1 aba do Excel. Cache permanente."""
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


@st.cache_resource
def read_all_states(path: str) -> dict:
    """Lê todas as 5 UFs. Reutiliza cache de read_uf — 0 leituras extras."""
    return {uf: read_uf(path, uf) for uf in STATES}


@st.cache_resource
def get_db_data(excel_path: str) -> pd.DataFrame:
    """
    Dados de enriquecimento do BD.
    Fast path: se cache em disco válido, retorna sem ler o Excel.
    Slow path: lê Excel + consulta BD (uma única vez; resultado salvo em disco).
    """
    cached = get_cached_data()
    if cached is not None:
        return cached
    # Reutiliza read_all_states (já cacheado) para extrair SKUs
    all_states = read_all_states(excel_path)
    all_skus: set = set()
    for df in all_states.values():
        all_skus.update(df["COD_SKU"].tolist())
    return query_items(list(all_skus))


@st.cache_resource
def get_enriched(path: str, uf: str) -> pd.DataFrame:
    """
    DataFrame enriquecido por UF (Excel + BD).
    Computado UMA vez por UF e cacheado. Cada render: retorno instantâneo.
    NÃO inclui desconto (calculado inline a cada render, pois depende de input).
    """
    raw = read_uf(path, uf)
    db  = get_db_data(path)

    result = raw.copy()
    if not db.empty:
        result = result.merge(
            db[["COD_FAB", "COD_CITEL", "DESCRICAO_DB", "MARCA"]],
            left_on="COD_SKU", right_on="COD_FAB",
            how="left",
        ).drop(columns=["COD_FAB"])
        result["COD_CITEL"]    = result["COD_CITEL"].fillna("").astype(str)
        result["MARCA"]        = result["MARCA"].fillna("").astype(str)
        result["DESCRICAO_DB"] = result["DESCRICAO_DB"].fillna("").astype(str)
    else:
        result["COD_CITEL"] = result["MARCA"] = result["DESCRICAO_DB"] = ""

    # Fallback: usa descrição do Excel quando SKU não está no BD
    result["DESCRICAO_DB"] = np.where(
        result["DESCRICAO_DB"] != "", result["DESCRICAO_DB"], result["DESCRICAO"]
    )
    # DESC_FINAL = "Descrição — Cor" (vectorizado)
    result["DESC_FINAL"] = np.where(
        result["COR"] != "",
        result["DESCRICAO_DB"] + " — " + result["COR"],
        result["DESCRICAO_DB"],
    )
    return result


@st.cache_resource
def get_product_opcoes(path: str) -> list:
    """
    Lista de strings para o seletor de similares.
    Computada 1x (vectorizada, sem apply/lambda). Render seguinte: ~0 ms.
    """
    ref       = get_enriched(path, "RN").drop_duplicates(subset="COD_SKU")
    has_marca = ref["MARCA"].ne("")
    with_m    = ref["COD_SKU"] + " — [" + ref["MARCA"] + "] " + ref["DESC_FINAL"]
    no_m      = ref["COD_SKU"] + " — " + ref["DESC_FINAL"]
    return np.where(has_marca, with_m, no_m).tolist()


@st.cache_resource
def get_states_indexed(path: str) -> dict:
    """
    DataFrames indexados por COD_SKU para busca O(1) de preços.
    Substitui df[df['COD_SKU'] == sku] (O(n)) por idx.loc[sku] (O(1)).
    """
    all_states = read_all_states(path)
    return {
        uf: df.drop_duplicates(subset="COD_SKU", keep="first")
               .set_index("COD_SKU")[["PRECO", "EMBALAGEM"]]
        for uf, df in all_states.items()
    }


@st.cache_resource(ttl=300)
def check_db() -> tuple:
    """Verifica conexão com o BD. Reavalida a cada 5 minutos."""
    return test_connection()


# ── Sidebar ──────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Status")

    db_ok, db_msg = check_db()
    if db_ok:
        st.success("🟢 BD Online")
    else:
        st.error("🔴 BD Offline")
        st.caption(db_msg[:120])

    if st.button("🔄 Recarregar BD", use_container_width=True,
                 help="Força nova consulta ao banco de dados"):
        clear_disk_cache()
        get_db_data.clear()
        get_enriched.clear()
        get_product_opcoes.clear()
        get_states_indexed.clear()
        check_db.clear()
        st.rerun()

    src = st.session_state.excel_source
    if src:
        st.divider()
        st.caption(f"📄 `{Path(src).name}`")


# ── Cabeçalho ────────────────────────────────────────────────────────────────────
st.markdown("## 🎨 Toque de Cor — Simulador de Pedidos")
st.divider()

tab_sim, tab_comp, tab_imp = st.tabs(
    ["📋 Simulador", "🔄 Similares / Comparativo", "📥 Importar Tabela"]
)


# ══════════════════════════════════════════════════════════════════════════════════
# TAB 1 — SIMULADOR
# ══════════════════════════════════════════════════════════════════════════════════
with tab_sim:
    c1, c2 = st.columns([1, 1])
    with c1:
        uf = st.selectbox("**UF**", STATES)
    with c2:
        pct = st.number_input("**Desconto Global (%)**", 0.0, 100.0, 0.0, 0.5, format="%.2f")

    src = st.session_state.excel_source
    if not src or not Path(src).exists():
        st.info("Nenhuma tabela carregada. Vá para **Importar Tabela** para carregar o arquivo Excel.")
    else:
        # Retorno instantâneo após primeiro carregamento (cache_resource)
        rich   = get_enriched(src, uf)
        factor = 1.0 - pct / 100.0

        # Usa .values (numpy arrays) — mais rápido que pandas Series para construir DF
        display = pd.DataFrame({
            "LINHA":           rich["LINHA"].values,
            "COD SKU":         rich["COD_SKU"].values,
            "COD CITEL":       rich["COD_CITEL"].values,
            "MARCA":           rich["MARCA"].values,
            "EMBALAGEM":       rich["EMBALAGEM"].values,
            "DESCRIÇÃO / COR": rich["DESC_FINAL"].values,
            "PREÇO COMPRA":    rich["PRECO"].values,
            "DESCONTO %":      pct,
            "PREÇO C/ DESC.":  np.round(rich["PRECO"].values * factor, 2),
        })

        if not db_ok:
            st.warning(
                "⚠️ BD offline — COD CITEL, Marca e Descrição do CITEL indisponíveis. "
                "Exibindo descrições do Excel."
            )

        st.dataframe(
            display,
            column_config={
                "LINHA":           st.column_config.NumberColumn(width=60),
                "COD SKU":         st.column_config.TextColumn(width=110),
                "COD CITEL":       st.column_config.TextColumn("COD CITEL", width=110),
                "MARCA":           st.column_config.TextColumn(width=160),
                "EMBALAGEM":       st.column_config.TextColumn(width=170),
                "DESCRIÇÃO / COR": st.column_config.TextColumn(),
                "PREÇO COMPRA":    st.column_config.NumberColumn(
                                       "Preço Compra", format="R$ %.2f", width=140),
                "DESCONTO %":      st.column_config.NumberColumn(format="%.2f%%", width=90),
                "PREÇO C/ DESC.":  st.column_config.NumberColumn(
                                       "Preço c/ Desconto", format="R$ %.2f", width=150),
            },
            hide_index=True,
            use_container_width=True,
        )

        st.caption(
            f"**{len(display)}** itens — UF: **{uf}** | "
            f"Fonte BD: {'✅ CITEL' if db_ok else '⚠️ offline (usando Excel)'}"
        )


# ══════════════════════════════════════════════════════════════════════════════════
# TAB 2 — SIMILARES / COMPARATIVO
# ══════════════════════════════════════════════════════════════════════════════════
with tab_comp:
    src = st.session_state.excel_source
    if not src or not Path(src).exists():
        st.info("Nenhuma tabela carregada.")
    else:
        # Tudo cacheado: retorno instantâneo em todos os renders seguintes ao primeiro
        opcoes  = get_product_opcoes(src)
        indexed = get_states_indexed(src)

        # Lookup rápido de MARCA/DESCRICAO para os títulos dos expanders
        db      = get_db_data(src)
        db_lookup = (
            db.set_index("COD_FAB")[["MARCA", "DESCRICAO_DB"]].to_dict("index")
            if not db.empty else {}
        )

        st.markdown("### Adicionar Par de Similares")
        cc1, cc2, cc3 = st.columns([4, 4, 1])
        with cc1:
            item_a = st.selectbox("Item A (produto base)", opcoes, key="add_a")
        with cc2:
            item_b = st.selectbox("Item B (produto similar)", opcoes, key="add_b")
        with cc3:
            st.write("")
            st.write("")
            if st.button("➕ Adicionar", use_container_width=True):
                sku_a = item_a.split(" — ")[0]
                sku_b = item_b.split(" — ")[0]
                if sku_a == sku_b:
                    st.warning("Selecione dois produtos diferentes.")
                else:
                    pair = {
                        "sku_a": sku_a, "label_a": item_a,
                        "sku_b": sku_b, "label_b": item_b,
                    }
                    existing = [(p["sku_a"], p["sku_b"]) for p in st.session_state.similares]
                    if (sku_a, sku_b) not in existing and (sku_b, sku_a) not in existing:
                        st.session_state.similares.append(pair)
                        SIMILARES_FILE.write_text(
                            json.dumps(st.session_state.similares, ensure_ascii=False, indent=2),
                            "utf-8",
                        )
                        st.rerun()
                    else:
                        st.warning("Par já cadastrado.")

        st.divider()

        if not st.session_state.similares:
            st.info("Nenhum par de similares cadastrado ainda.")
        else:
            for idx, pair in enumerate(st.session_state.similares):
                info_a = db_lookup.get(pair["sku_a"], {})
                info_b = db_lookup.get(pair["sku_b"], {})
                marca_a = info_a.get("MARCA", "")
                marca_b = info_b.get("MARCA", "")
                desc_a  = info_a.get("DESCRICAO_DB", pair["label_a"])
                desc_b  = info_b.get("DESCRICAO_DB", pair["label_b"])

                hdr_a  = f"[{marca_a}] {desc_a}" if marca_a else desc_a
                hdr_b  = f"[{marca_b}] {desc_b}" if marca_b else desc_b
                titulo = f"🔄 **{hdr_a}**  ×  **{hdr_b}**"

                with st.expander(titulo, expanded=True):
                    rows = []
                    for uf in STATES:
                        idx_uf  = indexed[uf]
                        sku_a   = pair["sku_a"]
                        sku_b   = pair["sku_b"]
                        # O(1) — índice pandas; antes era O(n) com boolean filter
                        preco_a = float(idx_uf.at[sku_a, "PRECO"])  if sku_a in idx_uf.index else None
                        preco_b = float(idx_uf.at[sku_b, "PRECO"])  if sku_b in idx_uf.index else None
                        emb_a   = idx_uf.at[sku_a, "EMBALAGEM"]     if sku_a in idx_uf.index else "—"
                        emb_b   = idx_uf.at[sku_b, "EMBALAGEM"]     if sku_b in idx_uf.index else "—"
                        diff    = round(preco_a - preco_b, 2) if (
                            preco_a is not None and preco_b is not None
                        ) else None
                        rows.append({
                            "UF":        uf,
                            "Emb. A":    emb_a,
                            "Preço A":   preco_a,
                            "Emb. B":    emb_b,
                            "Preço B":   preco_b,
                            "Diferença": diff,
                        })

                    st.dataframe(
                        pd.DataFrame(rows),
                        column_config={
                            "Preço A":   st.column_config.NumberColumn(format="R$ %.2f"),
                            "Preço B":   st.column_config.NumberColumn(format="R$ %.2f"),
                            "Diferença": st.column_config.NumberColumn(format="R$ %.2f"),
                        },
                        hide_index=True,
                        use_container_width=True,
                    )

                    if st.button("🗑️ Remover", key=f"del_{idx}"):
                        st.session_state.similares.pop(idx)
                        SIMILARES_FILE.write_text(
                            json.dumps(st.session_state.similares, ensure_ascii=False, indent=2),
                            "utf-8",
                        )
                        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════════
# TAB 3 — IMPORTAR TABELA
# ══════════════════════════════════════════════════════════════════════════════════
with tab_imp:
    st.markdown("### Importar Nova Tabela de Preços")
    st.markdown(
        "Selecione um arquivo `.xlsx` com as abas "
        "**Tabela RN, Tabela BA, Tabela PE, Tabela AL, Tabela PB** "
        "seguindo o padrão:  \n"
        "`Coluna A: UF | B: SKU | C: Descrição | D: Embalagem | E: Cor | F: Preço c/ ICMS`"
    )

    uploaded = st.file_uploader("Selecionar arquivo Excel (.xlsx)", type=["xlsx"])
    if uploaded:
        dest = BASE_DIR / uploaded.name
        dest.write_bytes(uploaded.getvalue())
        try:
            test = pd.read_excel(str(dest), sheet_name="Tabela RN", header=0)
            st.success(
                f"✅ Arquivo **{uploaded.name}** importado com sucesso! "
                f"({len(test)} linhas na aba Tabela RN)"
            )
            if st.button("📥 Usar este arquivo como tabela ativa"):
                st.session_state.excel_source = str(dest)
                # Limpa todos os caches dependentes do Excel
                read_uf.clear()
                read_all_states.clear()
                get_db_data.clear()
                get_enriched.clear()
                get_product_opcoes.clear()
                get_states_indexed.clear()
                clear_disk_cache()
                st.toast("Tabela atualizada com sucesso!", icon="🎨")
                st.rerun()
        except Exception as e:
            dest.unlink(missing_ok=True)
            st.error(f"Erro ao ler o arquivo: {e}")

    if st.session_state.excel_source and Path(st.session_state.excel_source).exists():
        st.info(f"**Tabela ativa:** `{Path(st.session_state.excel_source).name}`")
