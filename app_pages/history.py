"""
Histórico de Pedidos — Toque de Cor Web

Vendedor  → vê apenas os próprios pedidos
Supervisor → vê pedidos da sua loja; pode aprovar pendentes
Admin     → vê todos os pedidos; pode aprovar e reenviar e-mail
"""

import base64
import pandas as pd
import streamlit as st
import streamlit.components.v1 as _components
from datetime import datetime, timezone, timedelta

import auth

_BR_TZ = timezone(timedelta(hours=-3))  # Horaário de Brasília (UTC-3)
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
    exportar_excel_outros,
    exportar_excel_completo,
    exportar_excel_citel,
    enviar_email_pedido,
    _classifica_marca,
)

def _btn_multi_download(label: str, arquivos: list[tuple[bytes, str]], key: str) -> None:
    """
    Botão que dispara o download de múltiplos arquivos .xlsx via JavaScript,
    sem ZIP e sem botões extras — um clique, todos os arquivos separados.
    """
    trigger = f"_dl_trig_{key}"
    if st.button(label, use_container_width=True, key=f"btn_{key}"):
        st.session_state[trigger] = True
    if st.session_state.pop(trigger, False):
        partes = []
        for conteudo, nome in arquivos:
            b64 = base64.b64encode(conteudo).decode()
            partes.append(f'["{nome}","{b64}"]')
        js = (
            "<script>[" + ",".join(partes) + "].forEach(function(f,i){"
            "setTimeout(function(){"
            "var a=document.createElement('a');"
            "a.href='data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,'+f[1];"
            "a.download=f[0];"
            "document.body.appendChild(a);a.click();document.body.removeChild(a);"
            "},i*600);});"  # 600ms de intervalo para o browser não bloquear
            "</script>"
        )
        _components.html(js, height=0)


STATUS_LABEL = {
    "pendente": "🟡 Pendente",
    "enviado":  "🟢 Enviado",
    "aprovado": "✅ Aprovado",
    "cancelado":"🔴 Cancelado",
}


def render():
    u       = auth.usuario_atual()
    perfil  = u.get("perfil", "vendedor")
    usuario = u.get("nome", u.get("usuario", ""))
    loja    = u.get("loja", "")

    ver_preco   = auth.tem_permissao("ver_precos")
    pode_aprov  = auth.tem_permissao("aprovar_pedidos")
    from db_supabase import get_all_configs as _all_cfg
    exige_aprov = _all_cfg().get("pedido_aprovacao", "false") == "true"

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
            # Pedidos antigos de vendedor podem ter total_geral=0 → recalcula dos itens
            if tg == 0 and itens:
                tg = sum(float(it.get("preco_unit", 0)) * int(it.get("qtd", 0)) for it in itens)
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
            try:
                _df_cat = get_catalogo_uf(_nova_uf)
            except Exception:
                _df_cat = pd.DataFrame()
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
        data_str     = datetime.now(_BR_TZ).strftime("%d-%m-%Y")

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

        # Reenviar e-mail (somente após aprovado/enviado)
        if auth.tem_permissao("reenviar_pedido") and status_atual in ("aprovado", "enviado"):
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

        # Downloads Excel — 2 botões, cada um dispara múltiplos arquivos separados por marca
        _itens_suv    = [it for it in itens if _classifica_marca(it.get("marca", "")) == "suvinil"]
        _itens_sw     = [it for it in itens if _classifica_marca(it.get("marca", "")) == "sw"]
        _itens_outros = [it for it in itens if _classifica_marca(it.get("marca", "")) == "outros"]
        _data_str  = datetime.now(_BR_TZ).strftime("%d-%m-%Y")
        _loja_str  = ped.get("loja", "")
        _uf_str    = ped.get("uf", "")

        _dl1, _dl2 = st.columns(2)
        with _dl1:
            _arqs_excel = []
            if _itens_suv:
                _arqs_excel.append((
                    exportar_excel_suvinil(itens, pedido=ped),
                    f"Pedido_Suvinil_{_uf_str}_{_loja_str}_{_data_str}.xlsx",
                ))
            if _itens_sw:
                _arqs_excel.append((
                    exportar_excel_sw(itens, pedido=ped),
                    f"Pedido_SW_{_uf_str}_{_loja_str}_{_data_str}.xlsx",
                ))
            if _itens_outros:
                _arqs_excel.append((
                    exportar_excel_outros(itens, pedido=ped),
                    f"Pedido_Outros_{_uf_str}_{_loja_str}_{_data_str}.xlsx",
                ))
            if _arqs_excel:
                _btn_multi_download(
                    f"📥 Excel Completo ({len(itens)})",
                    _arqs_excel,
                    key=f"excel_{pid}",
                )
            else:
                st.button("📥 Excel Completo", disabled=True, use_container_width=True,
                          key=f"excel_{pid}")

        with _dl2:
            _arqs_citel = []
            if _itens_suv:
                _arqs_citel.append((
                    exportar_excel_citel(_itens_suv),
                    f"Importacao_Citel_Suvinil_{_uf_str}_{_loja_str}_{_data_str}.xlsx",
                ))
            if _itens_sw:
                _arqs_citel.append((
                    exportar_excel_citel(_itens_sw),
                    f"Importacao_Citel_SW_{_uf_str}_{_loja_str}_{_data_str}.xlsx",
                ))
            if _itens_outros:
                _arqs_citel.append((
                    exportar_excel_citel(_itens_outros),
                    f"Importacao_Citel_Outros_{_uf_str}_{_loja_str}_{_data_str}.xlsx",
                ))
            if _arqs_citel:
                _btn_multi_download(
                    f"📥 Excel CITEL ({len(itens)})",
                    _arqs_citel,
                    key=f"citel_{pid}",
                )
            else:
                st.button("📥 Excel CITEL", disabled=True, use_container_width=True,
                          key=f"citel_{pid}")

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

        fd1, fd2 = st.columns(2)
        with fd1:
            sel_data_ini = st.date_input("Data inicial", value=None, key="hist_data_ini")
        with fd2:
            sel_data_fim = st.date_input("Data final", value=None, key="hist_data_fim")

    filtrados = pedidos
    if sel_loja_f   != "Todas": filtrados = [p for p in filtrados if p.get("loja","")   == sel_loja_f]
    if sel_status_f != "Todos": filtrados = [p for p in filtrados if p.get("status","") == sel_status_f]
    if sel_uf_f     != "Todas": filtrados = [p for p in filtrados if p.get("uf","")     == sel_uf_f]
    if sel_data_ini:
        filtrados = [p for p in filtrados if _parse_date(p.get("criado_em","")) >= sel_data_ini]
    if sel_data_fim:
        filtrados = [p for p in filtrados if _parse_date(p.get("criado_em","")) <= sel_data_fim]

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
                if tg > 0:
                    tg_txt = f" · R$ {tg:,.2f}".replace(",","X").replace(".",",").replace("X",".")
                else:
                    tg_txt = " · —"
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
    """Formata data ISO (UTC) para dd/mm/yyyy HH:MM no fuso de Brasília."""
    try:
        if "T" in iso:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            if dt.tzinfo:
                dt = dt.astimezone(_BR_TZ)
        else:
            dt = datetime.fromisoformat(iso)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(iso)[:16]


def _parse_date(iso: str):
    """Retorna objeto date a partir de string ISO no fuso BR, ou date.min em caso de erro."""
    from datetime import date
    try:
        if "T" in iso:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            if dt.tzinfo:
                dt = dt.astimezone(_BR_TZ)
            return dt.date()
        return datetime.fromisoformat(iso).date()
    except Exception:
        return date.min
