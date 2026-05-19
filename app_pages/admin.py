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

    t_usr, t_perfis, t_cfg, t_imp, t_aud = st.tabs(
        ["👥 Usuários", "🔐 Perfis & Permissões", "📧 Configurações", "📥 Importar Planilha", "📋 Auditoria"]
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
        st.markdown(
            "Selecione um arquivo `.xlsx` com as abas "
            "**Tabela RN, Tabela BA, Tabela PE, Tabela AL, Tabela PB**.  \n"
            "Colunas: `UF | SKU | Descrição | Embalagem | Cor | Preço c/ ICMS`"
        )

        uploaded = st.file_uploader("Selecionar arquivo Excel (.xlsx)", type=["xlsx"])
        if uploaded:
            BASE_DIR = Path(__file__).parent.parent
            dest = BASE_DIR / uploaded.name
            dest.write_bytes(uploaded.getvalue())
            try:
                test = pd.read_excel(str(dest), sheet_name="Tabela RN", header=0)
                st.success(f"✅ **{uploaded.name}** carregado — {len(test)} linhas na aba Tabela RN.")

                if st.button("📥 Usar como tabela ativa e limpar cache", type="primary"):
                    st.session_state[excel_source_key] = str(dest)
                    st.session_state.pop("caches_warmed", None)
                    if clear_caches_fn:
                        clear_caches_fn()
                    from datetime import datetime

                    # Importa catálogo para o Supabase em background
                    with st.spinner("⏳ Comparando e enviando catálogo para o Supabase... (pode levar ~1 minuto)"):
                        try:
                            from importar_catalogo import importar
                            resultado = importar(str(dest))
                            # resultado = {uf: {inseridos, atualizados, removidos, sem_alteracao, total}}
                            tot     = sum(v["total"]        for v in resultado.values())
                            ins     = sum(v["inseridos"]    for v in resultado.values())
                            upd     = sum(v["atualizados"]  for v in resultado.values())
                            rem     = sum(v["removidos"]    for v in resultado.values())
                            same    = sum(v["sem_alteracao"] for v in resultado.values())
                            st.success(
                                f"✅ Catálogo atualizado — **{tot} produtos**  \n"
                                f"🟢 +{ins} novos  🔄 {upd} atualizados  "
                                f"🔴 -{rem} removidos  ⚪ {same} sem alteração"
                            )
                        except Exception as e:
                            st.warning(f"⚠️ Falha ao enviar catálogo ao Supabase: {e}. App usará Excel local.")
                            set_config("ultima_importacao", datetime.now().strftime("%d/%m/%Y %H:%M"))
                            set_config("excel_nome", uploaded.name)

                    registrar_auditoria(u.get("usuario",""), "IMPORTACAO", uploaded.name)
                    # Limpa cache para forçar recarga do Supabase
                    if clear_caches_fn:
                        clear_caches_fn()
                    st.toast("Tabela atualizada!", icon="🎨")
                    st.rerun()
            except Exception as e:
                dest.unlink(missing_ok=True)
                st.error(f"Erro ao ler o arquivo: {e}")

        ultima = get_config("ultima_importacao", "")
        nome   = get_config("excel_nome", "")
        if ultima:
            st.info(f"**Última importação:** {ultima}  |  **Arquivo:** {nome or '—'}")

        src = st.session_state.get(excel_source_key, "")
        if src and Path(src).exists():
            st.caption(f"📄 Tabela ativa: `{Path(src).name}`")

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
