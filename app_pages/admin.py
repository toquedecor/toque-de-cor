"""
Painel Administrativo — Toque de Cor Web
Acesso restrito a perfil 'admin'.

Abas:
  1. Usuários   — criar / editar / ativar / desativar
  2. Configurações — SMTP, destinatários, aprovação de pedidos
  3. Importar Planilha — upload do Excel e invalidação de cache
  4. Auditoria  — log de ações do sistema
"""

import pandas as pd
import streamlit as st
from pathlib import Path

import auth
from db_supabase import (
    get_all_configs, get_config, set_config,
    get_permissoes_perfil, set_permissoes_perfil,
    listar_auditoria, registrar_auditoria,
    get_supabase, supabase_ok,
)


def render(excel_source_key: str = "excel_source", clear_caches_fn=None):
    """
    Renderiza o painel admin dentro da página principal.
    excel_source_key: chave do st.session_state onde o caminho do Excel é salvo.
    clear_caches_fn: função a chamar após importar nova planilha.
    """
    u = auth.usuario_atual()

    if not auth.tem_permissao("gerenciar_usuarios"):
        st.error("⛔ Acesso restrito a administradores.")
        return

    # Uma única requisição busca todas as configs
    _cfg = get_all_configs()

    st.markdown("## ⚙️ Painel Administrativo")

    t_usr, t_perfis, t_cfg, t_imp, t_aud, t_diag = st.tabs(
        ["👥 Usuários", "🔐 Perfis & Permissões", "📧 Configurações", "📥 Importar Planilha", "📋 Auditoria", "🔌 Diagnóstico"]
    )

    # ══════════════════════════════════════════════════════════════════════════
    # ABA 1 — USUÁRIOS
    # ══════════════════════════════════════════════════════════════════════════
    with t_usr:
        st.markdown("### Usuários Cadastrados")

        usuarios = auth.listar_usuarios()
        if usuarios:
            df_usr = pd.DataFrame(usuarios)[["usuario", "nome", "perfil", "loja", "ativo"]]
            df_usr.columns = ["Login", "Nome", "Perfil", "Loja", "Ativo"]
            st.dataframe(df_usr, hide_index=True, use_container_width=True)
        else:
            st.info("Nenhum usuário cadastrado ainda.")

        st.divider()
        st.markdown("### Novo Usuário")

        with st.form("form_novo_usuario"):
            c1, c2 = st.columns(2)
            with c1:
                novo_login = st.text_input("Login", placeholder="fulano.silva")
                novo_nome  = st.text_input("Nome completo")
                novo_loja  = st.text_input("Loja")
            with c2:
                novo_perfil = st.selectbox(
                    "Perfil",
                    options=list(auth.PERFIS.keys()),
                    format_func=lambda x: auth.PERFIS[x],
                )
                novo_senha  = st.text_input("Senha", type="password")
                novo_senha2 = st.text_input("Confirmar senha", type="password")

            if st.form_submit_button("➕ Criar Usuário", type="primary"):
                if not novo_login or not novo_nome or not novo_senha:
                    st.error("Preencha todos os campos obrigatórios.")
                elif novo_senha != novo_senha2:
                    st.error("As senhas não coincidem.")
                else:
                    ok, msg = auth.criar_usuario(novo_login, novo_nome, novo_senha, novo_perfil, novo_loja)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

        st.divider()
        st.markdown("### Alterar Senha / Ativar / Desativar / Excluir")

        logins = [u_["usuario"] for u_ in usuarios] if usuarios else []
        if logins:
            with st.form("form_edit_usuario"):
                sel_login = st.selectbox("Selecionar usuário", logins)
                col_a, col_b = st.columns(2)
                with col_a:
                    nova_senha_ed  = st.text_input("Nova senha (deixe em branco para manter)", type="password")
                    nova_senha_ed2 = st.text_input("Confirmar nova senha", type="password")
                with col_b:
                    ativar   = st.checkbox("Usuário ativo", value=True)
                    excluir  = st.checkbox("⚠️ Excluir este usuário permanentemente")

                if st.form_submit_button("💾 Salvar alterações"):
                    if excluir:
                        ok, msg = auth.excluir_usuario(sel_login)
                    else:
                        ok, msg = auth.toggle_usuario(sel_login, ativar)
                        if ok and nova_senha_ed:
                            if nova_senha_ed != nova_senha_ed2:
                                st.error("As senhas não coincidem.")
                                st.stop()
                            ok, msg = auth.alterar_senha(sel_login, nova_senha_ed)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

    # ══════════════════════════════════════════════════════════════════════════
    # ABA 2 — PERFIS & PERMISSÕES
    # ══════════════════════════════════════════════════════════════════════════
    with t_perfis:
        st.markdown("### Permissões por Perfil")
        st.caption(
            "Marque quais ações cada perfil pode realizar. "
            "As alterações entram em vigor no próximo login do usuário."
        )

        _todos_perms = auth.TODAS_PERMISSOES  # {chave: rótulo}
        _perfis_edit = list(auth.PERFIS.keys())

        # Carrega permissões atuais de cada perfil (1 HTTP por perfil, mas só ao abrir a aba)
        _perms_atuais = {
            p: (
                get_permissoes_perfil(p)
                or auth._PERMISSOES_PADRAO.get(p, set())
            )
            for p in _perfis_edit
        }

        with st.form("form_perfis_permissoes"):
            # Cabeçalho da grade
            _header = st.columns([2] + [1] * len(_perfis_edit))
            _header[0].markdown("**Permissão**")
            for _i, _p in enumerate(_perfis_edit):
                _header[_i + 1].markdown(f"**{auth.PERFIS[_p]}**")

            st.divider()

            # Uma linha por permissão
            _novos_valores: dict[str, set] = {p: set() for p in _perfis_edit}
            for _perm_key, _perm_label in _todos_perms.items():
                _row = st.columns([2] + [1] * len(_perfis_edit))
                _row[0].markdown(_perm_label)
                for _i, _p in enumerate(_perfis_edit):
                    _marcado = _row[_i + 1].checkbox(
                        label=" ",
                        value=_perm_key in _perms_atuais[_p],
                        key=f"perm_{_p}_{_perm_key}",
                        label_visibility="collapsed",
                    )
                    if _marcado:
                        _novos_valores[_p].add(_perm_key)

            st.divider()
            if st.form_submit_button("💾 Salvar permissões", type="primary"):
                _erros = []
                for _p, _perms in _novos_valores.items():
                    if not set_permissoes_perfil(_p, _perms):
                        _erros.append(_p)
                if _erros:
                    st.error(f"Erro ao salvar perfis: {', '.join(_erros)}")
                else:
                    st.success("✅ Permissões salvas! Usuários verão as mudanças no próximo login.")

    # ══════════════════════════════════════════════════════════════════════════
    # ABA 3 — CONFIGURAÇÕES
    # ══════════════════════════════════════════════════════════════════════════
    with t_cfg:
        st.markdown("### Configurações de E-mail (SMTP)")

        sb_ok = supabase_ok()
        if not sb_ok:
            st.warning(
                "⚠️ Supabase não configurado — as configurações serão salvas apenas nas "
                "variáveis de ambiente do servidor (.env)."
            )

        with st.form("form_smtp"):
            c1, c2 = st.columns(2)
            with c1:
                smtp_host = st.text_input(
                    "Host SMTP", value=_cfg.get("smtp_host", "smtp.gmail.com")
                )
                smtp_port = st.number_input(
                    "Porta", value=int(_cfg.get("smtp_port", "587")), step=1
                )
                smtp_usr  = st.text_input(
                    "Usuário (e-mail remetente)", value=_cfg.get("smtp_usuario", "")
                )
            with c2:
                smtp_pwd  = st.text_input("Senha de aplicativo", type="password")
                smtp_rem  = st.text_input(
                    "Remetente (nome exibido)", value=_cfg.get("smtp_remetente", "Toque de Cor Pedidos")
                )
                smtp_dest = st.text_area(
                    "Destinatários (separados por vírgula)",
                    value=_cfg.get("smtp_destinatarios", ""),
                    height=80,
                )

            if st.form_submit_button("💾 Salvar configurações SMTP", type="primary"):
                set_config("smtp_host", smtp_host)
                set_config("smtp_port", str(int(smtp_port)))
                set_config("smtp_usuario", smtp_usr)
                set_config("smtp_remetente", smtp_rem)
                set_config("smtp_destinatarios", smtp_dest)
                if smtp_pwd:
                    set_config("smtp_senha", smtp_pwd)
                st.success("Configurações SMTP salvas.")

        st.divider()
        st.markdown("### Outras Configurações")

        with st.form("form_outras_cfg"):
            aprov = st.checkbox(
                "Exigir aprovação de supervisor antes de enviar pedido",
                value=_cfg.get("pedido_aprovacao", "false") == "true",
            )
            sess_h = st.number_input(
                "Tempo de sessão (horas)", min_value=1, max_value=48,
                value=int(_cfg.get("session_hours", "8")),
            )
            if st.form_submit_button("💾 Salvar"):
                set_config("pedido_aprovacao", "true" if aprov else "false")
                set_config("session_hours", str(int(sess_h)))
                st.success("Configurações salvas.")

    # ══════════════════════════════════════════════════════════════════════════
    # ABA 4 — IMPORTAR PLANILHA
    # ══════════════════════════════════════════════════════════════════════════
    with t_imp:
        st.markdown("### Importar Nova Tabela de Preços")

        # ── Status da tabela atual ──────────────────────────────────────────
        ultima = get_config("ultima_importacao", "")
        nome   = get_config("excel_nome", "")
        if ultima:
            st.success(f"📄 **Tabela ativa:** `{nome or '—'}`  \n🕒 **Importada em:** {ultima}")
        else:
            st.warning("⚠️ Nenhuma importação registrada ainda.")

        st.divider()
        st.markdown(
            "Selecione um arquivo `.xlsx` com as abas "
            "**Tabela RN, Tabela BA, Tabela PE, Tabela AL, Tabela PB**.  \n"
            "Colunas: `UF | SKU | Descrição | Embalagem | Cor | Preço c/ ICMS`"
        )

        _PEND_BYTES  = "import_pending_bytes"
        _PEND_NAME   = "import_pending_name"
        _REPORT_KEY  = "import_report"
        _IMPORT_DONE = "import_done"

        if not st.session_state.get(_PEND_BYTES):
            # ── FASE 1: Seleção e validação do arquivo ──────────────────────
            uploaded = st.file_uploader("Selecionar arquivo Excel (.xlsx)", type=["xlsx"])
            if uploaded:
                import tempfile
                _prog = st.progress(0, text="📂 Recebendo arquivo...")
                raw = uploaded.getvalue()
                _prog.progress(25, text="💾 Salvando arquivo temporário...")
                dest = Path(tempfile.gettempdir()) / uploaded.name
                dest.write_bytes(raw)
                _prog.progress(55, text="🔍 Validando estrutura da planilha...")
                try:
                    test = pd.read_excel(str(dest), sheet_name="Tabela RN", header=0)
                    _prog.progress(90, text="⚙️ Preparando ativação...")
                    _prog.progress(100, text="✅ Arquivo validado e pronto para ativar!")
                    # Armazena e troca para Fase 2
                    st.session_state[_PEND_BYTES] = raw
                    st.session_state[_PEND_NAME]  = uploaded.name
                    st.rerun()
                except Exception as e:
                    dest.unlink(missing_ok=True)
                    _prog.empty()
                    st.error(f"Erro ao ler o arquivo: {e}")
        else:
            # ── FASE 2: Arquivo pendente ─────────────────────────────────────
            pending_name  = st.session_state[_PEND_NAME]
            pending_bytes = st.session_state[_PEND_BYTES]

            if not st.session_state.get(_IMPORT_DONE):
                # ── FASE 2a: Aguardando confirmação ──────────────────────────
                tamanho_mb = round(len(pending_bytes) / 1024 / 1024, 2)
                st.success(f"📄 **{pending_name}** ({tamanho_mb} MB) — arquivo validado e pronto para ativar.")

                col_btn, col_troca = st.columns([3, 1])
                with col_btn:
                    ativar = st.button("📥 Ativar esta planilha", type="primary", use_container_width=True)
                with col_troca:
                    if st.button("🔄 Trocar arquivo", use_container_width=True):
                        st.session_state.pop(_PEND_BYTES, None)
                        st.session_state.pop(_PEND_NAME, None)
                        st.rerun()
            else:
                ativar = False

            if ativar:
                import tempfile, traceback as _tb
                try:
                    dest = Path(tempfile.gettempdir()) / pending_name
                    dest.write_bytes(pending_bytes)
                    st.session_state[excel_source_key] = str(dest)
                    st.session_state.pop("caches_warmed", None)
                    try:
                        if clear_caches_fn:
                            clear_caches_fn()
                    except Exception:
                        pass

                    # Persiste no Supabase Storage
                    try:
                        from db_supabase import upload_excel_storage
                        with st.spinner("☁️ Salvando Excel no Supabase Storage..."):
                            ok = upload_excel_storage(pending_bytes, pending_name)
                        if ok:
                            st.info("☁️ Excel salvo no Storage — persistirá após reinício do servidor.")
                    except Exception:
                        pass

                    from datetime import datetime, timezone, timedelta
                    _BR_TZ = timezone(timedelta(hours=-3))

                    # Importa catálogo para o Supabase
                    with st.spinner("⏳ Comparando e enviando catálogo para o Supabase... (pode levar ~1 minuto)"):
                        try:
                            from importar_catalogo import importar
                            resultado = importar(str(dest))
                            tot  = sum(v["total"]         for v in resultado.values())
                            ins  = sum(v["inseridos"]     for v in resultado.values())
                            upd  = sum(v["atualizados"]   for v in resultado.values())
                            rem  = sum(v["removidos"]     for v in resultado.values())
                            same = sum(v["sem_alteracao"] for v in resultado.values())
                            st.success(
                                f"✅ Catálogo atualizado — **{tot} produtos**  \n"
                                f"🟢 +{ins} novos  🔄 {upd} atualizados  "
                                f"🔴 -{rem} removidos  ⚪ {same} sem alteração"
                            )
                            # Coleta relatório de diff — SEMPRE salva para exibir após o rerun
                            _rpt_precos = [r for v in resultado.values() for r in v.get("precos_alterados", [])]
                            _rpt_citel  = [r for v in resultado.values() for r in v.get("novos_citel", [])]
                            st.session_state[_REPORT_KEY] = {
                                "precos": _rpt_precos,
                                "citel":  _rpt_citel,
                                "data":   datetime.now(_BR_TZ).strftime("%d/%m/%Y %H:%M"),
                                "stats":  {"total": tot, "inseridos": ins, "atualizados": upd,
                                           "removidos": rem, "sem_alteracao": same},
                            }
                        except Exception:
                            _exc_text = _tb.format_exc()
                            st.warning("⚠️ Falha ao sincronizar catálogo com o Supabase — app usará Excel local normalmente.")
                            with st.expander("🔍 Ver detalhe do erro"):
                                st.code(_exc_text)

                    # Registra data/nome da importação
                    _agora = datetime.now(_BR_TZ).strftime("%d/%m/%Y %H:%M")
                    set_config("ultima_importacao", _agora)
                    set_config("excel_nome", pending_name)
                    registrar_auditoria(u.get("usuario", ""), "IMPORTACAO", pending_name)

                    try:
                        if clear_caches_fn:
                            clear_caches_fn()
                    except Exception:
                        pass

                    # Sync CITEL inline (funciona quando MySQL acessível — local ou rede da empresa)
                    try:
                        import sync_citel_supabase as _scs
                        with st.spinner("🔄 Sincronizando catálogo CITEL → Supabase..."):
                            _ok_s, _msg_s = _scs.main(force=True)
                        if _ok_s:
                            st.success(f"✅ CITEL sincronizado — {_msg_s}")
                        else:
                            st.caption(f"ℹ️ Sync CITEL indisponível neste ambiente: {_msg_s}")
                    except Exception as _e_scs:
                        st.caption(f"ℹ️ Sync CITEL não executado: {_e_scs}")

                    st.toast("Tabela atualizada!", icon="🎨")
                    st.session_state[_IMPORT_DONE] = True
                    # Sem st.rerun() — mantém aba ativa

                except Exception:
                    st.error("❌ Erro inesperado ao ativar a planilha.")
                    with st.expander("🔍 Ver detalhe do erro"):
                        st.code(_tb.format_exc())

            if st.session_state.get(_IMPORT_DONE):
                # ── FASE 2b: Relatório pós-importação ───────────────────────
                import io as _io
                def _fmt_brl(v) -> str:
                    try:
                        f = float(str(v).replace(",", ".").replace("R$", "").strip())
                        s = f"{f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                        return f"R$ {s}"
                    except Exception:
                        return str(v)
                _BRL_COLS = {"Preço Anterior", "Preço Atual", "Diferença", "Preço"}
                _rpt = st.session_state.get(_REPORT_KEY, {})
                st.markdown(f"### 📊 Relatório da Importação — {_rpt.get('data', '')}")
                _rs = _rpt.get("stats", {})
                st.info(
                    f"**{_rs.get('total', '?')} produtos** processados — "
                    f"🟢 +{_rs.get('inseridos', 0)} novos  "
                    f"🔄 {_rs.get('atualizados', 0)} atualizados  "
                    f"🔴 -{_rs.get('removidos', 0)} removidos  "
                    f"⚪ {_rs.get('sem_alteracao', 0)} sem alteração"
                )
                if _rpt.get("precos"):
                    _df_p = pd.DataFrame(_rpt["precos"])
                    try:
                        _df_p["Diferença"] = (
                            pd.to_numeric(_df_p["Preço Atual"], errors="coerce")
                            - pd.to_numeric(_df_p["Preço Anterior"], errors="coerce")
                        )
                    except Exception:
                        pass
                    _df_disp = _df_p.copy()
                    for _bc in _BRL_COLS:
                        if _bc in _df_disp.columns:
                            _df_disp[_bc] = _df_disp[_bc].apply(_fmt_brl)
                    st.markdown(f"#### 💰 Alterações de Preço — {len(_rpt['precos'])} itens")
                    st.dataframe(_df_disp, hide_index=True, use_container_width=True)
                else:
                    st.caption("💰 Nenhuma alteração de preço nesta importação.")
                if _rpt.get("citel"):
                    st.markdown(f"#### 🔗 Novos Vínculos CITEL — {len(_rpt['citel'])} itens")
                    st.dataframe(pd.DataFrame(_rpt["citel"]), hide_index=True, use_container_width=True)
                else:
                    st.caption("🔗 Nenhum novo vínculo CITEL nesta importação.")
                # ── Botões de exportação ─────────────────────────────────────
                _fn = "relatorio_" + _rpt.get("data", "").replace("/", "").replace(" ", "_").replace(":", "")
                _ec, _pc, _fc = st.columns([2, 2, 3])
                with _ec:
                    _xbuf = _io.BytesIO()
                    with pd.ExcelWriter(_xbuf, engine="openpyxl") as _xlw:
                        pd.DataFrame([_rs]).to_excel(_xlw, sheet_name="Resumo", index=False)
                        if _rpt.get("precos"):
                            _df_xl = pd.DataFrame(_rpt["precos"])
                            try:
                                _df_xl["Diferença"] = (
                                    pd.to_numeric(_df_xl["Preço Atual"], errors="coerce")
                                    - pd.to_numeric(_df_xl["Preço Anterior"], errors="coerce")
                                )
                            except Exception:
                                pass
                            for _bc in _BRL_COLS:
                                if _bc in _df_xl.columns:
                                    _df_xl[_bc] = _df_xl[_bc].apply(_fmt_brl)
                            _df_xl.to_excel(_xlw, sheet_name="Precos", index=False)
                        if _rpt.get("citel"):
                            pd.DataFrame(_rpt["citel"]).to_excel(_xlw, sheet_name="CITEL", index=False)
                    _xbuf.seek(0)
                    st.download_button("📥 Exportar Excel", _xbuf, file_name=f"{_fn}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True)
                with _pc:
                    try:
                        from fpdf import FPDF as _FPDF
                        _pdf = _FPDF(orientation="L", unit="mm", format="A4")
                        _pdf.set_auto_page_break(auto=True, margin=15)
                        _pdf.add_page()
                        _PW = _pdf.w - 2 * _pdf.l_margin
                        _pdf.set_font("Helvetica", "B", 14)
                        _pdf.cell(0, 10, "Toque de Cor - Relatorio de Importacao", ln=True, align="C")
                        _pdf.set_font("Helvetica", "", 9)
                        _pdf.cell(0, 7,
                            f"Data: {_rpt.get('data', '')}   |   "
                            f"Total: {_rs.get('total', 0)}   Novos: +{_rs.get('inseridos', 0)}   "
                            f"Atualizados: {_rs.get('atualizados', 0)}   "
                            f"Removidos: -{_rs.get('removidos', 0)}",
                            ln=True, align="C")
                        _pdf.ln(4)
                        def _safe_pdf(text: str) -> str:
                            subs = {"—": "-", "–": "-", "\u2018": "'", "\u2019": "'",
                                    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00b7": "."}
                            for k, v in subs.items():
                                text = text.replace(k, v)
                            return text.encode("latin-1", errors="replace").decode("latin-1")
                        def _brl(v) -> str:
                            try:
                                f = float(str(v).replace(",", ".").replace("R$", "").strip())
                                s = f"{f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                                return f"R$ {s}"
                            except Exception:
                                return str(v)
                        _PRICE_COLS = {"Preço Anterior", "Preço Atual", "Preço", "Diferença"}
                        _COL_W = {
                            "UF": 12, "SKU": 25, "COD CITEL": 22, "Descrição": 80,
                            "Embalagem": 20, "Cor": 20, "Preço Anterior": 24,
                            "Preço Atual": 24, "Diferença": 22, "Preço": 28,
                        }
                        def _ptab(rows, title):
                            if not rows:
                                return
                            _pdf.set_font("Helvetica", "B", 10)
                            _pdf.cell(0, 7, _safe_pdf(title), ln=True)
                            _cols = list(rows[0].keys())
                            _raw_w = [_COL_W.get(_c, 28) for _c in _cols]
                            _scale = _PW / sum(_raw_w)
                            _widths = [w * _scale for w in _raw_w]
                            _pdf.set_fill_color(210, 210, 210)
                            _pdf.set_font("Helvetica", "B", 7)
                            for _c, _cw in zip(_cols, _widths):
                                _pdf.cell(_cw, 6, _safe_pdf(str(_c)[:22]), border=1, fill=True)
                            _pdf.ln()
                            _pdf.set_font("Helvetica", "", 7)
                            for _r in rows:
                                for _c, _cw in zip(_cols, _widths):
                                    _raw = _brl(_r.get(_c, "")) if _c in _PRICE_COLS else str(_r.get(_c, ""))
                                    _v = _safe_pdf(_raw)
                                    while _v and _pdf.get_string_width(_v) > _cw - 1.5:
                                        _v = _v[:-1]
                                    _pdf.cell(_cw, 5, _v, border=1)
                                _pdf.ln()
                            _pdf.ln(3)
                        if _rpt.get("precos"):
                            _precos_pdf = []
                            for _r in _rpt["precos"]:
                                _row = dict(_r)
                                try:
                                    _pa = float(str(_row.get("Preço Anterior", 0)).replace(",", "."))
                                    _pu = float(str(_row.get("Preço Atual", 0)).replace(",", "."))
                                    _row["Diferença"] = round(_pu - _pa, 2)
                                except Exception:
                                    _row["Diferença"] = ""
                                _precos_pdf.append(_row)
                            _ptab(_precos_pdf, f"Alteracoes de Preco ({len(_precos_pdf)} itens)")
                        else:
                            _pdf.set_font("Helvetica", "I", 9)
                            _pdf.cell(0, 6, "Nenhuma alteracao de preco nesta importacao.", ln=True)
                        if _rpt.get("citel"):
                            _ptab(_rpt["citel"], f"Novos Vinculos CITEL ({len(_rpt['citel'])} itens)")
                        else:
                            _pdf.set_font("Helvetica", "I", 9)
                            _pdf.cell(0, 6, "Nenhum novo vinculo CITEL nesta importacao.", ln=True)
                        st.download_button("📄 Exportar PDF", bytes(_pdf.output()),
                            file_name=f"{_fn}.pdf", mime="application/pdf",
                            use_container_width=True)
                    except ImportError:
                        st.caption("PDF indisponível")
                with _fc:
                    if st.button("✅ Fechar relatório e finalizar", type="primary", use_container_width=True):
                        for _k in (_PEND_BYTES, _PEND_NAME, _REPORT_KEY, _IMPORT_DONE):
                            st.session_state.pop(_k, None)

    # ══════════════════════════════════════════════════════════════════════════
    # ABA 5 — AUDITORIA
    # ══════════════════════════════════════════════════════════════════════════
    with t_aud:
        st.markdown("### Log de Auditoria")

        logs = listar_auditoria(200)
        if logs:
            df_log = pd.DataFrame(logs)[["criado_em", "usuario", "acao", "detalhe"]]
            df_log.columns = ["Data/Hora", "Usuário", "Ação", "Detalhe"]
            st.dataframe(df_log, hide_index=True, use_container_width=True)
        else:
            st.info("Nenhum registro de auditoria ainda.")

    # ══════════════════════════════════════════════════════════════════════════
    # ABA 6 — DIAGNÓSTICO DE CONECTIVIDADE CITEL
    # ══════════════════════════════════════════════════════════════════════════
    with t_diag:
        st.markdown("### 🔌 Diagnóstico de Conectividade CITEL")
        st.caption("Verifica se este ambiente consegue acessar o MySQL CITEL diretamente.")

        _CITEL_HOST = "SRVORACLEBR18.CITELSOFTWARE.COM.BR"
        _CITEL_PORT = 61670

        if st.button("▶ Testar conexão com CITEL agora", type="primary"):
            import socket, time as _time
            _res = []

            # 1. DNS
            with st.spinner("Resolvendo DNS..."):
                try:
                    _ip = socket.gethostbyname(_CITEL_HOST)
                    _res.append(("✅ DNS", f"{_CITEL_HOST} → {_ip}"))
                except Exception as _e:
                    _res.append(("❌ DNS", str(_e)))
                    st.error(f"DNS falhou: {_e}")
                    st.stop()

            # 2. TCP
            with st.spinner("Testando porta TCP..."):
                _t0 = _time.time()
                try:
                    _s = socket.create_connection((_CITEL_HOST, _CITEL_PORT), timeout=10)
                    _s.close()
                    _res.append(("✅ TCP", f"Porta {_CITEL_PORT} aberta em {_time.time()-_t0:.1f}s"))
                except Exception as _e:
                    _res.append(("❌ TCP", f"Timeout/bloqueado em {_time.time()-_t0:.1f}s: {_e}"))

            # 3. MySQL
            _tcp_ok = _res[-1][0].startswith("✅")
            if _tcp_ok:
                with st.spinner("Conectando ao MySQL..."):
                    try:
                        import pymysql
                        _conn = pymysql.connect(
                            host=_CITEL_HOST, port=_CITEL_PORT,
                            user="converte_toquedecor", password="converte13347",
                            database="AUTCOM", connect_timeout=15,
                        )
                        with _conn.cursor() as _cur:
                            _cur.execute("SELECT COUNT(*) FROM CADITE")
                            _cnt = _cur.fetchone()[0]
                        _conn.close()
                        _res.append(("✅ MySQL", f"Conectado! CADITE tem {_cnt} registros."))
                    except Exception as _e:
                        _res.append(("❌ MySQL", str(_e)))

            # Exibe resultado
            for _status, _detail in _res:
                st.markdown(f"**{_status}** — {_detail}")

            if all(r[0].startswith("✅") for r in _res):
                st.success("🎉 Este ambiente acessa o CITEL! O sync automático pode rodar aqui.")
            else:
                st.warning("⚠️ Este ambiente NÃO acessa o CITEL (IP bloqueado). Sync deve ser feito localmente.")
