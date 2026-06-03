"""
Simulador de Pedidos (Catálogo) — Toque de Cor Web

Permissões:
  - Preços: visíveis apenas para admin e supervisor
  - Desconto: editável apenas por admin e supervisor
  - Envio de pedido: todos os perfis com 'fazer_pedidos'
"""

import io
import numpy as np
import pandas as pd
import streamlit as st
from pathlib import Path
from datetime import datetime

import auth
from orders import (
    exportar_excel_suvinil,
    exportar_excel_sw,
    exportar_excel_completo,
    enviar_email_pedido,
    _classifica_marca,
)
from db_supabase import (
    salvar_pedido,
    atualizar_status_pedido,
    get_config,
    registrar_auditoria,
    get_similares,
)

STATES = ["RN", "BA", "PE", "AL", "PB"]

# Filtro global de marcas visíveis no catálogo.
# Para exibir todas as marcas, deixe a lista vazia: []
_MARCAS_VISIVEIS = ["SUVINIL", "SHERWIN WILLIAMS IMOBI/SHERWIN"]


def _render_editor(
    uf: str,
    qtd_key: str,
    zerar_key: str,
    aplc_key: str,
    dlg_key: str,
    sim_sup_key: str,
    flt_cnt_key: str,
    ver_preco: bool,
    pct: float,
    factor: float,
    db_ok: bool,
):
    """
    Renderiza o data_editor de quantidades como um fragmento independente.
    On_change dispara rerun apenas do fragmento — o cursor no editor é preservado
    ao pressionar Enter, sem reruns completos da página.
    Botões Aplicar/Zerar usam st.rerun(scope='app') para atualizar o preview.
    """
    filtered    = st.session_state.get(f"_frag_filtered_{uf}", pd.DataFrame())
    rich        = st.session_state.get(f"_frag_rich_{uf}",     pd.DataFrame())
    _sim_lookup = st.session_state.get(f"_sim_lookup_{uf}",    {})

    if filtered.empty or rich.empty:
        return

    qtd_map   = st.session_state[qtd_key]
    sku_array = filtered["COD_SKU"].values

    _tem_similar_na_lista = any(sku in _sim_lookup for sku in sku_array)
    if _tem_similar_na_lista:
        st.caption("💡 Linhas com 🔄 possuem similar — marque ☑ para ver o similar (abre janela, funciona em tela cheia).")

    # ── Tabela principal (QTD editável + checkbox para ver similar) ───────────
    _display = {
        "ver_sim":       [False] * len(sku_array),
        "🔄":            ["🔄" if sku in _sim_lookup else "" for sku in sku_array],
        "QTD":           [int(qtd_map.get(sku, 0)) for sku in sku_array],
        "LINHA":         filtered["LINHA"].values,
        "COD SKU":       filtered["COD_SKU"].values,
        "COD CITEL":     filtered["COD_CITEL"].values,
        "LINHA / GRUPO": filtered["GRUPO"].values,
        "MARCA":         filtered["MARCA"].values,
        "EMBALAGEM":     filtered["EMBALAGEM"].values,
        "DESCRIÇÃO":     filtered["DESC_FINAL"].values,
    }
    if ver_preco:
        _display["PREÇO COMPRA"]   = filtered["PRECO"].values
        _display["DESCONTO %"]     = pct
        _display["PREÇO C/ DESC."] = np.round(filtered["PRECO"].values * factor, 2)

    _df_edit = pd.DataFrame(_display)
    _df_edit["ver_sim"] = _df_edit["ver_sim"].astype(bool)

    _col_cfg = {
        "ver_sim":       st.column_config.CheckboxColumn("☑", width=35,
                             help="Marque para ver o similar disponível"),
        "🔄":            st.column_config.TextColumn("🔄", width=40,  disabled=True,
                             help="Produto possui similar cadastrado"),
        "QTD":           st.column_config.NumberColumn("Qtd", width=70, min_value=0, step=1),
        "LINHA":         st.column_config.NumberColumn(width=60,  disabled=True),
        "COD SKU":       st.column_config.TextColumn(width=110,   disabled=True),
        "COD CITEL":     st.column_config.TextColumn("COD CITEL", width=110, disabled=True),
        "LINHA / GRUPO": st.column_config.TextColumn("Linha/Grupo", width=180, disabled=True),
        "MARCA":         st.column_config.TextColumn(width=130,   disabled=True),
        "EMBALAGEM":     st.column_config.TextColumn(width=160,   disabled=True),
        "DESCRIÇÃO":     st.column_config.TextColumn(disabled=True),
    }
    if ver_preco:
        _col_cfg["PREÇO COMPRA"]   = st.column_config.NumberColumn("Preço Compra",   format="R$ %.2f", width=140, disabled=True)
        _col_cfg["DESCONTO %"]     = st.column_config.NumberColumn(format="%.2f%%",  width=90,         disabled=True)
        _col_cfg["PREÇO C/ DESC."] = st.column_config.NumberColumn("Preço c/ Desc.", format="R$ %.2f", width=150, disabled=True)

    if not db_ok:
        citel_via_sb = st.session_state.get("sidebar_citel_via_sb", False)
        if not citel_via_sb:
            st.warning("⚠️ BD offline — COD CITEL, Grupo e Marca indisponíveis.")

    _editor_key = f"editor_{uf}_{st.session_state[zerar_key]}_{st.session_state[aplc_key]}_{st.session_state[dlg_key]}_{st.session_state[flt_cnt_key]}"

    edited = st.data_editor(
        _df_edit,
        key=_editor_key,
        column_config=_col_cfg,
        hide_index=True,
        use_container_width=True,
    )

    st.session_state[f"_prev_sku_{uf}"] = list(map(str, sku_array))

    # ── Diálogo de similar ────────────────────────────────────────────────────
    _marcadas = edited[edited["ver_sim"] == True]
    if not _marcadas.empty:
        _idx_marcado = _marcadas.index[0]
        if _idx_marcado < len(sku_array):
            _sku_marcado = str(sku_array[_idx_marcado])
            if _sku_marcado in _sim_lookup:
                _sku_sim = _sim_lookup[_sku_marcado]["sku"]
                if st.session_state.get(sim_sup_key) != _sku_marcado:
                    st.session_state[sim_sup_key] = _sku_marcado
                    _dlg_similar(
                        _sku_marcado, _sku_sim,
                        filtered[filtered["COD_SKU"] == _sku_marcado],
                        rich[rich["COD_SKU"] == _sku_sim],
                        ver_preco, dlg_key,
                    )
            else:
                st.info(f"O produto `{_sku_marcado}` não possui similar cadastrado.")
    else:
        st.session_state.pop(sim_sup_key, None)

    # ── Botões Aplicar / Zerar ────────────────────────────────────────────────
    _col_ap, _col_zer = st.columns(2)
    with _col_ap:
        if st.button("✅ Aplicar Quantidades", type="primary",
                     use_container_width=True, key=f"btn_ap_{uf}"):
            for i in range(len(sku_array)):
                try:
                    _v = edited.iloc[i]["QTD"]
                    _v = max(0, int(float(str(_v)))) if _v is not None else 0
                except (ValueError, TypeError):
                    _v = 0
                _s = str(sku_array[i])
                if _v > 0:
                    st.session_state[qtd_key][_s] = _v
                else:
                    st.session_state[qtd_key].pop(_s, None)
            st.session_state[aplc_key] += 1
            st.rerun(scope="app")
    with _col_zer:
        if st.button("🗑️ Zerar Quantidades", use_container_width=True,
                     key=f"btn_zer_{uf}"):
            st.session_state[qtd_key]   = {}
            st.session_state[zerar_key] += 1
            st.rerun(scope="app")

    # ── Rodapé: contagem ──────────────────────────────────────────────────────
    _sel_skus_frag = {sku: q for sku, q in st.session_state[qtd_key].items() if q > 0}
    n_total   = len(rich)
    n_display = len(_df_edit)
    filtrado  = f" (filtrado de {n_total})" if n_display < n_total else ""
    st.caption(
        f"**{n_display}** itens{filtrado} — UF: **{uf}**"
        + (f" | **{len(_sel_skus_frag)}** selecionado(s)" if _sel_skus_frag else "")
    )


@st.dialog("🔄 Similar disponível", width="large")
def _dlg_similar(sku_a: str, sku_b: str, ra, rb, ver_preco: bool, dlg_cnt_key: str):  # noqa: ARG001
    """Modal de comparação de similar — aparece sobre a tabela (inclusive em tela cheia)."""
    _ca, _cb = st.columns(2)
    with _ca:
        st.markdown("##### Item selecionado")
        if not ra.empty:
            _r = ra.iloc[0]
            st.markdown(f"`{sku_a}` {_r['DESC_FINAL']}")
            st.caption(f"Marca: **{_r['MARCA']}** · Emb: **{_r['EMBALAGEM']}**")
            if ver_preco:
                st.caption(f"Preço: **R$ {float(_r['PRECO']):.2f}**")
        else:
            st.caption("Produto não encontrado nesta UF.")
    with _cb:
        st.markdown("##### Similar")
        if not rb.empty:
            _r = rb.iloc[0]
            st.markdown(f"`{sku_b}` {_r['DESC_FINAL']}")
            st.caption(f"Marca: **{_r['MARCA']}** · Emb: **{_r['EMBALAGEM']}**")
            if ver_preco:
                st.caption(f"Preço: **R$ {float(_r['PRECO']):.2f}**")
        else:
            st.caption(f"Similar `{sku_b}` não encontrado nesta UF.")
    st.divider()
    _b1, _b2 = st.columns(2)
    with _b1:
        if st.button(f"🔍 Buscar {sku_b}", use_container_width=True, type="primary"):
            # Não altera dlg_cnt_key: evita trocar a chave do editor (o que sairia do fullscreen)
            # sim_sup_key já impede o diálogo de reabrir
            st.session_state["cat_busca_pending"] = sku_b
            st.rerun(scope="app")  # precisa rerun completo para processar cat_busca_pending
    with _b2:
        if st.button("✖ Fechar", use_container_width=True):
            # Apenas fecha — sim_sup_key impede reabertura; editor permanece em fullscreen
            st.rerun(scope="app")


def render(
    get_enriched_fn,
    get_product_opcoes_fn,
    get_states_indexed_fn,
    get_db_lookup_fn,
    read_all_states_fn,
    get_db_data_fn,
    check_db_fn,
    excel_source_key: str = "excel_source",
):
    """
    Renderiza o simulador de pedidos.
    Recebe as funções de cache do app principal para evitar re-importação.
    """
    u        = auth.usuario_atual()
    ver_preco = auth.tem_permissao("ver_precos")
    pode_ped  = auth.tem_permissao("fazer_pedidos")

    src    = st.session_state.get(excel_source_key, "")

    # Lê status do BD do session_state (populado na sidebar, sem nova chamada HTTP)
    db_ok = st.session_state.get("sidebar_db_ok", True)

    # Quando o catálogo está no Supabase, o Excel local não é necessário
    from db_supabase import catalogo_disponivel as _catalogo_disp
    _usar_supabase = _catalogo_disp()

    if not _usar_supabase and (not src or not Path(src).exists()):
        st.info("Nenhuma tabela carregada. Solicite ao administrador que importe o arquivo Excel.")
        return

    # No fast-path (Supabase), src pode ser None — passa string vazia para get_enriched
    if not src or not Path(src).exists():
        src = ""

    # ── Carrega similares cadastrados ────────────────────────────────────────
    _sim_pairs = get_similares()
    # Lookup bidirecional: sku -> {sku, label, desc}
    _sim_lookup: dict = {}
    for _p in _sim_pairs:
        _sim_lookup[_p["sku_a"]] = {"sku": _p["sku_b"], "label": _p.get("label_b", _p["sku_b"])}
        _sim_lookup[_p["sku_b"]] = {"sku": _p["sku_a"], "label": _p.get("label_a", _p["sku_a"])}

    # ── Seletores de UF e desconto ───────────────────────────────────────────
    _perfil_atual = st.session_state.get("auth_perfil", "")
    _uf_usuario   = st.session_state.get("auth_uf", "") or ""

    c1, c2 = st.columns([1, 1])
    with c1:
        if _perfil_atual == "vendedor" and _uf_usuario:
            # Vendedor vê apenas sua UF — selectbox fixo (sem escolha)
            uf = st.selectbox("**UF**", [_uf_usuario], key="cat_uf", disabled=True)
        else:
            uf = st.selectbox("**UF**", STATES, key="cat_uf")
    with c2:
        if ver_preco:
            pct = st.number_input(
                "**Desconto Global (%)**", 0.0, 100.0, 0.0, 0.5,
                format="%.2f", key="cat_pct"
            )
        else:
            pct = 0.0
            st.info("💡 Preços e descontos visíveis apenas para Supervisores e Administradores.")

    try:
        rich   = get_enriched_fn(src, uf)
    except Exception:
        st.warning("⚠️ Catálogo temporariamente indisponível. Clique em **Recarregar BD** na barra lateral ou aguarde e recarregue a página.")
        return
    factor = 1.0 - pct / 100.0

    # Aplica busca pendente ANTES do widget ser instanciado
    if "cat_busca_pending" in st.session_state:
        st.session_state["cat_busca"] = st.session_state.pop("cat_busca_pending")

    # ── Busca + Filtros ──────────────────────────────────────────────────────
    busca = st.text_input(
        "🔍 Buscar produto",
        placeholder="ex: latex branco 18l",
        key="cat_busca",
    )

    unique_grupos = sorted(g for g in rich["GRUPO"].unique() if g)
    _all_marcas   = sorted(m for m in rich["MARCA"].unique() if m)
    unique_marcas = [m for m in _all_marcas if not _MARCAS_VISIVEIS or m in _MARCAS_VISIVEIS]
    unique_embs   = sorted(e for e in rich["EMBALAGEM"].unique() if e)

    with st.expander("🔍 Filtros", expanded=False):
        f1, f2, f3 = st.columns(3)
        with f1:
            sel_grupos = st.multiselect("Linha / Grupo", unique_grupos, placeholder="Todos")
        with f2:
            sel_marcas = st.multiselect("Marca", unique_marcas, placeholder="Todas")
        with f3:
            sel_embs = st.multiselect("Embalagem", unique_embs, placeholder="Todas")

    mask = pd.Series(True, index=rich.index)
    if _MARCAS_VISIVEIS:
        mask &= rich["MARCA"].isin(_MARCAS_VISIVEIS)
    if sel_grupos:
        mask &= rich["GRUPO"].isin(sel_grupos)
    if sel_marcas:
        mask &= rich["MARCA"].isin(sel_marcas)
    if sel_embs:
        mask &= rich["EMBALAGEM"].isin(sel_embs)
    filtered = rich[mask]

    if busca.strip():
        words = busca.upper().split()
        combined = (
            filtered["COD_SKU"].astype(str) + " " +
            filtered["COD_CITEL"].astype(str) + " " +
            filtered["DESC_FINAL"].astype(str) + " " +
            filtered["MARCA"].astype(str) + " " +
            filtered["GRUPO"].astype(str) + " " +
            filtered["EMBALAGEM"].astype(str)
        ).str.upper()
        for w in words:
            filtered = filtered[combined.loc[filtered.index].str.contains(w, na=False)]

    qtd_key   = f"pedido_qtd_{uf}"
    zerar_key = f"zerar_cnt_{uf}"
    aplc_key  = f"aplc_cnt_{uf}"
    dlg_key   = f"dlg_cnt_{uf}"       # incrementado ao fechar diálogo → reseta ☑
    sim_sup_key = f"sim_sup_{uf}"     # SKU cujo diálogo já foi dispensado (X)
    _flt_cnt_key = f"_flt_cnt_{uf}"   # incrementado ao mudar filtro → reseta editor
    _flt_sig_key = f"_flt_sig_{uf}"   # assinatura do filtro atual
    if qtd_key      not in st.session_state: st.session_state[qtd_key]      = {}
    if zerar_key    not in st.session_state: st.session_state[zerar_key]    = 0
    if aplc_key     not in st.session_state: st.session_state[aplc_key]     = 0
    if dlg_key      not in st.session_state: st.session_state[dlg_key]      = 0
    if _flt_cnt_key not in st.session_state: st.session_state[_flt_cnt_key] = 0
    if _flt_sig_key not in st.session_state: st.session_state[_flt_sig_key] = None

    # Detecta mudança de filtro e incrementa contador para forçar reset do editor
    _flt_sig_cur = repr((sorted(sel_grupos), sorted(sel_marcas), sorted(sel_embs), busca.strip().upper()))
    if st.session_state[_flt_sig_key] != _flt_sig_cur:
        if st.session_state[_flt_sig_key] is not None:   # não é primeiro render
            # Salva QTDs do editor atual ANTES de resetar (captura edições não aplicadas)
            _old_editor_key = f"editor_{uf}_{st.session_state[zerar_key]}_{st.session_state[aplc_key]}_{st.session_state[dlg_key]}_{st.session_state[_flt_cnt_key]}"
            _prev_sku = st.session_state.get(f"_prev_sku_{uf}", [])
            for _ri_str, _rd in st.session_state.get(_old_editor_key, {}).get("edited_rows", {}).items():
                try:
                    _ri = int(_ri_str)
                    if _ri < len(_prev_sku) and "QTD" in _rd:
                        _v = max(0, int(float(str(_rd["QTD"])))) if _rd["QTD"] is not None else 0
                        _s = str(_prev_sku[_ri])
                        if _v > 0:
                            st.session_state[qtd_key][_s] = _v
                        else:
                            st.session_state[qtd_key].pop(_s, None)
                except (ValueError, TypeError):
                    pass
            st.session_state[_flt_cnt_key] += 1
        st.session_state[_flt_sig_key] = _flt_sig_cur

    # Dados expostos ao fragmento via session_state (evita serialização de DataFrames)
    st.session_state[f"_frag_filtered_{uf}"] = filtered
    st.session_state[f"_frag_rich_{uf}"]     = rich
    st.session_state[f"_sim_lookup_{uf}"]    = _sim_lookup

    _render_editor(
        uf=uf,
        qtd_key=qtd_key,
        zerar_key=zerar_key,
        aplc_key=aplc_key,
        dlg_key=dlg_key,
        sim_sup_key=sim_sup_key,
        flt_cnt_key=_flt_cnt_key,
        ver_preco=ver_preco,
        pct=pct,
        factor=factor,
        db_ok=db_ok,
    )

    # ── Preview do Pedido ─────────────────────────────────────────────────────
    qtd_map  = st.session_state[qtd_key]   # re-lê após possível atualização do fragmento
    _sel_skus = {sku: q for sku, q in qtd_map.items() if q > 0}
    if not _sel_skus:
        return

    _sel_rich = rich[rich["COD_SKU"].isin(_sel_skus)].copy()
    _sel_rich["QTD"] = _sel_rich["COD_SKU"].map(_sel_skus).astype(int)
    _f = 1.0 - pct / 100.0

    itens_pedido = []
    for _, row in _sel_rich.iterrows():
        pu = float(row["PRECO"]) * _f
        itens_pedido.append({
            "cod_sku":   str(row["COD_SKU"]),
            "cod_citel": str(row["COD_CITEL"]),
            "marca":     str(row["MARCA"]),
            "descricao": str(row["DESC_FINAL"]),
            "embalagem": str(row["EMBALAGEM"]),
            "qtd":       int(row["QTD"]),
            "preco_unit": round(pu, 2),
            "total":      round(pu * int(row["QTD"]), 2),
        })

    st.divider()
    st.markdown("#### 🛒 Preview do Pedido")

    prev_cols = ["cod_citel", "cod_sku", "marca", "descricao", "embalagem", "qtd"]
    prev_cfg  = {
        "cod_citel": st.column_config.TextColumn("COD CITEL", width=120, disabled=True),
        "cod_sku":   st.column_config.TextColumn("SKU", width=110, disabled=True),
        "marca":     st.column_config.TextColumn("Marca", width=120, disabled=True),
        "descricao": st.column_config.TextColumn("Descrição", disabled=True),
        "embalagem": st.column_config.TextColumn("Embalagem", width=160, disabled=True),
        "qtd":       st.column_config.NumberColumn("Qtd", width=70, min_value=0, step=1),
    }
    if ver_preco:
        prev_cols += ["preco_unit", "total"]
        prev_cfg["preco_unit"] = st.column_config.NumberColumn("Preço Unit.", format="R$ %.2f", width=130, disabled=True)
        prev_cfg["total"]      = st.column_config.NumberColumn("Total", format="R$ %.2f", width=130, disabled=True)

    _prev_ver_key = f"_prev_ver_{uf}"
    if _prev_ver_key not in st.session_state:
        st.session_state[_prev_ver_key] = 0

    _edited_prev = st.data_editor(
        pd.DataFrame(itens_pedido)[prev_cols],
        column_config=prev_cfg,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        key=f"prev_ed_{uf}_{st.session_state[aplc_key]}_{st.session_state[_prev_ver_key]}",
    )

    # Propaga edições de QTD e deleções → qtd_key (fonte de verdade)
    _orig_map     = {it["cod_sku"]: it for it in itens_pedido}
    _edited_skus  = set(_edited_prev["cod_sku"].dropna().astype(str).tolist())
    _qmap_new     = dict(qtd_map)
    _prev_changed = False

    # Deleções: SKUs que sumiram do editor
    for _sku in list(_orig_map.keys()):
        if _sku not in _edited_skus:
            _prev_changed = True
            _qmap_new[_sku] = 0

    # Edições de QTD nas linhas restantes
    for _, erow in _edited_prev.iterrows():
        _sku = str(erow.get("cod_sku", "")) if pd.notna(erow.get("cod_sku")) else ""
        if _sku not in _orig_map:
            continue  # ignora linhas em branco adicionadas acidentalmente
        _nq = int(erow["qtd"]) if pd.notna(erow.get("qtd")) else 0
        if _nq != _orig_map[_sku]["qtd"]:
            _prev_changed = True
        _qmap_new[_sku] = _nq

    # Reconstrói itens_pedido a partir do editor (sem zeros/deletados)
    itens_pedido = []
    for _, erow in _edited_prev.iterrows():
        _sku = str(erow.get("cod_sku", "")) if pd.notna(erow.get("cod_sku")) else ""
        if _sku not in _orig_map:
            continue
        _nq = int(erow["qtd"]) if pd.notna(erow.get("qtd")) else 0
        if _nq > 0:
            _orig = _orig_map[_sku]
            itens_pedido.append({**_orig, "qtd": _nq, "total": round(_orig["preco_unit"] * _nq, 2)})

    if _prev_changed:
        st.session_state[qtd_key]       = _qmap_new
        st.session_state[_prev_ver_key] += 1
        st.rerun()

    # Total sempre calculado (vendedor não vê na tela, mas é gravado no banco)
    total_geral = sum(it["total"] for it in itens_pedido)
    if ver_preco:
        tg_fmt = f"R$ {total_geral:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        st.markdown(
            f"<div style='text-align:right;font-size:1.1em'><b>Total geral: {tg_fmt}</b></div>",
            unsafe_allow_html=True,
        )

    # ── Botões de ação ────────────────────────────────────────────────────────
    if not pode_ped:
        st.warning("Você não tem permissão para enviar pedidos.")
        return

    st.divider()
    exige_aprov = get_config("pedido_aprovacao", "false") == "true"
    label_btn   = "📨 Enviar para Aprovação" if exige_aprov else "📨 Enviar Pedido"

    col_env, col_exp_suv, col_exp_sw, col_exp_all = st.columns(4)

    # Botão Enviar Pedido
    with col_env:
        if st.button(label_btn, type="primary", use_container_width=True):
            if not itens_pedido:
                st.error("❌ Adicione ao menos um item antes de enviar.")
                st.stop()
            pedido_data = {
                "usuario":     u.get("nome", u.get("usuario", "")),
                "loja":        u.get("loja", ""),
                "uf":          uf,
                "desconto_pct": pct,
                "total_geral":  total_geral,
            }
            ok, msg, pid, num_pedido = salvar_pedido(pedido_data, itens_pedido)
            if ok:
                if exige_aprov:
                    st.success(f"✅ {msg} — aguardando aprovação do supervisor.")
                else:
                    # Envia e-mail imediatamente
                    pedido_data["numero"] = num_pedido
                    ok_mail, msg_mail = enviar_email_pedido(
                        pedido_data, itens_pedido, mostrar_precos=True
                    )
                    atualizar_status_pedido(pid, "enviado", u.get("nome", u.get("usuario","")))
                    st.success(f"✅ {msg}")
                    if ok_mail:
                        st.info(f"📧 {msg_mail}")
                    else:
                        st.warning(f"⚠️ Pedido salvo, mas falha no e-mail: {msg_mail}")

                # Limpa quantidades
                st.session_state[qtd_key]   = {}
                st.session_state[zerar_key] += 1
                st.rerun()
            else:
                st.error(f"Erro ao salvar pedido: {msg}")

    # ── Similares dos itens selecionados ──────────────────────────────────────
    _sim_selecionados = [(sku, _sim_lookup[sku]) for sku in _sel_skus if sku in _sim_lookup]
    if _sim_selecionados:
        st.divider()
        st.markdown("#### 🔄 Similares disponíveis para itens do pedido")
        indexed = get_states_indexed_fn(src)
        for _sku_orig, _sim in _sim_selecionados:
            _sku_sim  = _sim["sku"]
            _row_orig = rich[rich["COD_SKU"] == _sku_orig]
            _row_sim  = rich[rich["COD_SKU"] == _sku_sim]
            _desc_orig = _row_orig["DESC_FINAL"].iloc[0] if not _row_orig.empty else _sku_orig
            _desc_sim  = _row_sim["DESC_FINAL"].iloc[0]  if not _row_sim.empty  else _sku_sim
            _marca_sim = _row_sim["MARCA"].iloc[0]        if not _row_sim.empty  else ""
            _emb_sim   = _row_sim["EMBALAGEM"].iloc[0]    if not _row_sim.empty  else ""

            with st.expander(
                f"🔄 **{_desc_orig}** → similar: **{_desc_sim}**"
                + (f" ({_marca_sim})" if _marca_sim else ""),
                expanded=True,
            ):
                # Preços por UF
                _rows_comp = []
                for _uf2 in STATES:
                    _idx = indexed.get(_uf2, pd.DataFrame())
                    _pa  = float(_idx.at[_sku_orig, "PRECO"]) if not _idx.empty and _sku_orig in _idx.index else None
                    _pb  = float(_idx.at[_sku_sim,  "PRECO"]) if not _idx.empty and _sku_sim  in _idx.index else None
                    _diff = round(_pa - _pb, 2) if _pa is not None and _pb is not None else None
                    _row_dict = {"UF": _uf2}
                    if ver_preco:
                        _pa_d  = round(_pa  * factor, 2) if _pa  is not None else None
                        _pb_d  = round(_pb  * factor, 2) if _pb  is not None else None
                        _diff_d = round(_pa_d - _pb_d, 2) if _pa_d is not None and _pb_d is not None else None
                        _row_dict.update({"Preço Original": _pa_d, "Preço Similar": _pb_d, "Diferença": _diff_d})
                    _rows_comp.append(_row_dict)

                _cfg_comp = {}
                if ver_preco:
                    _cfg_comp = {
                        "Preço Original": st.column_config.NumberColumn(format="R$ %.2f"),
                        "Preço Similar":  st.column_config.NumberColumn(format="R$ %.2f"),
                        "Diferença":      st.column_config.NumberColumn(format="R$ %.2f",
                                              help="Original − Similar (negativo = similar mais barato)"),
                    }
                st.caption(f"**SKU similar:** {_sku_sim} · {_emb_sim}")
                st.dataframe(
                    pd.DataFrame(_rows_comp),
                    column_config=_cfg_comp,
                    hide_index=True,
                    use_container_width=True,
                )

                # Botões de troca
                _btn_cols = st.columns([2, 2, 3])
                with _btn_cols[0]:
                    if st.button(
                        f"🔄 Trocar por similar",
                        key=f"swap_{_sku_orig}_{_sku_sim}",
                        type="primary",
                        use_container_width=True,
                        help=f"Remove {_sku_orig} e adiciona {_sku_sim} com a mesma quantidade",
                    ):
                        _qtd_orig = qtd_map.get(_sku_orig, 1)
                        new_map = {k: v for k, v in qtd_map.items() if k != _sku_orig}
                        new_map[_sku_sim] = _qtd_orig
                        st.session_state[qtd_key] = new_map
                        st.rerun()
                with _btn_cols[1]:
                    if st.button(
                        f"➕ Adicionar similar",
                        key=f"add_sim_{_sku_orig}_{_sku_sim}",
                        use_container_width=True,
                        help=f"Mantém {_sku_orig} e também adiciona {_sku_sim} com qtd 1",
                    ):
                        _qtd_orig = qtd_map.get(_sku_orig, 1)
                        new_map = dict(qtd_map)
                        new_map[_sku_sim] = new_map.get(_sku_sim, 0) + _qtd_orig
                        st.session_state[qtd_key] = new_map
                        st.rerun()

    # Exportar Excel Suvinil
    with col_exp_suv:
        itens_suv = [it for it in itens_pedido if _classifica_marca(it.get("marca","")) == "suvinil"]
        if itens_suv:
            xls_suv = exportar_excel_suvinil(itens_pedido)
            data_str = datetime.now().strftime("%d-%m-%Y")
            st.download_button(
                f"📥 Excel Suvinil ({len(itens_suv)})",
                data=xls_suv,
                file_name=f"Pedido_Suvinil_{u.get('loja',uf)}_{data_str}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        else:
            st.button("📥 Excel Suvinil", disabled=True, use_container_width=True,
                      help="Nenhum item Suvinil/Glasurit no pedido")

    # Exportar Excel SW
    with col_exp_sw:
        itens_sw_list = [it for it in itens_pedido if _classifica_marca(it.get("marca","")) == "sw"]
        if itens_sw_list:
            xls_sw  = exportar_excel_sw(itens_pedido)
            data_str = datetime.now().strftime("%d-%m-%Y")
            st.download_button(
                f"📥 Excel SW ({len(itens_sw_list)})",
                data=xls_sw,
                file_name=f"Pedido_SW_{u.get('loja',uf)}_{data_str}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        else:
            st.button("📥 Excel SW", disabled=True, use_container_width=True,
                      help="Nenhum item Sherwin-Williams no pedido")

    # Exportar tudo
    with col_exp_all:
        xls_all  = exportar_excel_completo(itens_pedido, mostrar_precos=ver_preco)
        data_str = datetime.now().strftime("%d-%m-%Y")
        st.download_button(
            f"📥 Excel Completo ({len(itens_pedido)})",
            data=xls_all,
            file_name=f"Pedido_Completo_{u.get('loja',uf)}_{data_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
