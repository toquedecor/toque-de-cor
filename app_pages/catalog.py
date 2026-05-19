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
)

STATES = ["RN", "BA", "PE", "AL", "PB"]


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
            st.rerun()
    with _b2:
        if st.button("✖ Fechar", use_container_width=True):
            # Apenas fecha — sim_sup_key impede reabertura; editor permanece em fullscreen
            st.rerun()


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

    if not src or not Path(src).exists():
        st.info("Nenhuma tabela carregada. Solicite ao administrador que importe o arquivo Excel.")
        return

    # ── Carrega similares cadastrados ────────────────────────────────────────
    import json as _json
    _sim_file = Path(__file__).parent.parent / "similares.json"
    _sim_pairs = _json.loads(_sim_file.read_text("utf-8")) if _sim_file.exists() else []
    # Lookup bidirecional: sku -> {sku, label, desc}
    _sim_lookup: dict = {}
    for _p in _sim_pairs:
        _sim_lookup[_p["sku_a"]] = {"sku": _p["sku_b"], "label": _p.get("label_b", _p["sku_b"])}
        _sim_lookup[_p["sku_b"]] = {"sku": _p["sku_a"], "label": _p.get("label_a", _p["sku_a"])}

    # ── Seletores de UF e desconto ───────────────────────────────────────────
    c1, c2 = st.columns([1, 1])
    with c1:
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

    rich   = get_enriched_fn(src, uf)
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
    unique_marcas = sorted(m for m in rich["MARCA"].unique() if m)
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
    if qtd_key   not in st.session_state: st.session_state[qtd_key]   = {}
    if zerar_key not in st.session_state: st.session_state[zerar_key] = 0
    if aplc_key  not in st.session_state: st.session_state[aplc_key]  = 0
    if dlg_key   not in st.session_state: st.session_state[dlg_key]   = 0

    qtd_map   = st.session_state[qtd_key]   # quantidades APLICADAS (para preview)
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

    # Chave inclui aplc_key e dlg_key: resetam o editor quando Aplicar/Fechar são clicados
    edited = st.data_editor(
        _df_edit,
        key=f"editor_{uf}_{st.session_state[zerar_key]}_{st.session_state[aplc_key]}_{st.session_state[dlg_key]}",
        column_config=_col_cfg,
        hide_index=True,
        use_container_width=True,
    )

    # ── Diálogo de similar (abre sobre a tabela, funciona em tela cheia) ───────
    _marcadas = edited[edited["ver_sim"] == True]
    if not _marcadas.empty:
        _idx_marcado = _marcadas.index[0]
        if _idx_marcado < len(sku_array):
            _sku_marcado = str(sku_array[_idx_marcado])
            if _sku_marcado in _sim_lookup:
                _sku_sim = _sim_lookup[_sku_marcado]["sku"]
                # Supressão: evita reabrir o diálogo quando o usuário fecha com X
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
        # Checkbox desmarcado → limpa supressão para permitir reabrir
        st.session_state.pop(sim_sup_key, None)

    # ── Botões Aplicar / Zerar ────────────────────────────────────────────────
    _col_ap, _col_zer = st.columns(2)
    with _col_ap:
        if st.button("✅ Aplicar Quantidades", type="primary",
                     use_container_width=True, key=f"btn_ap_{uf}"):
            _new_qtd: dict = {}
            for i in range(len(sku_array)):
                try:
                    _v = edited.iloc[i]["QTD"]
                    _v = max(0, int(float(str(_v)))) if _v is not None else 0
                except (ValueError, TypeError):
                    _v = 0
                if _v > 0:
                    _new_qtd[str(sku_array[i])] = _v
            st.session_state[qtd_key] = _new_qtd
            st.session_state[aplc_key] += 1   # força editor a refletir valores salvos
            st.rerun()
    with _col_zer:
        if st.button("🗑️ Zerar Quantidades", use_container_width=True,
                     key=f"btn_zer_{uf}"):
            st.session_state[qtd_key]   = {}
            st.session_state[zerar_key] += 1
            st.rerun()

    # ── Rodapé: contagem ──────────────────────────────────────────────────────
    _sel_skus = {sku: q for sku, q in qtd_map.items() if q > 0}
    n_total   = len(rich)
    n_display = len(_df_edit)
    filtrado  = f" (filtrado de {n_total})" if n_display < n_total else ""
    st.caption(
        f"**{n_display}** itens{filtrado} — UF: **{uf}**"
        + (f" | **{len(_sel_skus)}** selecionado(s)" if _sel_skus else "")
    )


    # ── Preview do Pedido ─────────────────────────────────────────────────────
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
        "cod_citel": st.column_config.TextColumn("COD CITEL", width=120),
        "cod_sku":   st.column_config.TextColumn("SKU", width=110),
        "marca":     st.column_config.TextColumn("Marca", width=120),
        "descricao": st.column_config.TextColumn("Descrição"),
        "embalagem": st.column_config.TextColumn("Embalagem", width=160),
        "qtd":       st.column_config.NumberColumn("Qtd", width=70),
    }
    if ver_preco:
        prev_cols += ["preco_unit", "total"]
        prev_cfg["preco_unit"] = st.column_config.NumberColumn("Preço Unit.", format="R$ %.2f", width=130)
        prev_cfg["total"]      = st.column_config.NumberColumn("Total", format="R$ %.2f", width=130)

    st.dataframe(
        pd.DataFrame(itens_pedido)[prev_cols],
        column_config=prev_cfg,
        hide_index=True,
        use_container_width=True,
    )

    if ver_preco:
        total_geral = sum(it["total"] for it in itens_pedido)
        tg_fmt = f"R$ {total_geral:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        st.markdown(
            f"<div style='text-align:right;font-size:1.1em'><b>Total geral: {tg_fmt}</b></div>",
            unsafe_allow_html=True,
        )
    else:
        total_geral = 0.0

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

    st.divider()

    # ── Botões de ação ────────────────────────────────────────────────────────
    if not pode_ped:
        st.warning("Você não tem permissão para enviar pedidos.")
        return

    col_env, col_exp_suv, col_exp_sw, col_exp_all = st.columns(4)

    # Botão Enviar Pedido
    with col_env:
        exige_aprov = get_config("pedido_aprovacao", "false") == "true"
        label_btn   = "📨 Enviar para Aprovação" if exige_aprov else "📨 Enviar Pedido"

        if st.button(label_btn, type="primary", use_container_width=True):
            pedido_data = {
                "usuario":     u.get("usuario", ""),
                "loja":        u.get("loja", ""),
                "uf":          uf,
                "desconto_pct": pct,
                "total_geral":  total_geral,
            }
            ok, msg, pid = salvar_pedido(pedido_data, itens_pedido)
            if ok:
                if exige_aprov:
                    st.success(f"✅ {msg} — aguardando aprovação do supervisor.")
                else:
                    # Envia e-mail imediatamente
                    pedido_data["numero"] = pid
                    ok_mail, msg_mail = enviar_email_pedido(
                        pedido_data, itens_pedido, mostrar_precos=True
                    )
                    atualizar_status_pedido(pid, "enviado", u.get("usuario",""))
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
