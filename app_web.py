"""
Toque de Cor — Sistema de Pedidos Web
Arquivo principal — ponto de entrada do Streamlit

Execute: streamlit run app_web.py
"""

import io
import os
import sys
from pathlib import Path

# Carrega .env se disponível (desenvolvimento local)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Bridge: injeta st.secrets → os.environ (usado no Streamlit Community Cloud)
# Precisa estar ANTES de qualquer import que leia os.environ
import streamlit as st  # noqa: E402 — necessário aqui para st.secrets
try:
    for _sk, _sv in st.secrets.items():
        if isinstance(_sv, str):
            os.environ.setdefault(_sk, _sv)
except Exception:
    pass

import numpy as np
import pandas as pd

# ── Configuração da página ────────────────────────────────────────────────────
st.set_page_config(
    page_title="Toque de Cor — Pedidos",
    page_icon="🎨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Módulos internos ──────────────────────────────────────────────────────────
import auth
from db import (                          # db.py original — MySQL/CITEL
    query_items, test_connection,
    clear_disk_cache, get_cached_data,
)
from db_supabase import supabase_ok, get_config, catalogo_disponivel, get_catalogo_uf
from app_pages import admin, catalog, history

# ── Constantes ────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
_env_data     = os.environ.get("TOQUEDECOR_DATA_DIR", "")
DATA_DIR      = Path(_env_data) if _env_data else BASE_DIR
STATES        = ["RN", "BA", "PE", "AL", "PB"]
DEFAULT_EXCEL = BASE_DIR / "Tabela SW Suvinil Geral.xlsx"

# ── Session state inicial ─────────────────────────────────────────────────────
if "excel_source" not in st.session_state:
    src_salvo = get_config("excel_path", "")
    if src_salvo and Path(src_salvo).exists():
        # Caminho explícito salvo no banco → usa direto
        st.session_state.excel_source = src_salvo
    else:
        # Prioridade: Supabase Storage (arquivo importado pelo admin) > DEFAULT_EXCEL
        # DEFAULT_EXCEL está no Docker mas pode estar desatualizado.
        _excel_path = None
        try:
            from db_supabase import download_excel_storage
            _data, _fname = download_excel_storage()
            if _data and _fname:
                import tempfile
                _dest = Path(tempfile.gettempdir()) / _fname
                _dest.write_bytes(_data)
                _excel_path = str(_dest)
        except Exception:
            pass

        if _excel_path:
            st.session_state.excel_source = _excel_path
        elif DEFAULT_EXCEL.exists():
            st.session_state.excel_source = str(DEFAULT_EXCEL)
        else:
            st.session_state.excel_source = None

# ══════════════════════════════════════════════════════════════════════════════
# FUNÇÕES DE CACHE (idênticas ao app original — preservam todo o histórico)
# ══════════════════════════════════════════════════════════════════════════════
@st.cache_resource
def read_uf(path: str, uf: str) -> pd.DataFrame:
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
    return {uf: read_uf(path, uf) for uf in STATES}


@st.cache_resource
def get_db_data(excel_path: str) -> pd.DataFrame:
    cached = get_cached_data()
    if cached is not None and "GRUPO" in cached.columns:
        return cached
    if cached is not None:
        clear_disk_cache()
    all_states = read_all_states(excel_path)
    all_skus: set = set()
    for df in all_states.values():
        all_skus.update(df["COD_SKU"].tolist())
    return query_items(list(all_skus))


@st.cache_resource
def get_enriched(path: str, uf: str) -> pd.DataFrame:
    """
    Retorna DataFrame enriquecido para a UF.
    Fonte primária: Supabase (instantâneo).
    Fallback: Excel + MySQL (lento, só se Supabase vazio).
    """
    # ── Fast path: Supabase ───────────────────────────────────────────────────
    if catalogo_disponivel():
        df = get_catalogo_uf(uf)
        if not df.empty:
            return df

    # ── Slow path: Excel + CITEL ──────────────────────────────────────────────
    raw = read_uf(path, uf)
    db  = get_db_data(path)
    result = raw.copy()
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


@st.cache_resource
def get_product_opcoes(path: str) -> list:
    # Fast path: Supabase já tem MARCA e DESCRICAO_DB merged
    if catalogo_disponivel():
        df_ref = get_catalogo_uf(STATES[0])
        if df_ref.empty:
            for _uf in STATES[1:]:
                df_ref = get_catalogo_uf(_uf)
                if not df_ref.empty:
                    break
        if not df_ref.empty:
            skus_df = df_ref.drop_duplicates("COD_SKU")[["COD_SKU","DESCRICAO","DESCRICAO_DB","MARCA"]].copy()
            skus_df["DESCRICAO_DB"] = skus_df["DESCRICAO_DB"].fillna("").astype(str)
            skus_df["MARCA"]        = skus_df["MARCA"].fillna("").astype(str)
            skus_df["DESC_SHOW"]    = np.where(skus_df["DESCRICAO_DB"] != "", skus_df["DESCRICAO_DB"], skus_df["DESCRICAO"])
            has_m  = skus_df["MARCA"].ne("")
            with_m = skus_df["COD_SKU"] + " — [" + skus_df["MARCA"] + "] " + skus_df["DESC_SHOW"]
            no_m   = skus_df["COD_SKU"] + " — " + skus_df["DESC_SHOW"]
            return np.where(has_m, with_m, no_m).tolist()
    # Slow path: Excel + MySQL
    all_states = read_all_states(path)
    skus_df = (
        pd.concat([df[["COD_SKU","DESCRICAO"]].drop_duplicates("COD_SKU") for df in all_states.values()])
        .drop_duplicates("COD_SKU").reset_index(drop=True)
    )
    db = get_db_data(path)
    if not db.empty:
        skus_df = skus_df.merge(
            db[["COD_FAB","DESCRICAO_DB","MARCA"]], left_on="COD_SKU", right_on="COD_FAB", how="left"
        ).drop(columns=["COD_FAB"])
        skus_df["DESCRICAO_DB"] = skus_df["DESCRICAO_DB"].fillna("").astype(str)
        skus_df["MARCA"]        = skus_df["MARCA"].fillna("").astype(str)
        skus_df["DESC_SHOW"]    = np.where(skus_df["DESCRICAO_DB"] != "", skus_df["DESCRICAO_DB"], skus_df["DESCRICAO"])
    else:
        skus_df["MARCA"] = ""
        skus_df["DESC_SHOW"] = skus_df["DESCRICAO"]
    has_m  = skus_df["MARCA"].ne("")
    with_m = skus_df["COD_SKU"] + " — [" + skus_df["MARCA"] + "] " + skus_df["DESC_SHOW"]
    no_m   = skus_df["COD_SKU"] + " — " + skus_df["DESC_SHOW"]
    return np.where(has_m, with_m, no_m).tolist()


@st.cache_resource
def get_states_indexed(path: str) -> dict:
    # Fast path: Supabase
    if catalogo_disponivel():
        result = {}
        for _uf in STATES:
            df = get_catalogo_uf(_uf)
            if not df.empty:
                result[_uf] = df.drop_duplicates("COD_SKU", keep="first").set_index("COD_SKU")[["PRECO","EMBALAGEM"]]
        if result:
            return result
    # Slow path: Excel
    all_states = read_all_states(path)
    return {
        uf: df.drop_duplicates("COD_SKU", keep="first").set_index("COD_SKU")[["PRECO","EMBALAGEM"]]
        for uf, df in all_states.items()
    }


@st.cache_resource
def get_db_lookup(path: str) -> dict:
    # Fast path: Supabase já tem os dados CITEL merged no catálogo
    if catalogo_disponivel():
        df_ref = get_catalogo_uf(STATES[0])
        if not df_ref.empty:
            cols = [c for c in ["MARCA","DESCRICAO_DB","GRUPO"] if c in df_ref.columns]
            return df_ref.drop_duplicates("COD_SKU").set_index("COD_SKU")[cols].to_dict("index")
    # Slow path: MySQL
    db = get_db_data(path)
    if db.empty:
        return {}
    cols = ["MARCA","DESCRICAO_DB"] + (["GRUPO"] if "GRUPO" in db.columns else [])
    return db.set_index("COD_FAB")[cols].to_dict("index")


@st.cache_resource(ttl=300)
def check_db() -> tuple:
    return test_connection()


def _clear_all_caches():
    clear_disk_cache()
    # Nuclear clear — apaga TODOS os caches do Streamlit de uma vez
    try:
        st.cache_data.clear()
        st.cache_resource.clear()
    except Exception:
        pass
    # Fallback individual (garantia extra)
    for fn in (get_db_data, get_enriched, get_product_opcoes,
               get_states_indexed, get_db_lookup, read_uf, read_all_states,
               get_catalogo_uf, catalogo_disponivel):
        try:
            fn.clear()
        except Exception:
            pass





# ══════════════════════════════════════════════════════════════════════════════
# AUTENTICAÇÃO — bloqueia tudo se não logado
# ══════════════════════════════════════════════════════════════════════════════
if not auth.requer_login():
    st.stop()

u      = auth.usuario_atual()
perfil = u.get("perfil", "vendedor")

# ── Sidebar ───────────────────────────────────────────────────────────────────
# Renderiza SEMPRE primeiro — garante que o app aparece imediatamente após login.
with st.sidebar:
    st.image(str(BASE_DIR / "logo.png"), use_container_width=True)
    st.markdown(f"**{u.get('nome', u.get('usuario',''))}**")
    st.caption(f"{auth.PERFIS.get(perfil, perfil)} · {u.get('loja','')}")
    st.divider()

    # Navegação
    paginas_disp = ["📋 Simulador", "📦 Pedidos"]
    if perfil in ("admin", "supervisor"):
        paginas_disp.append("🔄 Similares")
    if perfil == "admin":
        paginas_disp.append("⚙️ Admin")

    pagina = st.radio("Navegação", paginas_disp, label_visibility="collapsed")
    st.divider()

    # Status BD: carrega uma vez por sessão; só reatualiza ao clicar em Recarregar BD
    if "sidebar_db_ok" not in st.session_state:
        db_ok, db_msg = check_db()
        sb_ok = supabase_ok()
        ultima = get_config("ultima_importacao", "")
        # Verifica se citel_itens do Supabase tem dados (fallback)
        try:
            from db_supabase import get_citel_itens
            _df_c = get_citel_itens()
            citel_via_sb = _df_c is not None and not _df_c.empty
        except Exception:
            citel_via_sb = False
        st.session_state.sidebar_db_ok      = db_ok
        st.session_state.sidebar_sb_ok      = sb_ok
        st.session_state.sidebar_ultima     = ultima
        st.session_state.sidebar_citel_via_sb = citel_via_sb
    else:
        db_ok        = st.session_state.sidebar_db_ok
        sb_ok        = st.session_state.sidebar_sb_ok
        ultima       = st.session_state.sidebar_ultima
        citel_via_sb = st.session_state.get("sidebar_citel_via_sb", False)

    st.caption("**Status dos Bancos**")
    st.caption(f"{'🟢' if db_ok else '🔴'} MySQL CITEL")
    st.caption(f"{'🟢' if sb_ok else '🔴'} Supabase")

    _lbl_reload = "🔄 Recarregar BD" if db_ok else "🔁 Tentar reconectar BD"
    if st.button(_lbl_reload, use_container_width=True):
        _clear_all_caches()
        check_db.clear()
        st.session_state.pop("caches_warmed", None)
        st.session_state.pop("sidebar_db_ok", None)
        st.session_state.pop("sidebar_citel_via_sb", None)
        st.rerun()

    if ultima:
        st.caption(f"📄 Tabela: {ultima}")

    st.divider()
    if st.button("🚪 Sair", use_container_width=True):
        auth.fazer_logout()
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# BARRA DE CARREGAMENTO — exibida na área principal logo após o login.
# A sidebar já está visível; o app abre normalmente após o st.rerun().
# ══════════════════════════════════════════════════════════════════════════════
if "caches_warmed" not in st.session_state:
    import time as _time
    _nome_u = u.get("nome", u.get("usuario", ""))

    st.image(str(BASE_DIR / "logo.png"), width=280)
    st.markdown(
        f"<p style='color:#aaa'>Bem-vindo(a), <b>{_nome_u}</b> — carregando catálogo...</p>",
        unsafe_allow_html=True,
    )
    _prog   = st.progress(0, text="Iniciando...")
    _status = st.empty()

    try:
        if catalogo_disponivel():
            _STATES_W = ["RN", "BA", "PE", "AL", "PB"]
            _n = len(_STATES_W)
            _prog.progress(5, text="🔗 Conectando ao catálogo...")
            _time.sleep(0.1)
            for _i, _uf in enumerate(_STATES_W):
                _prog.progress(10 + int(_i / _n * 80),
                               text=f"Carregando {_uf}... ({_i + 1}/{_n})")
                get_catalogo_uf(_uf)
                _prog.progress(10 + int((_i + 1) / _n * 80),
                               text=f"✔ {_uf} carregado ({_i + 1}/{_n})")
                _status.caption(f"📦 **{_uf}** carregado")
                _time.sleep(0.08)
            _prog.progress(100, text="✅ Pronto!")
            _status.caption("Abrindo o aplicativo...")
            _time.sleep(0.3)

        elif st.session_state.get("excel_source") and Path(st.session_state["excel_source"]).exists():
            _prog.progress(10, text="📂 Lendo tabela Excel...")
            read_all_states(st.session_state["excel_source"])
            _prog.progress(65, text="🔗 Consultando banco CITEL...")
            get_db_data(st.session_state["excel_source"])
            _prog.progress(100, text="✅ Pronto!")
            _status.caption("Abrindo o aplicativo...")
            _time.sleep(0.4)

        else:
            for _p, _txt in [(30, "Verificando configuração..."),
                             (70, "Preparando interface..."),
                             (100, "✅ Pronto!")]:
                _prog.progress(_p, text=_txt)
                _time.sleep(0.12)

    except Exception as _err:
        _prog.progress(100, text="⚠️ Erro ao carregar — abrindo mesmo assim")
        _status.caption(f"Detalhe: {_err}")
        _time.sleep(1)

    # st.rerun() FORA do try/finally — garante que o Streamlit envia todas as
    # atualizações da barra ao browser antes de iniciar o novo render.
    st.session_state["caches_warmed"] = True
    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# ROTEAMENTO DE PÁGINAS
# ══════════════════════════════════════════════════════════════════════════════
if pagina == "📋 Simulador":
    st.markdown("## 🎨 Toque de Cor — Simulador de Pedidos")
    st.divider()
    catalog.render(
        get_enriched_fn=get_enriched,
        get_product_opcoes_fn=get_product_opcoes,
        get_states_indexed_fn=get_states_indexed,
        get_db_lookup_fn=get_db_lookup,
        read_all_states_fn=read_all_states,
        get_db_data_fn=get_db_data,
        check_db_fn=check_db,
        excel_source_key="excel_source",
    )

elif pagina == "📦 Pedidos":
    history.render()

elif pagina == "🔄 Similares":
    src = st.session_state.get("excel_source", "")
    _sb_ok = catalogo_disponivel()
    if not _sb_ok and (not src or not Path(src).exists()):
        st.info("Nenhuma tabela carregada.")
    else:
        from pathlib import Path as _P
        import json

        SIMILARES_FILE = DATA_DIR / "similares.json"
        if "similares" not in st.session_state:
            st.session_state.similares = (
                json.loads(SIMILARES_FILE.read_text("utf-8")) if SIMILARES_FILE.exists() else []
            )

        st.markdown("## 🔄 Similares / Comparativo")
        opcoes    = get_product_opcoes(src)
        indexed   = get_states_indexed(src)
        db_lookup = get_db_lookup(src)

        st.markdown("### Adicionar Par de Similares")
        cc1, cc2, cc3 = st.columns([4, 4, 1])
        with cc1:
            sa = st.text_input("🔍 Buscar Item A", key="search_a_w")
            wa = [w for w in sa.upper().split() if w]
            oa = [o for o in opcoes if all(w in o.upper() for w in wa)] if wa else opcoes
            ia = st.selectbox("Item A", oa or opcoes, key="add_a_w")
        with cc2:
            sb_ = st.text_input("🔍 Buscar Item B", key="search_b_w")
            wb  = [w for w in sb_.upper().split() if w]
            ob  = [o for o in opcoes if all(w in o.upper() for w in wb)] if wb else opcoes
            ib  = st.selectbox("Item B", ob or opcoes, key="add_b_w")
        with cc3:
            st.write(""); st.write("")
            if st.button("➕ Adicionar", use_container_width=True):
                sku_a = ia.split(" — ")[0]
                sku_b = ib.split(" — ")[0]
                if sku_a == sku_b:
                    st.warning("Selecione dois produtos diferentes.")
                else:
                    pair = {"sku_a": sku_a, "label_a": ia, "sku_b": sku_b, "label_b": ib}
                    existing = [(p["sku_a"], p["sku_b"]) for p in st.session_state.similares]
                    if (sku_a, sku_b) not in existing and (sku_b, sku_a) not in existing:
                        st.session_state.similares.append(pair)
                        SIMILARES_FILE.write_text(
                            json.dumps(st.session_state.similares, ensure_ascii=False, indent=2), "utf-8"
                        )
                        st.rerun()
                    else:
                        st.warning("Par já cadastrado.")

        st.divider()
        for idx, pair in enumerate(st.session_state.similares):
            info_a = db_lookup.get(pair["sku_a"], {})
            info_b = db_lookup.get(pair["sku_b"], {})
            desc_a = info_a.get("DESCRICAO_DB", pair["label_a"])
            desc_b = info_b.get("DESCRICAO_DB", pair["label_b"])
            with st.expander(f"🔄 **{desc_a}** × **{desc_b}**", expanded=True):
                rows = []
                for uf in STATES:
                    idx_uf  = indexed[uf]
                    sa_sku  = pair["sku_a"]
                    sb_sku  = pair["sku_b"]
                    pa = float(idx_uf.at[sa_sku, "PRECO"]) if sa_sku in idx_uf.index else None
                    pb = float(idx_uf.at[sb_sku, "PRECO"]) if sb_sku in idx_uf.index else None
                    rows.append({
                        "UF": uf,
                        "Preço A": pa, "Preço B": pb,
                        "Diferença": round(pa - pb, 2) if pa is not None and pb is not None else None,
                    })
                st.dataframe(
                    pd.DataFrame(rows),
                    column_config={
                        "Preço A":    st.column_config.NumberColumn(format="R$ %.2f"),
                        "Preço B":    st.column_config.NumberColumn(format="R$ %.2f"),
                        "Diferença":  st.column_config.NumberColumn(format="R$ %.2f"),
                    },
                    hide_index=True, use_container_width=True,
                )
                if st.button("🗑️ Remover", key=f"del_sim_{idx}"):
                    st.session_state.similares.pop(idx)
                    SIMILARES_FILE.write_text(
                        json.dumps(st.session_state.similares, ensure_ascii=False, indent=2), "utf-8"
                    )
                    st.rerun()

elif pagina == "⚙️ Admin":
    admin.render(
        excel_source_key="excel_source",
        clear_caches_fn=_clear_all_caches,
    )
