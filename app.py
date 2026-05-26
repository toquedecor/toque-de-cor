import io
import os
import sys
import streamlit as st
import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime
from db import query_items, test_connection, clear_disk_cache, get_cached_data

st.set_page_config(
    page_title="Toque de Cor — Simulador de Pedidos",
    page_icon="🎨",
    layout="wide",
)

# ── Constantes ──────────────────────────────────────────────────────────────────
# BASE_DIR: localização dos arquivos do código/bundle (leitura)
BASE_DIR = Path(__file__).parent
# DATA_DIR: localização dos dados do usuário (gravável)
# Quando rodando como .app (PyInstaller), usa ~/Documents/ToqueDeCor/
_env_data = os.environ.get("TOQUEDECOR_DATA_DIR", "")
DATA_DIR       = Path(_env_data) if _env_data else BASE_DIR

STATES         = ["RN", "BA", "PE", "AL", "PB"]
SIMILARES_FILE = DATA_DIR / "similares.json"
PEDIDOS_FILE   = DATA_DIR / "pedidos.json"
DEFAULT_EXCEL  = BASE_DIR / "Tabela SW Suvinil Geral.xlsx"

# ── Session state ────────────────────────────────────────────────────────────────
if "similares" not in st.session_state:
    st.session_state.similares = (
        json.loads(SIMILARES_FILE.read_text("utf-8")) if SIMILARES_FILE.exists() else []
    )
if "excel_source" not in st.session_state:
    st.session_state.excel_source = str(DEFAULT_EXCEL) if DEFAULT_EXCEL.exists() else None
if "pedidos" not in st.session_state:
    st.session_state.pedidos = (
        json.loads(PEDIDOS_FILE.read_text("utf-8")) if PEDIDOS_FILE.exists() else []
    )
if "preview_pedido" not in st.session_state:
    st.session_state.preview_pedido = None

# ══════════════════════════════════════════════════════════════════════════════════
# FUNÇÕES CACHEADAS
# @st.cache_resource: retorna o MESMO objeto em memória — sem cópia/serialização.
# Cada função é computada UMA vez por argumento único; renders seguintes: ~0 ms.
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
    """Lê todas as 5 UFs reutilizando o cache de read_uf."""
    return {uf: read_uf(path, uf) for uf in STATES}


@st.cache_resource
def get_db_data(excel_path: str) -> pd.DataFrame:
    """
    Dados de enriquecimento do BD.
    Fast path: cache em disco válido COM coluna GRUPO → retorna sem ler Excel.
    Se cache for antigo (sem GRUPO): limpa e refaz a query com o novo SQL.
    Slow path: lê Excel + consulta BD → salva em disco para próximas sessões.
    """
    cached = get_cached_data()
    if cached is not None and "GRUPO" in cached.columns:
        return cached
    if cached is not None:
        # Cache antigo sem GRUPO — descarta para re-consultar com novo SQL
        clear_disk_cache()

    all_states = read_all_states(excel_path)
    all_skus: set = set()
    for df in all_states.values():
        all_skus.update(df["COD_SKU"].tolist())
    return query_items(list(all_skus))


@st.cache_resource
def get_enriched(path: str, uf: str) -> pd.DataFrame:
    """
    DataFrame enriquecido (Excel + BD) por UF.
    Inclui: COD_CITEL, MARCA, GRUPO, DESC_FINAL.
    Computado UMA vez por UF e cacheado permanentemente.
    """
    raw = read_uf(path, uf)
    db  = get_db_data(path)

    result = raw.copy()
    if not db.empty:
        merge_cols = ["COD_FAB", "COD_CITEL", "DESCRICAO_DB", "MARCA"]
        if "GRUPO" in db.columns:
            merge_cols.append("GRUPO")

        result = result.merge(
            db[merge_cols],
            left_on="COD_SKU", right_on="COD_FAB",
            how="left",
        ).drop(columns=["COD_FAB"])

        result["COD_CITEL"]    = result["COD_CITEL"].fillna("").astype(str)
        result["MARCA"]        = result["MARCA"].fillna("").astype(str)
        result["DESCRICAO_DB"] = result["DESCRICAO_DB"].fillna("").astype(str)
        if "GRUPO" not in result.columns:
            result["GRUPO"] = ""
        else:
            result["GRUPO"] = result["GRUPO"].fillna("").astype(str)
    else:
        result["COD_CITEL"] = result["MARCA"] = result["DESCRICAO_DB"] = result["GRUPO"] = ""

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
    """Lista de strings para o seletor de similares.
    Cobre todos os estados; usa CADITE.ITE_DESITE (DESCRICAO_DB) do BD. Vectorizado."""
    all_states = read_all_states(path)
    # Reúne todos os SKUs de todos os estados, sem duplicatas
    skus_df = (
        pd.concat(
            [df[["COD_SKU", "DESCRICAO"]].drop_duplicates("COD_SKU")
             for df in all_states.values()]
        )
        .drop_duplicates("COD_SKU")
        .reset_index(drop=True)
    )

    db = get_db_data(path)
    if not db.empty:
        merge_cols = ["COD_FAB", "DESCRICAO_DB", "MARCA"]
        skus_df = skus_df.merge(
            db[merge_cols], left_on="COD_SKU", right_on="COD_FAB", how="left"
        ).drop(columns=["COD_FAB"])
        skus_df["DESCRICAO_DB"] = skus_df["DESCRICAO_DB"].fillna("").astype(str)
        skus_df["MARCA"]        = skus_df["MARCA"].fillna("").astype(str)
        # Usa descrição do BD (ITE_DESITE); fallback para Excel se ausente
        skus_df["DESC_SHOW"] = np.where(
            skus_df["DESCRICAO_DB"] != "", skus_df["DESCRICAO_DB"], skus_df["DESCRICAO"]
        )
    else:
        skus_df["MARCA"]     = ""
        skus_df["DESC_SHOW"] = skus_df["DESCRICAO"]

    has_marca = skus_df["MARCA"].ne("")
    with_m    = skus_df["COD_SKU"] + " — [" + skus_df["MARCA"] + "] " + skus_df["DESC_SHOW"]
    no_m      = skus_df["COD_SKU"] + " — " + skus_df["DESC_SHOW"]
    return np.where(has_marca, with_m, no_m).tolist()


@st.cache_resource
def get_states_indexed(path: str) -> dict:
    """DataFrames indexados por COD_SKU para busca O(1) de preços na aba Similares."""
    all_states = read_all_states(path)
    return {
        uf: df.drop_duplicates(subset="COD_SKU", keep="first")
               .set_index("COD_SKU")[["PRECO", "EMBALAGEM"]]
        for uf, df in all_states.items()
    }


@st.cache_resource
def get_db_lookup(path: str) -> dict:
    """
    Dict {COD_FAB: {MARCA, DESCRICAO_DB, GRUPO}} para lookups nos títulos de Similares.
    Cached — não recalculado a cada render.
    """
    db = get_db_data(path)
    if db.empty:
        return {}
    cols = ["MARCA", "DESCRICAO_DB"]
    if "GRUPO" in db.columns:
        cols.append("GRUPO")
    return db.set_index("COD_FAB")[cols].to_dict("index")


@st.cache_resource(ttl=300)
def check_db() -> tuple:
    """Verifica conexão com o BD. Reavalida a cada 5 minutos."""
    return test_connection()


def _clear_all_caches():
    """Limpa todos os caches de dados (chamado em Recarregar BD e Importar)."""
    clear_disk_cache()
    get_db_data.clear()
    get_enriched.clear()
    get_product_opcoes.clear()
    get_states_indexed.clear()
    get_db_lookup.clear()
    read_uf.clear()
    read_all_states.clear()


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
        _clear_all_caches()
        check_db.clear()
        st.session_state.pop("caches_warmed", None)
        st.rerun()

    src = st.session_state.excel_source
    if src:
        st.divider()
        st.caption(f"📄 `{Path(src).name}`")


# ── Cabeçalho ────────────────────────────────────────────────────────────────────
st.markdown("## 🎨 Toque de Cor — Simulador de Pedidos")
st.divider()

# ── Pré-aquecimento de caches ────────────────────────────────────────────────────
# _warmup_ph ocupa SEMPRE a mesma posição no component tree (mesmo vazio).
# Sem isso, st.tabs aparece em posições diferentes entre o 1º carregamento
# (quando o spinner existe) e os reruns seguintes, causando reset da aba ativa.
_warmup_ph = st.empty()
src = st.session_state.excel_source
if src and Path(src).exists():
    if "caches_warmed" not in st.session_state:
        with _warmup_ph:
            with st.spinner("Carregando dados..."):
                read_all_states(src)
                get_db_data(src)
        st.session_state["caches_warmed"] = True

tab_sim, tab_ped, tab_comp, tab_imp = st.tabs(
    ["📋 Simulador", "📦 Pedidos", "🔄 Similares / Comparativo", "📥 Importar Tabela"]
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
        rich = get_enriched(src, uf)

        # ── Busca rápida (palavras em qualquer ordem) ──────────────────────────────
        busca_sim = st.text_input(
            "🔍 Buscar produto",
            placeholder="Digite palavras em qualquer ordem — ex: latex branco 18l",
            key="busca_sim",
        )

        # ── Filtros ──────────────────────────────────────────────────────────────
        unique_grupos = sorted(g for g in rich["GRUPO"].unique() if g)
        unique_marcas = sorted(m for m in rich["MARCA"].unique() if m)
        unique_embs   = sorted(e for e in rich["EMBALAGEM"].unique() if e)

        with st.expander("🔍 Filtros", expanded=False):
            f1, f2, f3 = st.columns(3)
            with f1:
                sel_grupos = st.multiselect(
                    "Linha / Grupo", unique_grupos,
                    placeholder="Todos os grupos",
                    disabled=not unique_grupos,
                )
            with f2:
                sel_marcas = st.multiselect(
                    "Marca", unique_marcas,
                    placeholder="Todas as marcas",
                    disabled=not unique_marcas,
                )
            with f3:
                sel_embs = st.multiselect(
                    "Embalagem", unique_embs,
                    placeholder="Todas as embalagens",
                    disabled=not unique_embs,
                )

        # Aviso se GRUPO ainda não disponível (cache antigo sendo atualizado)
        if db_ok and not unique_grupos:
            st.info("💡 Dados de Grupo/Linha ainda não carregados. Aguarde ou clique **Recarregar BD**.")

        # Aplica filtros sem mutar o DF em cache (rich é somente-leitura)
        mask = pd.Series(True, index=rich.index)
        if sel_grupos:
            mask &= rich["GRUPO"].isin(sel_grupos)
        if sel_marcas:
            mask &= rich["MARCA"].isin(sel_marcas)
        if sel_embs:
            mask &= rich["EMBALAGEM"].isin(sel_embs)
        filtered = rich[mask]

        # Aplica busca textual (palavras em qualquer ordem, case-insensitive)
        if busca_sim.strip():
            words_sim = busca_sim.upper().split()
            combined_sim = (
                filtered["COD_SKU"].astype(str) + " " +
                filtered["COD_CITEL"].astype(str) + " " +
                filtered["DESC_FINAL"].astype(str) + " " +
                filtered["MARCA"].astype(str) + " " +
                filtered["GRUPO"].astype(str) + " " +
                filtered["EMBALAGEM"].astype(str)
            ).str.upper()
            for w in words_sim:
                filtered = filtered[combined_sim.loc[filtered.index].str.contains(w, na=False)]

        factor = 1.0 - pct / 100.0

        # ── Estado do pedido: QTD por SKU (persiste enquanto filtros mudam) ──────
        qtd_key    = f"pedido_qtd_{uf}"
        zerar_key  = f"zerar_cnt_{uf}"
        if qtd_key not in st.session_state:
            st.session_state[qtd_key] = {}
        if zerar_key not in st.session_state:
            st.session_state[zerar_key] = 0
        qtd_map   = st.session_state[qtd_key]
        sku_array = filtered["COD_SKU"].values
        qtd_col   = [str(int(qtd_map.get(sku, 0))) for sku in sku_array]

        # Usa .values (numpy arrays) — mais rápido para construir o display DataFrame
        display = pd.DataFrame({
            "QTD":             qtd_col,
            "LINHA":           filtered["LINHA"].values,
            "COD SKU":         filtered["COD_SKU"].values,
            "COD CITEL":       filtered["COD_CITEL"].values,
            "LINHA / GRUPO":   filtered["GRUPO"].values,
            "MARCA":           filtered["MARCA"].values,
            "EMBALAGEM":       filtered["EMBALAGEM"].values,
            "DESCRIÇÃO / COR": filtered["DESC_FINAL"].values,
            "PREÇO COMPRA":    filtered["PRECO"].values,
            "DESCONTO %":      pct,
            "PREÇO C/ DESC.":  np.round(filtered["PRECO"].values * factor, 2),
        })

        if not db_ok:
            st.warning(
                "⚠️ BD offline — COD CITEL, Grupo, Marca e Descrição do CITEL indisponíveis. "
                "Exibindo descrições do Excel."
            )

        # JS em fase de captura: intercepta Enter/↓/↑ na TEXTAREA do editor GDG.
        # ESTRATÉGIA CORRETA: re-despachar Enter na própria TEXTAREA com flag
        # __stQtdFwd para que o handler React do GDG leia o valor LIVE do textarea
        # (e.currentTarget.value = valor digitado), não o estado React desatualizado.
        # Para ↑/↓: commita via Enter na textarea, depois navega no canvas.
        st.components.v1.html("""
        <script>
        (function () {
            'use strict';
            // Colapsa o container do iframe para não criar espaço vazio na página
            var fr = window.frameElement;
            if (fr) {
                fr.style.display = 'block';
                fr.style.height   = '0';
                fr.style.overflow = 'hidden';
                var p1 = fr.parentElement;
                if (p1) { p1.style.margin='0'; p1.style.padding='0'; p1.style.minHeight='0'; }
                var p2 = p1 && p1.parentElement;
                if (p2) { p2.style.margin='0'; p2.style.padding='0'; p2.style.minHeight='0'; }
            }

            var doc = window.parent.document;

            // Remove listener anterior antes de registrar novo (garante versão atualizada
            // após hot-reload do Streamlit sem acumular listeners duplicados)
            if (doc.__stQtdNavHandler) {
                doc.removeEventListener('keydown', doc.__stQtdNavHandler, true);
            }

            function navHandler(e) {
                if (e.__stQtdFwd) return;
                if (e.key !== 'Enter' && e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;

                var ta = e.target;
                if (!ta || (ta.tagName !== 'INPUT' && ta.tagName !== 'TEXTAREA')) return;

                // Verifica se é editor GDG (algum pai tem classe gdg-)
                var isGdg = false;
                var el = ta.parentElement;
                for (var i = 0; i < 6 && el; i++) {
                    if (el.className && el.className.indexOf('gdg-') !== -1) {
                        isGdg = true; break;
                    }
                    el = el.parentElement;
                }
                if (!isGdg) return;

                e.preventDefault();
                e.stopImmediatePropagation();

                var isUp = (e.key === 'ArrowUp');
                var canvas = doc.querySelector('[data-testid="data-grid-canvas"]');
                if (!canvas) return;

                // Re-despacha Enter na TEXTAREA com flag __stQtdFwd.
                // O handler React do GDG lê e.currentTarget.value (valor live) → commit correto.
                var fwd = new KeyboardEvent('keydown', {
                    key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
                    bubbles: true, cancelable: true, composed: true
                });
                fwd.__stQtdFwd = true;
                ta.dispatchEvent(fwd);

                setTimeout(function () {
                    canvas.focus();
                    if (isUp) {
                        canvas.dispatchEvent(new KeyboardEvent('keydown', {
                            key: 'ArrowUp', code: 'ArrowUp', keyCode: 38, which: 38,
                            bubbles: true, cancelable: true, composed: true
                        }));
                        canvas.dispatchEvent(new KeyboardEvent('keydown', {
                            key: 'ArrowUp', code: 'ArrowUp', keyCode: 38, which: 38,
                            bubbles: true, cancelable: true, composed: true
                        }));
                    }
                }, 200);
            }

            doc.__stQtdNavHandler = navHandler;
            doc.addEventListener('keydown', navHandler, true);
        }());
        </script>
        """, height=0)

        with st.form(key=f"form_{uf}", enter_to_submit=False):
            edited = st.data_editor(
                display,
                key=f"editor_{uf}_{st.session_state[zerar_key]}",
                column_config={
                    "QTD":             st.column_config.TextColumn("Qtd", width=70),
                    "LINHA":           st.column_config.NumberColumn(width=60, disabled=True),
                    "COD SKU":         st.column_config.TextColumn(width=110, disabled=True),
                    "COD CITEL":       st.column_config.TextColumn("COD CITEL", width=110, disabled=True),
                    "LINHA / GRUPO":   st.column_config.TextColumn("Linha / Grupo", width=180, disabled=True),
                    "MARCA":           st.column_config.TextColumn(width=130, disabled=True),
                    "EMBALAGEM":       st.column_config.TextColumn(width=160, disabled=True),
                    "DESCRIÇÃO / COR": st.column_config.TextColumn(disabled=True),
                    "PREÇO COMPRA":    st.column_config.NumberColumn(
                                           "Preço Compra", format="R$ %.2f", width=140, disabled=True),
                    "DESCONTO %":      st.column_config.NumberColumn(
                                           format="%.2f%%", width=90, disabled=True),
                    "PREÇO C/ DESC.":  st.column_config.NumberColumn(
                                           "Preço c/ Desconto", format="R$ %.2f", width=150, disabled=True),
                },
                hide_index=True,
                use_container_width=True,
            )
            col_aplicar, col_zerar = st.columns(2)
            with col_aplicar:
                form_submitted = st.form_submit_button(
                    "✅ Aplicar Quantidades", type="primary", use_container_width=True
                )
            with col_zerar:
                form_zeroed = st.form_submit_button(
                    "🗑️ Zerar Quantidades", use_container_width=True
                )

        if form_zeroed:
            st.session_state[qtd_key] = {}
            st.session_state[zerar_key] += 1
            st.rerun()

        if form_submitted:
            new_qtd = {}
            for i, qtd_val in enumerate(edited["QTD"].values):
                try:
                    v = max(0, int(float(str(qtd_val)))) if qtd_val is not None else 0
                except (ValueError, TypeError):
                    v = 0
                if v > 0:
                    new_qtd[sku_array[i]] = v
            st.session_state[qtd_key] = new_qtd
            qtd_map = new_qtd

        # ── Rodapé: info ─────────────────────────────────────────────────────────
        # sel construído a partir do qtd_map + rich (inclui itens fora do filtro atual)
        _sel_skus = {sku: q for sku, q in qtd_map.items() if q > 0}
        if _sel_skus:
            _sel_rich = rich[rich["COD_SKU"].isin(_sel_skus)].copy()
            _sel_rich["QTD"] = _sel_rich["COD_SKU"].map(_sel_skus).astype(int)
            _f = 1.0 - pct / 100.0
            sel = pd.DataFrame({
                "COD CITEL":       _sel_rich["COD_CITEL"].values,
                "DESCRIÇÃO / COR": _sel_rich["DESC_FINAL"].values,
                "EMBALAGEM":       _sel_rich["EMBALAGEM"].values,
                "QTD":             _sel_rich["QTD"].values,
                "PREÇO C/ DESC.":  np.round(_sel_rich["PRECO"].values * _f, 2),
            })
        else:
            sel = pd.DataFrame(
                columns=["COD CITEL", "DESCRIÇÃO / COR", "EMBALAGEM", "QTD", "PREÇO C/ DESC."]
            )
        n_sel     = len(sel)
        n_total   = len(rich)
        n_display = len(display)
        filtrado  = f" (filtrado de {n_total})" if n_display < n_total else ""

        st.caption(
            f"**{n_display}** itens{filtrado} — UF: **{uf}** | "
            f"Fonte BD: {'✅ CITEL' if db_ok else '⚠️ offline (usando Excel)'}"
            + (f" | **{n_sel}** selecionado(s) para pedido" if n_sel else "")
        )

        # ── Preview + exportação ──────────────────────────────────────────────────
        if n_sel:
            st.divider()
            st.markdown("#### 🛒 Preview do Pedido")

            # Tabela de preview com totais por linha
            prev = sel[["COD CITEL", "DESCRIÇÃO / COR", "EMBALAGEM", "QTD", "PREÇO C/ DESC."]].copy()
            prev["TOTAL"] = np.round(prev["QTD"] * prev["PREÇO C/ DESC."], 2)

            st.dataframe(
                prev,
                column_config={
                    "COD CITEL":       st.column_config.TextColumn("COD CITEL", width=120),
                    "DESCRIÇÃO / COR": st.column_config.TextColumn("Descrição / Cor"),
                    "EMBALAGEM":       st.column_config.TextColumn("Embalagem", width=160),
                    "QTD":             st.column_config.NumberColumn("Qtd", width=70),
                    "PREÇO C/ DESC.":  st.column_config.NumberColumn(
                                           "Preço Unit.", format="R$ %.2f", width=130),
                    "TOTAL":           st.column_config.NumberColumn(
                                           "Total", format="R$ %.2f", width=130),
                },
                hide_index=True,
                use_container_width=True,
            )

            total_geral = np.round((prev["QTD"] * prev["PREÇO C/ DESC."]).sum(), 2)
            st.markdown(
                f"<div style='text-align:right; font-size:1.1em;'>"
                f"<b>Total geral: R$ {total_geral:,.2f}</b></div>".replace(",", "X").replace(".", ",").replace("X", "."),
                unsafe_allow_html=True,
            )

            # ── Botões de ação ────────────────────────────────────────────────────
            btn_salvar, btn_export = st.columns(2)

            with btn_salvar:
                if st.button("💾 Salvar Pedido", type="primary", use_container_width=True):
                    next_num = max((p["numero"] for p in st.session_state.pedidos), default=0) + 1
                    itens_list = [
                        {
                            "cod_citel":  str(r["COD CITEL"]),
                            "descricao":  str(r["DESCRIÇÃO / COR"]),
                            "embalagem":  str(r["EMBALAGEM"]),
                            "qtd":        int(r["QTD"]),
                            "preco_unit": float(r["PREÇO C/ DESC."]),
                            "total":      float(np.round(r["QTD"] * r["PREÇO C/ DESC."], 2)),
                        }
                        for _, r in sel.iterrows()
                    ]
                    pedido = {
                        "numero":       next_num,
                        "uf":           uf,
                        "data":         datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "desconto_pct": float(pct),
                        "total_geral":  float(total_geral),
                        "itens":        itens_list,
                    }
                    st.session_state.pedidos.append(pedido)
                    PEDIDOS_FILE.write_text(
                        json.dumps(st.session_state.pedidos, ensure_ascii=False, indent=2), "utf-8"
                    )
                    # Limpa QTD do UF atual após salvar
                    st.session_state[f"pedido_qtd_{uf}"] = {}
                    st.toast(f"✅ Pedido #{next_num:04d} salvo com sucesso!", icon="💾")
                    st.rerun()

            with btn_export:
                # Monta Excel: col B = COD CITEL, col F = QTD, col H = Preço c/ Desc.
                rows_ped = []
                for _, r in sel.iterrows():
                    row_ped = [""] * 8
                    row_ped[1] = str(r["COD CITEL"])        # B
                    row_ped[5] = int(r["QTD"])               # F
                    row_ped[7] = float(r["PREÇO C/ DESC."])  # H
                    rows_ped.append(row_ped)
                buf = io.BytesIO()
                pd.DataFrame(rows_ped).to_excel(buf, index=False, header=False)
                buf.seek(0)
                st.download_button(
                    f"📥 Exportar Excel ({n_sel} itens)",
                    data=buf,
                    file_name=f"pedido_{uf}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )


# ══════════════════════════════════════════════════════════════════════════════════
# TAB 2 — PEDIDOS
# ══════════════════════════════════════════════════════════════════════════════════
with tab_ped:
    pedidos_list = st.session_state.pedidos

    if st.session_state.preview_pedido is not None:
        # ── Vista de detalhe ──────────────────────────────────────────────────────
        idx = st.session_state.preview_pedido
        if idx >= len(pedidos_list):
            st.session_state.preview_pedido = None
        else:
            ped = pedidos_list[idx]

            st.button("← Voltar à lista de pedidos",
                      on_click=lambda: st.session_state.update(preview_pedido=None))

            st.markdown(f"## 📄 Pedido #{ped['numero']:04d} — {ped['uf']}")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("UF", ped["uf"])
            m2.metric("Data", ped["data"])
            m3.metric("Desconto", f"{ped['desconto_pct']:.2f}%")
            total_fmt = f"R$ {ped['total_geral']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            m4.metric("Total Geral", total_fmt)

            st.divider()

            itens_df = pd.DataFrame(ped["itens"])
            st.dataframe(
                itens_df,
                column_config={
                    "cod_citel":  st.column_config.TextColumn("COD CITEL", width=120),
                    "descricao":  st.column_config.TextColumn("Descrição / Cor"),
                    "embalagem":  st.column_config.TextColumn("Embalagem", width=160),
                    "qtd":        st.column_config.NumberColumn("Qtd", width=70),
                    "preco_unit": st.column_config.NumberColumn(
                                      "Preço Unit.", format="R$ %.2f", width=130),
                    "total":      st.column_config.NumberColumn(
                                      "Total", format="R$ %.2f", width=130),
                },
                hide_index=True,
                use_container_width=True,
            )

            total_linha = f"R$ {ped['total_geral']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            st.markdown(
                f"<div style='text-align:right;font-size:1.1em;'>"
                f"<b>Total geral: {total_linha}</b></div>",
                unsafe_allow_html=True,
            )

            # ── Editar Pedido (UF + Desconto) ────────────────────────────────────
            st.divider()
            st.markdown("#### ✏️ Editar Pedido")

            # Callback executa ANTES do render → ped já carrega dados atualizados
            def _apply_edit(_idx=idx):
                _ped      = st.session_state.pedidos[_idx]
                _novo_uf  = st.session_state.get(f"nova_uf_{_idx}",  _ped["uf"])
                _novo_pct = st.session_state.get(f"novo_desc_{_idx}", _ped["desconto_pct"])
                _uf_chg   = _novo_uf != _ped["uf"]
                _pct_chg  = abs(_novo_pct - _ped["desconto_pct"]) > 0.001
                if not _uf_chg and not _pct_chg:
                    st.session_state[f"_ed_info_{_idx}"] = True
                    return
                _nao_enc = []
                if _uf_chg:
                    _rich = get_enriched(st.session_state.excel_source, _novo_uf)
                    _lkp  = dict(zip(_rich["COD_CITEL"].astype(str), _rich["PRECO"]))
                    _fn   = 1.0 - _novo_pct / 100.0
                    _itens = []
                    for item in _ped["itens"]:
                        base = _lkp.get(str(item["cod_citel"]))
                        if base is not None:
                            u = round(float(base) * _fn, 2)
                            _itens.append({**item, "preco_unit": u, "total": round(u * item["qtd"], 2)})
                        else:
                            _nao_enc.append(str(item["cod_citel"]))
                            _itens.append(item)
                else:
                    _fo = 1.0 - _ped["desconto_pct"] / 100.0
                    _fn = 1.0 - _novo_pct / 100.0
                    _r  = (_fn / _fo) if _fo != 0 else 0.0
                    _itens = []
                    for item in _ped["itens"]:
                        u = round(item["preco_unit"] * _r, 2)
                        _itens.append({**item, "preco_unit": u, "total": round(u * item["qtd"], 2)})
                _ntg = round(sum(i["total"] for i in _itens), 2)
                st.session_state.pedidos[_idx] = {
                    **_ped, "uf": _novo_uf, "desconto_pct": float(_novo_pct),
                    "total_geral": _ntg, "itens": _itens,
                }
                PEDIDOS_FILE.write_text(
                    json.dumps(st.session_state.pedidos, ensure_ascii=False, indent=2), "utf-8"
                )
                if _nao_enc:
                    st.session_state[f"_ed_warn_{_idx}"] = _nao_enc
                partes = []
                if _uf_chg:  partes.append(f"UF → {_novo_uf}")
                if _pct_chg: partes.append(f"Desconto → {_novo_pct:.2f}%")
                st.toast(f"Pedido atualizado: {' | '.join(partes)}", icon="✅")

            col_uf_e, col_desc_e, col_btn_e, _ = st.columns([1.2, 1.2, 0.8, 2])
            with col_uf_e:
                st.selectbox(
                    "UF", STATES,
                    index=STATES.index(ped["uf"]),
                    key=f"nova_uf_{idx}",
                )
            with col_desc_e:
                st.number_input(
                    "Desconto (%)",
                    min_value=0.0, max_value=99.99,
                    value=float(ped["desconto_pct"]),
                    step=0.5, format="%.2f",
                    key=f"novo_desc_{idx}",
                )
            with col_btn_e:
                st.write("")
                st.button("✅ Aplicar", key=f"btn_ed_{idx}",
                          use_container_width=True, on_click=_apply_edit)

            if st.session_state.pop(f"_ed_info_{idx}", False):
                st.info("Nenhuma alteração detectada.")
            if f"_ed_warn_{idx}" in st.session_state:
                _w = st.session_state.pop(f"_ed_warn_{idx}")
                st.warning(
                    f"⚠️ {len(_w)} item(ns) não encontrado(s) em {ped['uf']} "
                    f"(COD CITEL: {', '.join(_w)}) — preço original mantido."
                )

            st.divider()
            col_exp, col_del = st.columns(2)

            with col_exp:
                rows_exp = [[""] * 8 for _ in ped["itens"]]
                for i, item in enumerate(ped["itens"]):
                    rows_exp[i][1] = str(item["cod_citel"])
                    rows_exp[i][5] = int(item["qtd"])
                    rows_exp[i][7] = float(item["preco_unit"])
                buf_det = io.BytesIO()
                pd.DataFrame(rows_exp).to_excel(buf_det, index=False, header=False)
                buf_det.seek(0)
                st.download_button(
                    "📥 Exportar Excel",
                    data=buf_det,
                    file_name=f"pedido_{ped['numero']:04d}_{ped['uf']}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

            with col_del:
                def _discard(_idx=idx):
                    _ped_num = st.session_state.pedidos[_idx]["numero"]
                    st.session_state.pedidos.pop(_idx)
                    PEDIDOS_FILE.write_text(
                        json.dumps(st.session_state.pedidos, ensure_ascii=False, indent=2), "utf-8"
                    )
                    st.session_state.preview_pedido = None
                    st.toast(f"Pedido #{_ped_num:04d} descartado.", icon="🗑️")

                st.button("🗑️ Descartar Pedido", use_container_width=True,
                          on_click=_discard)

    else:
        # ── Lista de pedidos por UF ───────────────────────────────────────────────
        if not pedidos_list:
            st.info(
                "Nenhum pedido salvo ainda. "
                "Vá para **Simulador**, monte um pedido e clique **💾 Salvar Pedido**."
            )
        else:
            for uf_g in STATES:
                grupo = [(i, p) for i, p in enumerate(pedidos_list) if p["uf"] == uf_g]
                if not grupo:
                    continue
                st.markdown(f"### 🗺️ {uf_g} — {len(grupo)} pedido(s)")
                for orig_idx, ped in grupo:
                    total_fmt = (
                        f"R$ {ped['total_geral']:,.2f}"
                        .replace(",", "X").replace(".", ",").replace("X", ".")
                    )
                    n_itens = len(ped["itens"])
                    col_info, col_btn = st.columns([6, 1])
                    with col_info:
                        st.markdown(
                            f"📄 **Pedido #{ped['numero']:04d}** &nbsp;·&nbsp; "
                            f"{ped['data']} &nbsp;·&nbsp; "
                            f"{n_itens} item(ns) &nbsp;·&nbsp; **{total_fmt}**"
                        )
                    with col_btn:
                        st.button(
                            "👁️ Ver",
                            key=f"ver_{orig_idx}",
                            use_container_width=True,
                            on_click=lambda _i=orig_idx: st.session_state.update(preview_pedido=_i),
                        )
                st.divider()


# ══════════════════════════════════════════════════════════════════════════════════
# TAB 3 — SIMILARES / COMPARATIVO
# ══════════════════════════════════════════════════════════════════════════════════
with tab_comp:
    src = st.session_state.excel_source
    if not src or not Path(src).exists():
        st.info("Nenhuma tabela carregada.")
    else:
        # Todos retornam de cache — ~0 ms em todos os renders após o primeiro
        opcoes    = get_product_opcoes(src)
        indexed   = get_states_indexed(src)
        db_lookup = get_db_lookup(src)

        st.markdown("### Adicionar Par de Similares")
        cc1, cc2, cc3 = st.columns([4, 4, 1])
        with cc1:
            search_a = st.text_input(
                "🔍 Buscar Item A",
                placeholder="Digite palavras em qualquer ordem",
                key="search_a",
            )
            words_a = [w for w in search_a.upper().split() if w]
            opts_a  = [o for o in opcoes if all(w in o.upper() for w in words_a)] if words_a else opcoes
            item_a  = st.selectbox("Item A (produto base)", opts_a or opcoes, key="add_a")
        with cc2:
            search_b = st.text_input(
                "🔍 Buscar Item B",
                placeholder="Digite palavras em qualquer ordem",
                key="search_b",
            )
            words_b = [w for w in search_b.upper().split() if w]
            opts_b  = [o for o in opcoes if all(w in o.upper() for w in words_b)] if words_b else opcoes
            item_b  = st.selectbox("Item B (produto similar)", opts_b or opcoes, key="add_b")
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
                info_a  = db_lookup.get(pair["sku_a"], {})
                info_b  = db_lookup.get(pair["sku_b"], {})
                marca_a = info_a.get("MARCA", "")
                marca_b = info_b.get("MARCA", "")
                desc_a  = info_a.get("DESCRICAO_DB", pair["label_a"])
                desc_b  = info_b.get("DESCRICAO_DB", pair["label_b"])

                hdr_a  = f"[{marca_a}] {desc_a}" if marca_a else desc_a
                hdr_b  = f"[{marca_b}] {desc_b}" if marca_b else desc_b
                titulo = f"🔄 **{hdr_a}**  ×  **{hdr_b}**"

                with st.expander(titulo, expanded=True):
                    # ── Descrição dos produtos ────────────────────────────────────────────
                    ca, cb = st.columns(2)
                    with ca:
                        grupo_a = info_a.get("GRUPO", "")
                        st.markdown(
                            f"**SKU A:** `{pair['sku_a']}`  \n"
                            f"**Marca:** {marca_a or '—'}  \n"
                            f"**Grupo:** {grupo_a or '—'}  \n"
                            f"**Descrição:** {desc_a}"
                        )
                    with cb:
                        grupo_b = info_b.get("GRUPO", "")
                        st.markdown(
                            f"**SKU B:** `{pair['sku_b']}`  \n"
                            f"**Marca:** {marca_b or '—'}  \n"
                            f"**Grupo:** {grupo_b or '—'}  \n"
                            f"**Descrição:** {desc_b}"
                        )
                    st.divider()

                    # ── Tabela de preços por UF ───────────────────────────────────────────
                    rows = []
                    for uf in STATES:
                        idx_uf  = indexed[uf]
                        sku_a   = pair["sku_a"]
                        sku_b   = pair["sku_b"]
                        # O(1) com índice — antes era O(n) com boolean filter
                        preco_a = float(idx_uf.at[sku_a, "PRECO"])  if sku_a in idx_uf.index else None
                        preco_b = float(idx_uf.at[sku_b, "PRECO"])  if sku_b in idx_uf.index else None
                        emb_a   = idx_uf.at[sku_a, "EMBALAGEM"]     if sku_a in idx_uf.index else "—"
                        emb_b   = idx_uf.at[sku_b, "EMBALAGEM"]     if sku_b in idx_uf.index else "—"
                        diff    = round(preco_a - preco_b, 2) if (
                            preco_a is not None and preco_b is not None
                        ) else None
                        rows.append({
                            "UF":             uf,
                            "Emb. A":         emb_a,
                            "Preço Compra A": preco_a,
                            "Emb. B":         emb_b,
                            "Preço Compra B": preco_b,
                            "Diferença":      diff,
                        })

                    st.dataframe(
                        pd.DataFrame(rows),
                        column_config={
                            "Preço Compra A": st.column_config.NumberColumn(format="R$ %.2f"),
                            "Preço Compra B": st.column_config.NumberColumn(format="R$ %.2f"),
                            "Diferença":      st.column_config.NumberColumn(format="R$ %.2f"),
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
                st.session_state.pop("caches_warmed", None)
                _clear_all_caches()
                st.toast("Tabela atualizada com sucesso!", icon="🎨")
                st.rerun()
        except Exception as e:
            dest.unlink(missing_ok=True)
            st.error(f"Erro ao ler o arquivo: {e}")

    if st.session_state.excel_source and Path(st.session_state.excel_source).exists():
        st.info(f"**Tabela ativa:** `{Path(st.session_state.excel_source).name}`")
