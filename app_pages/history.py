"""
Histórico de Pedidos — Toque de Cor Web

Vendedor  → vê apenas os próprios pedidos
Supervisor → vê pedidos da sua loja; pode aprovar pendentes
Admin     → vê todos os pedidos; pode aprovar e reenviar e-mail
"""

import pandas as pd
import streamlit as st
from datetime import datetime

import auth
from db_supabase import (
    listar_pedidos,
    buscar_pedido_completo,
    atualizar_status_pedido,
    atualizar_pedido,
    excluir_pedido,
    get_config,
    get_catalogo_uf,
)
from orders import (
    exportar_excel_suvinil,
    exportar_excel_sw,
    exportar_excel_completo,
    exportar_excel_citel,
    enviar_email_pedido,
)

STATUS_LABEL = {
    "pendente": "🟡 Pendente",
    "enviado":  "🟢 Enviado",
    "aprovado": "✅ Aprovado",
    "cancelado":"🔴 Cancelado",
}


def render():
    u       = auth.usuario_atual()
    perfil  = u.get("perfil", "vendedor")
    usuario = u.get("usuario", "")
    loja    = u.get("loja", "")

    ver_preco   = auth.tem_permissao("ver_precos")
    pode_aprov  = auth.tem_permissao("aprovar_pedidos")
    exige_aprov = get_config("pedido_aprovacao", "false") == "true"

    st.markdown("## 📦 Histórico de Pedidos")

    # ── Detalhe de pedido selecionado ────────────────────────────────────────
    if "hist_pedido_id" in st.session_state:
        pid  = st.session_state["hist_pedido_id"]
        ped  = buscar_pedido_completo(pid)

        if not ped:
            st.error("Pedido não encontrado.")
            del st.session_state["hist_pedido_id"]
            st.rerun()

        st.button(
            "← Voltar à lista",
            on_click=lambda: st.session_state.pop("hist_pedido_id", None),
        )

        num  = ped.get("numero", 0)
        itens = ped.get("itens", [])

        st.markdown(f"### Pedido #{num:04d} — {ped.get('uf','')} | {ped.get('loja','')}")

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Operador", ped.get("usuario", ""))
        m2.metric("UF", ped.get("uf", ""))
        m3.metric("Status", STATUS_LABEL.get(ped.get("status",""), ped.get("status","")))
        m4.metric("Data", _fmt_data(ped.get("criado_em", "")))
        if ver_preco:
            tg = float(ped.get("total_geral", 0))
            tg_fmt = f"R$ {tg:,.2f}".replace(",","X").replace(".",",").replace("X",".")
            m5.metric("Total", tg_fmt)

        st.divider()

        # ── Modo edição / visualização ────────────────────────────────────────
        _STATES_ED  = ["RN", "BA", "PE", "AL", "PB"]
        _pode_editar = (
            perfil == "admin"
            or perfil == "supervisor"
            or (perfil == "vendedor" and ped.get("usuario", "") == usuario)
        ) and ped.get("status", "") in ("pendente", "enviado")
        _edit_mode = st.session_state.get(f"edit_ped_{pid}", False)

        if _edit_mode:
            st.markdown("#### ✏️ Editando Pedido")
            _ec1, _ec2 = st.columns(2)
            _uf_atual  = ped.get("uf", _STATES_ED[0])
            _nova_uf   = _ec1.selectbox(
                "UF",
                _STATES_ED,
                index=_STATES_ED.index(_uf_atual) if _uf_atual in _STATES_ED else 0,
                key=f"edit_uf_{pid}",
            )
            _novo_desc = _ec2.number_input(
                "Desconto (%)", min_value=0.0, max_value=100.0,
                value=float(ped.get("desconto_pct", 0)),
                step=0.5, format="%.2f",
                key=f"edit_desc_{pid}",
            )

            # Carrega preços da UF selecionada (já está em cache — sem HTTP extra)
            _df_cat   = get_catalogo_uf(_nova_uf)
            _preco_map = {}
            if _df_cat is not None and not _df_cat.empty:
                _preco_map = dict(zip(
                    _df_cat["COD_SKU"].astype(str),
                    _df_cat["PRECO"].astype(float),
                ))

            st.caption("Quantidades (0 = remover item do pedido)")
            _hdr = st.columns([4, 2, 1, 2])
            _hdr[0].markdown("**Descrição / Embalagem**")
            _hdr[1].markdown("**SKU**")
            _hdr[2].markdown("**Qtd**")
            if ver_preco:
                _hdr[3].markdown("**Total c/ desc.**")

            _novos_itens_edit: list[dict] = []
            _novo_total = 0.0
            for _it in itens:
                _sku        = str(_it.get("cod_sku", ""))
                _preco_base = _preco_map.get(_sku, float(_it.get("preco_unit", 0)))
                _preco_fin  = round(_preco_base * (1 - _novo_desc / 100), 4)
                _row = st.columns([4, 2, 1, 2])
                _row[0].caption(f"{_it.get('descricao','')}  ·  {_it.get('embalagem','')}")
                _row[1].caption(_sku)
                _qtd_nova = _row[2].number_input(
                    "", min_value=0, value=int(_it.get("qtd", 0)), step=1,
                    key=f"edit_qtd_{pid}_{_sku}",
                    label_visibility="collapsed",
                )
                if ver_preco:
                    _tot_item = round(_qtd_nova * _preco_fin, 2)
                    _row[3].caption(
                        f"R$ {_tot_item:,.2f}".replace(",","X").replace(".",",").replace("X",".")
                    )
                if _qtd_nova > 0:
                    _tot_item = round(_qtd_nova * _preco_fin, 2)
                    _novo_total += _tot_item
                    _novos_itens_edit.append({
                        "cod_sku":   _sku,
                        "cod_citel": _it.get("cod_citel", ""),
                        "marca":     _it.get("marca", ""),
                        "descricao": _it.get("descricao", ""),
                        "embalagem": _it.get("embalagem", ""),
                        "qtd":       _qtd_nova,
                        "preco_unit": _preco_fin,
                        "total":     _tot_item,
                    })

            if ver_preco:
                _tg_fmt = f"R$ {_novo_total:,.2f}".replace(",","X").replace(".",",").replace("X",".")
                st.metric("Novo Total", _tg_fmt)

            st.divider()
            _sc1, _sc2 = st.columns(2)
            with _sc1:
                if st.button("💾 Salvar alterações", type="primary",
                             use_container_width=True, key=f"save_edit_{pid}"):
                    _ok, _msg = atualizar_pedido(
                        pid, _nova_uf, _novo_desc, _novo_total,
                        _novos_itens_edit, usuario,
                    )
                    if _ok:
                        st.success(_msg)
                        st.session_state.pop(f"edit_ped_{pid}", None)
                        st.rerun()
                    else:
                        st.error(_msg)
            with _sc2:
                if st.button("✗ Cancelar edição", use_container_width=True,
                             key=f"cancel_edit_{pid}"):
                    st.session_state.pop(f"edit_ped_{pid}", None)
                    st.rerun()

        else:
            # ── Tabela de itens (modo visualização) ───────────────────────────
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

            if itens:
                st.dataframe(
                    pd.DataFrame(itens)[prev_cols],
                    column_config=prev_cfg,
                    hide_index=True,
                    use_container_width=True,
                )

        st.divider()

        # ── Ações no pedido ───────────────────────────────────────────────────
        status_atual = ped.get("status", "pendente")
        data_str     = datetime.now().strftime("%d-%m-%Y")

        btn_cols = st.columns(5)

        # Aprovar
        if pode_aprov and status_atual == "pendente" and exige_aprov:
            with btn_cols[0]:
                if st.button("✅ Aprovar e Enviar", type="primary", use_container_width=True):
                    ok_mail, msg_mail = enviar_email_pedido(
                        ped, itens, mostrar_precos=True
                    )
                    atualizar_status_pedido(pid, "aprovado", usuario)
                    st.success("Pedido aprovado.")
                    if ok_mail:
                        st.info(f"📧 {msg_mail}")
                    else:
                        st.warning(f"E-mail: {msg_mail}")
                    st.rerun()

        # Reenviar e-mail (admin)
        if perfil == "admin":
            with btn_cols[1]:
                if st.button("📧 Reenviar E-mail", use_container_width=True):
                    ok_mail, msg_mail = enviar_email_pedido(
                        ped, itens, mostrar_precos=True
                    )
                    st.info(msg_mail if ok_mail else f"⚠️ {msg_mail}")

        # Cancelar
        if perfil in ("admin", "supervisor") and status_atual not in ("cancelado",):
            with btn_cols[2]:
                if st.button("🔴 Cancelar Pedido", use_container_width=True):
                    atualizar_status_pedido(pid, "cancelado", usuario)
                    st.warning("Pedido cancelado.")
                    st.rerun()

        # Excluir (admin)
        if perfil == "admin":
            with btn_cols[3]:
                if st.button("🗑️ Excluir", use_container_width=True):
                    excluir_pedido(pid, usuario)
                    st.session_state.pop("hist_pedido_id", None)
                    st.toast(f"Pedido #{num:04d} excluído.", icon="🗑️")
                    st.rerun()

        # Editar pedido
        if not _edit_mode and _pode_editar:
            with btn_cols[4]:
                if st.button("✏️ Editar", use_container_width=True, key=f"btn_edit_{pid}"):
                    st.session_state[f"edit_ped_{pid}"] = True
                    st.rerun()

        # Downloads Excel
        _dl1, _dl2 = st.columns(2)
        with _dl1:
            xls_all = exportar_excel_completo(itens, mostrar_precos=ver_preco, pedido=ped)
            st.download_button(
                "📥 Excel Completo",
                data=xls_all,
                file_name=f"Pedido_{num:04d}_{ped.get('loja','')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        with _dl2:
            xls_citel = exportar_excel_citel(itens)
            st.download_button(
                "📥 Excel CITEL",
                data=xls_citel,
                file_name=f"Pedido_{num:04d}_CITEL_{ped.get('loja','')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

        return  # não renderiza lista enquanto detalhe está aberto

    # ── Lista de pedidos ─────────────────────────────────────────────────────
    pedidos = listar_pedidos(usuario=usuario, loja=loja, perfil=perfil)

    if not pedidos:
        st.info("Nenhum pedido encontrado.")
        return

    # Filtros da lista
    with st.expander("🔍 Filtros", expanded=False):
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            lojas_disp  = sorted({p.get("loja","") for p in pedidos if p.get("loja","")})
            sel_loja_f  = st.selectbox("Loja", ["Todas"] + lojas_disp)
        with fc2:
            status_disp = sorted({p.get("status","") for p in pedidos})
            sel_status_f = st.selectbox("Status", ["Todos"] + status_disp)
        with fc3:
            ufs_disp    = sorted({p.get("uf","") for p in pedidos})
            sel_uf_f    = st.selectbox("UF", ["Todas"] + ufs_disp)

    filtrados = pedidos
    if sel_loja_f   != "Todas": filtrados = [p for p in filtrados if p.get("loja","")   == sel_loja_f]
    if sel_status_f != "Todos": filtrados = [p for p in filtrados if p.get("status","") == sel_status_f]
    if sel_uf_f     != "Todas": filtrados = [p for p in filtrados if p.get("uf","")     == sel_uf_f]

    st.caption(f"**{len(filtrados)}** pedido(s) encontrado(s)")

    for ped in filtrados:
        pid   = ped["id"]
        num   = ped.get("numero", 0)
        st_lb = STATUS_LABEL.get(ped.get("status",""), ped.get("status",""))
        data  = _fmt_data(ped.get("criado_em", ""))
        loja_ = ped.get("loja", "")
        uf_   = ped.get("uf", "")
        usr_  = ped.get("usuario", "")

        ci, cb = st.columns([7, 1])
        with ci:
            tg_txt = ""
            if ver_preco:
                tg = float(ped.get("total_geral", 0))
                tg_txt = f" · R$ {tg:,.2f}".replace(",","X").replace(".",",").replace("X",".")
            st.markdown(
                f"**#{num:04d}** &nbsp;{st_lb}&nbsp; · {data} · **{uf_}** · {loja_} · _{usr_}_{tg_txt}"
            )
        with cb:
            st.button(
                "👁️ Ver",
                key=f"ver_ped_{pid}",
                use_container_width=True,
                on_click=lambda _id=pid: st.session_state.update(hist_pedido_id=_id),
            )


def _fmt_data(iso: str) -> str:
    """Formata data ISO para dd/mm/yyyy HH:MM."""
    try:
        if "T" in iso:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(iso)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(iso)[:16]
