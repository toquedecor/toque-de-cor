import streamlit as st
import pandas as pd
import numpy as np
import json
from pathlib import Path
from db import query_items, test_connection, clear_disk_cache

st.set_page_config(
    page_title="Toque de Cor — Simulador de Pedidos",
    page_icon="🎨",
    layout="wide",
)

# ── Constantes ─────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
STATES         = ["RN", "BA", "PE", "AL", "PB"]
SIMILARES_FILE = BASE_DIR / "similares.json"
DEFAULT_EXCEL  = BASE_DIR / "Tabela SW Suvinil Geral.xlsx"

# ── Session state ──────────────────────────────────────────────────────────────
if "similares" not in st.session_state:
    st.session_state.similares = (
        json.loads(SIMILARES_FILE.read_text("utf-8")) if SIMILARES_FILE.exists() else []
    )
if "excel_source" not in st.session_state:
    st.session_state.excel_source = str(DEFAULT_EXCEL) if DEFAULT_EXCEL.exists() else None

# ══════════════════════════════════════════════════════════════════════════════
# FUNÇÕES DE CACHE
#
# st.cache_resource:
#   - Retorna o MESMO objeto (sem serialização/cópia a cada cache hit)
#   - Ideal para DataFrames grandes usados como somente-leitura
#   - vs st.cache_data que pickle-serializa a cada acesso → lento para DFs grandes
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource
def read_uf(path: str, uf: str) -> pd.DataFrame:
    """Lê e normaliza dados de uma UF do Excel. Cache permanente (sem TTL)."""
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
    """Lê todos os estados. Chamado apenas na aba Similares."""
    return {uf: read_uf(path, uf) for uf in STATES}


@st.cache_resource
def get_all_skus(excel_path: str) -> list:
    """
    Extrai SKUs únicos lendo APENAS a coluna B de cada aba.
    Muito mais rápido do que ler todas as colunas para obter a lista de SKUs.
    """
    all_skus: set = set()
    for uf in STATES:
        df = pd.read_excel(excel_path, sheet_name=f"Tabela {uf}",
                           usecols=[1], header=0)
        df.columns = ["SKU"]
        df = df.dropna(subset=["SKU"])
        all_skus.update(df["SKU"].astype(str).str.strip().tolist())
    all_skus.discard("")
    return sorted(all_skus)


@st.cache_resource
def get_db_data(excel_path: str) -> pd.DataFrame:
    """
    Dados do BD para todos os SKUs do Excel.
    Cache em memória (cache_resource) + cache em disco no db.py.
    Primeiro acesso: busca BD (ou disco). Acessos seguintes: retorno imediato.
    """
    skus = get_all_skus(excel_path)
    return query_items(skus)


@st.cache_resource(ttl=300)
def check_db() -> tuple:
    """Verifica conexão com o BD. Reavalia a cada 5 minutos."""
    return test_connection()


# ── Enriquecimento (vectorizado) ───────────────────────────────────────────────
def enrich(raw: pd.DataFrame, db: pd.DataFrame) -> pd.DataFrame:
    """
    Combina dados do Excel com dados do BD usando pd.merge (vectorizado).
    raw é sempre copiado — o objeto em cache nunca é mutado.
    """
    raw = raw.copy()

    if not db.empty:
        raw = raw.merge(
            db[["COD_FAB", "COD_CITEL", "DESCRICAO_DB", "MARCA"]],
            left_on="COD_SKU", right_on="COD_FAB",
            how="left",
        ).drop(columns=["COD_FAB"])
        raw["COD_CITEL"]    = raw["COD_CITEL"].fillna("").astype(str)
        raw["MARCA"]        = raw["MARCA"].fillna("").astype(str)
        raw["DESCRICAO_DB"] = raw["DESCRICAO_DB"].fillna("").astype(str)
    else:
        raw["COD_CITEL"]    = ""
        raw["MARCA"]        = ""
        raw["DESCRICAO_DB"] = ""

    # Fallback para descrição do Excel quando SKU não está no BD
    raw["DESCRICAO_DB"] = np.where(
        raw["DESCRICAO_DB"] != "", raw["DESCRICAO_DB"], raw["DESCRICAO"]
    )
    # DESC_FINAL = "Descrição — Cor"  (vectorizado, sem apply/lambda)
    raw["DESC_FINAL"] = np.where(
        raw["COR"] != "",
        raw["DESCRICAO_DB"] + " — " + raw["COR"],
        raw["DESCRICAO_DB"],
    )
    return raw


# ── Sidebar ────────────────────────────────────────────────────────────────────
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
        get_all_skus.clear()
        check_db.clear()
        st.rerun()

    src = st.session_state.excel_source
    if src:
        st.divider()
        st.caption(f"📄 `{Path(src).name}`")


# ── Cabeçalho ──────────────────────────────────────────────────────────────────
st.markdown("## 🎨 Toque de Cor — Simulador de Pedidos")
st.divider()

tab_sim, tab_comp, tab_imp = st.tabs(
    ["📋 Simulador", "🔄 Similares / Comparativo", "📥 Importar Tabela"]
)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — SIMULADOR
# ══════════════════════════════════════════════════════════════════════════════
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
        # read_uf e get_db_data retornam objetos em cache — sem custo de desserialização
        raw  = read_uf(src, uf)
        db   = get_db_data(src)
        rich = enrich(raw, db)   # cópia leve + merge vectorizado

        factor = 1.0 - pct / 100.0

        display = pd.DataFrame({
            "LINHA":           rich["LINHA"],
            "COD SKU":         rich["COD_SKU"],
            "COD CITEL":       rich["COD_CITEL"],
            "MARCA":           rich["MARCA"],
            "EMBALAGEM":       rich["EMBALAGEM"],
            "DESCRIÇÃO / COR": rich["DESC_FINAL"],
            "PREÇO COMPRA":    rich["PRECO"],
            "DESCONTO %":      pct,
            "PREÇO C/ DESC.":  (rich["PRECO"] * factor).round(2),
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


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — SIMILARES / COMPARATIVO
# ══════════════════════════════════════════════════════════════════════════════
with tab_comp:
    src = st.session_state.excel_source
    if not src or not Path(src).exists():
        st.info("Nenhuma tabela carregada.")
    else:
        all_data = read_all_states(src)   # cache_resource — retorno imediato após 1ª carga
        db       = get_db_data(src)

        # Lista de produtos para o seletor (usa descrição do BD)
        ref_rich = enrich(all_data["RN"], db).drop_duplicates("COD_SKU")
        opcoes   = ref_rich.apply(
            lambda r: (
                f"{r['COD_SKU']} — [{r['MARCA']}] {r['DESC_FINAL']}"
                if r["MARCA"]
                else f"{r['COD_SKU']} — {r['DESC_FINAL']}"
            ),
            axis=1,
        ).tolist()

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
            db_lookup = db.set_index("COD_FAB").to_dict("index") if not db.empty else {}

            for idx, pair in enumerate(st.session_state.similares):
                marca_a = db_lookup.get(pair["sku_a"], {}).get("MARCA", "")
                marca_b = db_lookup.get(pair["sku_b"], {}).get("MARCA", "")
                desc_a  = db_lookup.get(pair["sku_a"], {}).get("DESCRICAO_DB", pair["label_a"])
                desc_b  = db_lookup.get(pair["sku_b"], {}).get("DESCRICAO_DB", pair["label_b"])

                hdr_a  = f"[{marca_a}] {desc_a}" if marca_a else desc_a
                hdr_b  = f"[{marca_b}] {desc_b}" if marca_b else desc_b
                titulo = f"🔄 **{hdr_a}**  ×  **{hdr_b}**"

                with st.expander(titulo, expanded=True):
                    rows = []
                    for uf in STATES:
                        df_uf = all_data[uf]
                        row_a = df_uf[df_uf["COD_SKU"] == pair["sku_a"]]
                        row_b = df_uf[df_uf["COD_SKU"] == pair["sku_b"]]
                        preco_a = float(row_a["PRECO"].values[0]) if len(row_a) else None
                        preco_b = float(row_b["PRECO"].values[0]) if len(row_b) else None
                        emb_a   = row_a["EMBALAGEM"].values[0] if len(row_a) else "—"
                        emb_b   = row_b["EMBALAGEM"].values[0] if len(row_b) else "—"
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


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — IMPORTAR TABELA
# ══════════════════════════════════════════════════════════════════════════════
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
                get_all_skus.clear()
                get_db_data.clear()
                clear_disk_cache()
                st.toast("Tabela atualizada com sucesso!", icon="🎨")
                st.rerun()
        except Exception as e:
            dest.unlink(missing_ok=True)
            st.error(f"Erro ao ler o arquivo: {e}")

    if st.session_state.excel_source and Path(st.session_state.excel_source).exists():
        st.info(f"**Tabela ativa:** `{Path(st.session_state.excel_source).name}`")

# ── Constantes ─────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
STATES         = ["RN", "BA", "PE", "AL", "PB"]
SIMILARES_FILE = BASE_DIR / "similares.json"
DEFAULT_EXCEL  = BASE_DIR / "Tabela SW Suvinil Geral.xlsx"

# ── Session state ──────────────────────────────────────────────────────────────
if "similares" not in st.session_state:
    st.session_state.similares = (
        json.loads(SIMILARES_FILE.read_text("utf-8")) if SIMILARES_FILE.exists() else []
    )
if "excel_source" not in st.session_state:
    st.session_state.excel_source = str(DEFAULT_EXCEL) if DEFAULT_EXCEL.exists() else None

# ── Carregamento de dados Excel ────────────────────────────────────────────────
@st.cache_data
def read_uf(path: str, uf: str) -> pd.DataFrame:
    """Lê e normaliza dados de uma UF específica do Excel."""
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


@st.cache_data
def read_all_states(path: str) -> dict:
    """Lê dados de todas as UFs de uma vez."""
    return {uf: read_uf(path, uf) for uf in STATES}


# ── Carregamento de dados do Banco de Dados ────────────────────────────────────
@st.cache_data(ttl=60)
def check_db() -> tuple:
    """Testa a conexão com o BD (cache 60 s)."""
    return test_connection()


@st.cache_data(ttl=3600)
def get_db_data(excel_path: str) -> pd.DataFrame:
    """
    Carrega enriquecimento do BD para todos os SKUs do Excel ativo.
    Cache de 1 hora. Chave = caminho do Excel (muda ao importar novo arquivo).
    """
    all_states = read_all_states(excel_path)
    all_skus: set = set()
    for df in all_states.values():
        all_skus.update(df["COD_SKU"].tolist())
    return query_items(list(all_skus))


# ── Enriquecimento Excel + BD ──────────────────────────────────────────────────
def enrich(raw: pd.DataFrame, db: pd.DataFrame) -> pd.DataFrame:
    """
    Adiciona ao DataFrame do Excel as colunas vindas do BD:
      COD_CITEL    → CADITE.ITE_CODITE
      DESCRICAO_DB → CADITE.ITE_DESITE  (fallback: DESCRICAO do Excel)
      MARCA        → CADMAR.MAR_DESMAR
    e calcula DESC_FINAL = DESCRICAO_DB — COR.
    """
    raw = raw.copy()
    if not db.empty:
        lookup = db.set_index("COD_FAB").to_dict("index")
    else:
        lookup = {}

    raw["COD_CITEL"] = raw["COD_SKU"].map(
        lambda s: str(lookup.get(s, {}).get("COD_CITEL") or "")
    )
    raw["MARCA"] = raw["COD_SKU"].map(
        lambda s: str(lookup.get(s, {}).get("MARCA") or "")
    )
    raw["DESCRICAO_DB"] = raw["COD_SKU"].map(
        lambda s: str(lookup.get(s, {}).get("DESCRICAO_DB") or "")
    )
    # Fallback para descrição do Excel quando o SKU não está no BD
    raw["DESCRICAO_DB"] = raw.apply(
        lambda r: r["DESCRICAO_DB"] if r["DESCRICAO_DB"] else r["DESCRICAO"], axis=1
    )
    raw["DESC_FINAL"] = raw.apply(
        lambda r: f"{r['DESCRICAO_DB']} — {r['COR']}" if r["COR"] else r["DESCRICAO_DB"],
        axis=1,
    )
    return raw


# ── Cabeçalho + status do BD ───────────────────────────────────────────────────
col_titulo, col_status = st.columns([6, 1])
with col_titulo:
    st.markdown("## 🎨 Toque de Cor — Simulador de Pedidos")
with col_status:
    db_ok, db_msg = check_db()
    if db_ok:
        st.success("🟢 BD Online")
    else:
        st.error("🔴 BD Offline")
        st.caption(db_msg)

st.divider()

tab_sim, tab_comp, tab_imp = st.tabs(
    ["📋 Simulador", "🔄 Similares / Comparativo", "📥 Importar Tabela"]
)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — SIMULADOR
# ══════════════════════════════════════════════════════════════════════════════
with tab_sim:
    c1, c2, c3 = st.columns([1, 1, 4])
    with c1:
        uf = st.selectbox("**UF**", STATES)
    with c2:
        pct = st.number_input("**Desconto Global (%)**", 0.0, 100.0, 0.0, 0.5, format="%.2f")
    with c3:
        st.write("")
        if st.button("🔄 Recarregar dados do BD", help="Força nova consulta ao banco de dados"):
            get_db_data.clear()
            check_db.clear()
            st.rerun()

    src = st.session_state.excel_source
    if not src or not Path(src).exists():
        st.info("Nenhuma tabela carregada. Vá para **Importar Tabela** para carregar o arquivo Excel.")
    else:
        with st.spinner("Carregando dados..."):
            raw = read_uf(src, uf)
            db  = get_db_data(src)
        rich = enrich(raw, db)

        display = pd.DataFrame({
            "LINHA":           rich["LINHA"],
            "COD SKU":         rich["COD_SKU"],
            "COD CITEL":       rich["COD_CITEL"],
            "MARCA":           rich["MARCA"],
            "EMBALAGEM":       rich["EMBALAGEM"],
            "DESCRIÇÃO / COR": rich["DESC_FINAL"],
            "PREÇO COMPRA":    rich["PRECO"],
            "DESCONTO %":      pct,
            "PREÇO C/ DESC.":  (rich["PRECO"] * (1 - pct / 100)).round(2),
        })

        if not db_ok:
            st.warning(
                "⚠️ BD offline — COD CITEL, Marca e Descrição do sistema CITEL não estão disponíveis. "
                "Exibindo descrições do arquivo Excel."
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
                                       "Preço Compra", format="R$ %.2f", width=140
                                   ),
                "DESCONTO %":      st.column_config.NumberColumn(format="%.2f%%", width=90),
                "PREÇO C/ DESC.":  st.column_config.NumberColumn(
                                       "Preço c/ Desconto", format="R$ %.2f", width=150
                                   ),
            },
            hide_index=True,
            use_container_width=True,
        )

        st.caption(
            f"**{len(display)}** itens — UF: **{uf}** | "
            f"Fonte BD: {'✅ CITEL' if db_ok else '⚠️ offline (usando Excel)'}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — SIMILARES / COMPARATIVO
# ══════════════════════════════════════════════════════════════════════════════
with tab_comp:
    src = st.session_state.excel_source
    if not src or not Path(src).exists():
        st.info("Nenhuma tabela carregada.")
    else:
        all_data = read_all_states(src)
        db       = get_db_data(src)

        # Lista de produtos usando descrição do BD (referência: UF = RN)
        ref_rich = enrich(all_data["RN"], db).drop_duplicates("COD_SKU")
        opcoes   = ref_rich.apply(
            lambda r: (
                f"{r['COD_SKU']} — [{r['MARCA']}] {r['DESC_FINAL']}"
                if r["MARCA"]
                else f"{r['COD_SKU']} — {r['DESC_FINAL']}"
            ),
            axis=1,
        ).tolist()

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
            # Monta lookup de DB para obter MARCA dos itens
            db_lookup = db.set_index("COD_FAB").to_dict("index") if not db.empty else {}

            for idx, pair in enumerate(st.session_state.similares):
                marca_a = db_lookup.get(pair["sku_a"], {}).get("MARCA", "")
                marca_b = db_lookup.get(pair["sku_b"], {}).get("MARCA", "")
                desc_a  = db_lookup.get(pair["sku_a"], {}).get("DESCRICAO_DB", pair["label_a"])
                desc_b  = db_lookup.get(pair["sku_b"], {}).get("DESCRICAO_DB", pair["label_b"])

                hdr_a = f"[{marca_a}] {desc_a}" if marca_a else desc_a
                hdr_b = f"[{marca_b}] {desc_b}" if marca_b else desc_b
                titulo = f"🔄 **{hdr_a}**  ×  **{hdr_b}**"

                with st.expander(titulo, expanded=True):
                    rows = []
                    for uf in STATES:
                        df_uf = all_data[uf]
                        row_a = df_uf[df_uf["COD_SKU"] == pair["sku_a"]]
                        row_b = df_uf[df_uf["COD_SKU"] == pair["sku_b"]]
                        preco_a = float(row_a["PRECO"].values[0]) if len(row_a) else None
                        preco_b = float(row_b["PRECO"].values[0]) if len(row_b) else None
                        emb_a   = row_a["EMBALAGEM"].values[0] if len(row_a) else "—"
                        emb_b   = row_b["EMBALAGEM"].values[0] if len(row_b) else "—"
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

                    cmp_df = pd.DataFrame(rows)
                    st.dataframe(
                        cmp_df,
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


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — IMPORTAR TABELA
# ══════════════════════════════════════════════════════════════════════════════
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
                # Limpa todos os caches dependentes do Excel e do BD
                read_uf.clear()
                read_all_states.clear()
                get_db_data.clear()
                st.toast("Tabela atualizada com sucesso!", icon="🎨")
                st.rerun()
        except Exception as e:
            dest.unlink(missing_ok=True)
            st.error(f"Erro ao ler o arquivo: {e}")

    if st.session_state.excel_source and Path(st.session_state.excel_source).exists():
        st.info(f"**Tabela ativa:** `{Path(st.session_state.excel_source).name}`")
