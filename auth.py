"""
Módulo de autenticação — Toque de Cor Web

Perfis:
  admin      → acesso total (importar planilha, gerenciar usuários, configurar e-mail)
  supervisor → ver preços + fazer pedidos (não gerencia usuários)
  vendedor   → montar e enviar pedidos (NÃO vê preços)

Armazenamento: tabela `usuarios` no Supabase (PostgreSQL).
Senhas: hash bcrypt (nunca armazenadas em texto puro).
Sessão: st.session_state com expiração configurável (padrão 8h).
"""

import hashlib
import os
import re
import unicodedata
import uuid
import streamlit as st
from datetime import datetime, timedelta, timezone

# ── Dependências opcionais ────────────────────────────────────────────────────
try:
    import bcrypt
    _BCRYPT_OK = True
except ImportError:
    _BCRYPT_OK = False

# ── Constantes ────────────────────────────────────────────────────────────────
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "8"))  # mantido por compatibilidade

# ── Token store (persiste entre reconexões WebSocket — vive no processo Python) ─
@st.cache_resource
def _token_store() -> dict:
    """Dict {token_uuid: user_data}. Sobrevive a reconexões sem precisar de DB."""
    return {}


def _get_cookie_ctrl():
    """Desabilitado — streamlit-cookies-controller não funciona no HF Spaces (cross-origin)."""
    return None

PERFIS = {
    "admin":      "Administrador",
    "supervisor": "Supervisor",
    "vendedor":   "Vendedor",
}

# Permissões padrão (usadas como fallback se não houver configuração no Supabase)
_PERMISSOES_PADRAO = {
    "admin":      {"ver_precos", "fazer_pedidos", "gerenciar_usuarios", "importar_planilha", "aprovar_pedidos", "reenviar_pedido"},
    "supervisor": {"ver_precos", "fazer_pedidos", "aprovar_pedidos", "reenviar_pedido"},
    "vendedor":   {"fazer_pedidos"},
}

# Rótulos legíveis de cada permissão
TODAS_PERMISSOES = {
    "fazer_pedidos":      "📋 Montar e enviar pedidos",
    "ver_precos":         "👁️ Ver preços dos produtos",
    "aprovar_pedidos":    "✅ Aprovar / rejeitar pedidos",
    "reenviar_pedido":    "📧 Reenviar pedido por e-mail",
    "importar_planilha":  "📥 Importar tabela de preços",
    "gerenciar_usuarios": "👥 Gerenciar usuários (Painel Admin)",
}


def _permissoes_do_perfil(perfil: str) -> set:
    """Retorna permissões do perfil: tenta Supabase primeiro, usa padrão como fallback."""
    try:
        from db_supabase import get_permissoes_perfil
        perms = get_permissoes_perfil(perfil)
        if perms:  # se há configuração salva, usa ela
            return perms
    except Exception:
        pass
    return _PERMISSOES_PADRAO.get(perfil, set())


# ── Helpers de hash ───────────────────────────────────────────────────────────
def hash_senha(senha: str) -> str:
    """Gera hash bcrypt da senha. Fallback para SHA-256 se bcrypt indisponível."""
    if _BCRYPT_OK:
        return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
    return hashlib.sha256(senha.encode()).hexdigest()


def verificar_senha(senha: str, hashed: str) -> bool:
    """Verifica senha contra o hash armazenado."""
    try:
        if _BCRYPT_OK and hashed.startswith("$2"):
            return bcrypt.checkpw(senha.encode(), hashed.encode())
        return hashlib.sha256(senha.encode()).hexdigest() == hashed
    except Exception:
        return False


# ── Gerenciamento de usuários via Supabase ────────────────────────────────────
def _get_sb():
    """Retorna cliente Supabase, ou None se não configurado."""
    try:
        from db_supabase import get_supabase
        return get_supabase()
    except Exception:
        return None


def listar_usuarios() -> list[dict]:
    """Retorna todos os usuários (sem hash de senha)."""
    sb = _get_sb()
    if not sb:
        return _usuarios_fallback()
    try:
        r = sb.table("usuarios").select("id, usuario, nome, perfil, loja, uf, ativo").execute()
        return r.data or []
    except Exception:
        return _usuarios_fallback()


def buscar_usuario(usuario: str) -> dict | None:
    """Busca usuário pelo login. Retorna dict completo (com hash) ou None."""
    sb = _get_sb()
    if not sb:
        return _buscar_fallback(usuario)
    try:
        r = (
            sb.table("usuarios")
            .select("*")
            .eq("usuario", usuario.strip().lower())
            .eq("ativo", True)
            .single()
            .execute()
        )
        return r.data
    except Exception:
        return _buscar_fallback(usuario)


def buscar_usuario_por_nome(nome: str) -> dict | None:
    """Busca o usuário mais recente pelo nome completo. Usado ao vincular código após criação."""
    sb = _get_sb()
    if not sb:
        return None
    try:
        r = (
            sb.table("usuarios")
            .select("*")
            .eq("nome", nome.strip())
            .eq("ativo", True)
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        return r.data[0] if r.data else None
    except Exception:
        return None


def _gerar_login(nome: str) -> str:
    """Gera login interno a partir do nome completo (sem acentos, letras e pontos)."""
    nfd = unicodedata.normalize("NFD", nome.strip().lower())
    sem_ac = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    slug = re.sub(r"[^a-z0-9.]", "", sem_ac.replace(" ", "."))
    slug = re.sub(r"\.+", ".", slug).strip(".")
    return slug or "usuario"


def criar_usuario(nome: str, senha: str, perfil: str, loja: str, uf: str = "", usuario: str = "") -> tuple[bool, str]:
    """Cria novo usuário. Login é gerado automaticamente a partir do nome se não informado."""
    if perfil not in PERFIS:
        return False, f"Perfil inválido: {perfil}"
    if len(senha) < 6:
        return False, "Senha deve ter no mínimo 6 caracteres."
    sb = _get_sb()
    if not sb:
        return False, "Banco de dados indisponível."
    # Auto-gera login único a partir do nome
    if not usuario:
        usuario = _gerar_login(nome)
    base_login = usuario
    sufixo = 2
    while True:
        try:
            existe = sb.table("usuarios").select("usuario").eq("usuario", usuario).execute()
            if not existe.data:
                break
            usuario = f"{base_login}{sufixo}"
            sufixo += 1
        except Exception:
            break
    try:
        sb.table("usuarios").insert({
            "usuario": usuario,
            "nome":    nome.strip(),
            "senha":   hash_senha(senha),
            "perfil":  perfil,
            "loja":    loja.strip(),
            "uf":      uf.strip() or None,
            "ativo":   True,
        }).execute()
        # Marca que o novo usuário deve trocar a senha no primeiro acesso
        try:
            from db_supabase import set_precisa_trocar_senha
            set_precisa_trocar_senha(usuario)
        except Exception:
            pass
        return True, f"Usuário '{nome}' criado com sucesso."
    except Exception as e:
        msg = str(e)
        if "duplicate" in msg.lower() or "unique" in msg.lower():
            return False, f"Usuário '{nome}' já existe."
        return False, f"Erro ao criar usuário: {msg}"


def alterar_senha(usuario: str, nova_senha: str) -> tuple[bool, str]:
    """Altera a senha de um usuário (reset pelo admin — exige troca no próximo login)."""
    if len(nova_senha) < 6:
        return False, "Senha deve ter no mínimo 6 caracteres."
    sb = _get_sb()
    if not sb:
        return False, "Banco de dados indisponível."
    try:
        sb.table("usuarios").update({"senha": hash_senha(nova_senha)}).eq("usuario", usuario).execute()
        # Força o usuário a trocar na próxima sessão
        try:
            from db_supabase import set_precisa_trocar_senha
            set_precisa_trocar_senha(usuario)
        except Exception:
            pass
        return True, "Senha alterada com sucesso."
    except Exception as e:
        return False, str(e)


def toggle_usuario(usuario: str, ativo: bool) -> tuple[bool, str]:
    """Ativa ou desativa um usuário."""
    sb = _get_sb()
    if not sb:
        return False, "Banco de dados indisponível."
    try:
        sb.table("usuarios").update({"ativo": ativo}).eq("usuario", usuario).execute()
        status = "ativado" if ativo else "desativado"
        return True, f"Usuário '{usuario}' {status}."
    except Exception as e:
        return False, str(e)


def excluir_usuario(usuario: str) -> tuple[bool, str]:
    """Remove um usuário permanentemente."""
    sb = _get_sb()
    if not sb:
        return False, "Banco de dados indisponível."
    try:
        sb.table("usuarios").delete().eq("usuario", usuario).execute()
        return True, f"Usuário '{usuario}' excluído."
    except Exception as e:
        return False, str(e)


def atualizar_usuario(usuario: str, nome: str, perfil: str, loja: str, uf: str = "") -> tuple[bool, str]:
    """Atualiza nome, perfil, loja e uf de um usuário existente."""
    if perfil not in PERFIS:
        return False, f"Perfil inválido: {perfil}"
    sb = _get_sb()
    if not sb:
        return False, "Banco de dados indisponível."
    try:
        sb.table("usuarios").update({
            "nome":   nome.strip(),
            "perfil": perfil,
            "loja":   loja.strip(),
            "uf":     uf.strip() or None,
        }).eq("usuario", usuario).execute()
        return True, f"Usuário '{usuario}' atualizado com sucesso."
    except Exception as e:
        return False, str(e)


# ── Fallback local (quando Supabase indisponível) ─────────────────────────────
_USUARIOS_LOCAL: list[dict] = []


def _usuarios_fallback() -> list[dict]:
    return [
        {u["usuario"]: {k: v for k, v in u.items() if k != "senha"}}
        for u in _USUARIOS_LOCAL
    ] if _USUARIOS_LOCAL else []


def _buscar_fallback(usuario: str) -> dict | None:
    for u in _USUARIOS_LOCAL:
        if u.get("usuario") == usuario.lower():
            return u
    return None


def _seed_admin_fallback(senha: str = "admin123"):
    """Cria admin local de emergência se lista estiver vazia."""
    if not _USUARIOS_LOCAL:
        _USUARIOS_LOCAL.append({
            "id":      1,
            "usuario": "admin",
            "nome":    "Administrador",
            "senha":   hash_senha(senha),
            "perfil":  "admin",
            "loja":    "Matriz",
            "ativo":   True,
        })


# ── Sessão ────────────────────────────────────────────────────────────────────
def fazer_login(usuario: str, senha: str) -> tuple[bool, str]:
    """
    Autentica o usuário e inicia a sessão.
    Retorna (sucesso, mensagem).
    """
    dados = buscar_usuario(usuario)
    if not dados:
        return False, "Usuário não encontrado ou inativo."
    if not verificar_senha(senha, dados.get("senha", "")):
        return False, "Senha incorreta."

    _perfil = dados.get("perfil", "vendedor")
    _token  = str(uuid.uuid4())
    _uf = dados.get("uf") or ""
    _dados_sessao = {
        "usuario": dados["usuario"],
        "nome":    dados.get("nome", usuario),
        "perfil":  _perfil,
        "loja":    dados.get("loja", ""),
        "uf":      _uf,
    }
    _token_store()[_token] = _dados_sessao
    # Persiste no Supabase — sobrevive a reinicialização do servidor
    try:
        from db_supabase import salvar_sessao
        salvar_sessao(_token, _dados_sessao)
    except Exception:
        pass
    st.session_state["auth_usuario"]    = dados["usuario"]
    st.session_state["auth_nome"]       = dados.get("nome", usuario)
    st.session_state["auth_perfil"]     = _perfil
    st.session_state["auth_loja"]       = dados.get("loja", "")
    st.session_state["auth_uf"]         = _uf
    st.session_state["auth_permissoes"] = _permissoes_do_perfil(_perfil)
    st.session_state["auth_token"]      = _token
    # Persiste token na URL (query param) — sobrevive ao Sleep do Space no HuggingFace
    try:
        st.query_params["t"] = _token
    except Exception:
        pass
    # Verifica se precisa trocar senha
    st.session_state["auth_precisa_trocar_senha"] = _checar_troca_senha(dados["usuario"])
    return True, "Login realizado com sucesso."


def fazer_logout():
    """Encerra a sessão atual."""
    token = st.session_state.get("auth_token")
    if token:
        _token_store().pop(token, None)
        try:
            from db_supabase import remover_sessao
            remover_sessao(token)
        except Exception:
            pass
    # Remove query param da URL
    try:
        st.query_params.pop("t", None)
    except Exception:
        pass
    for key in ["auth_usuario", "auth_nome", "auth_perfil", "auth_loja", "auth_uf",
                "auth_expira", "auth_permissoes", "auth_token", "_cookie_restore_tried"]:
        st.session_state.pop(key, None)


def _restaurar_sessao_por_token(token: str) -> bool:
    """Tenta restaurar a sessão a partir de um token (memória ou Supabase)."""
    dados = _token_store().get(token)
    if not dados:
        try:
            from db_supabase import buscar_sessao
            dados = buscar_sessao(token)
            if dados:
                _token_store()[token] = dados
        except Exception:
            pass
    if dados:
        st.session_state["auth_usuario"]    = dados["usuario"]
        st.session_state["auth_nome"]       = dados["nome"]
        st.session_state["auth_perfil"]     = dados["perfil"]
        st.session_state["auth_loja"]       = dados["loja"]
        st.session_state["auth_uf"]         = dados.get("uf", "")
        st.session_state["auth_permissoes"] = _permissoes_do_perfil(dados["perfil"])
        st.session_state["auth_token"]      = token
        # Ao restaurar sessão, revalida se precisa trocar senha (não confia apenas no cache)
        if "auth_precisa_trocar_senha" not in st.session_state:
            st.session_state["auth_precisa_trocar_senha"] = _checar_troca_senha(dados["usuario"])
        return True
    return False


def esta_logado() -> bool:
    """Verifica se há sessão ativa. Restaura automaticamente após reconexão ou Sleep do Space."""
    if "auth_usuario" in st.session_state:
        return True

    # 1. Query param (mecanismo principal — parte da URL, sobrevive ao Sleep do HF Space)
    try:
        token = st.query_params.get("t", "")
        if token and _restaurar_sessao_por_token(token):
            return True
    except Exception:
        pass

    return False


def usuario_atual() -> dict:
    """Retorna dados do usuário logado ou dict vazio."""
    if not esta_logado():
        return {}
    return {
        "usuario": st.session_state.get("auth_usuario", ""),
        "nome":    st.session_state.get("auth_nome", ""),
        "perfil":  st.session_state.get("auth_perfil", "vendedor"),
        "loja":    st.session_state.get("auth_loja", ""),
        "uf":      st.session_state.get("auth_uf", ""),
    }


def tem_permissao(permissao: str) -> bool:
    """Verifica se o usuário logado tem a permissão solicitada (usa sessão, sem HTTP)."""
    perms = st.session_state.get("auth_permissoes")
    if perms is None:
        # sessão antiga (antes da atualização) — reconstrói a partir do perfil
        perfil = st.session_state.get("auth_perfil", "")
        perms = _permissoes_do_perfil(perfil)
        st.session_state["auth_permissoes"] = perms
    return permissao in perms


# ── Troca obrigatória de senha ───────────────────────────────────────────────

def _checar_troca_senha(usuario: str) -> bool:
    """
    Retorna True se o usuário deve ser forçado a trocar a senha:
      - Nunca trocou (flag padrão '1')
      - Última troca há mais de 90 dias (≈ 3 meses)
    """
    try:
        from db_supabase import get_senha_status
        status = get_senha_status(usuario)
        if status["precisa_trocar"]:
            return True
        alterada_em = status["alterada_em"]
        if not alterada_em:
            return True
        dt = datetime.fromisoformat(str(alterada_em).replace("Z", "+00:00"))
        agora = datetime.now(timezone.utc)
        if (agora - dt.astimezone(timezone.utc)).days >= 90:
            return True
    except Exception:
        pass
    return False


def _executar_troca_senha(usuario: str, senha_atual: str, nova_senha: str, confirmar: str) -> tuple[bool, str]:
    """Valida e aplica a troca de senha do próprio usuário."""
    if not senha_atual or not nova_senha or not confirmar:
        return False, "Preencha todos os campos."
    if len(nova_senha) < 6:
        return False, "A nova senha deve ter no mínimo 6 caracteres."
    if nova_senha != confirmar:
        return False, "Nova senha e confirmação não conferem."
    dados = buscar_usuario(usuario)
    if not dados:
        return False, "Usuário não encontrado."
    if not verificar_senha(senha_atual, dados.get("senha", "")):
        return False, "Senha atual incorreta."
    if verificar_senha(nova_senha, dados.get("senha", "")):
        return False, "A nova senha deve ser diferente da senha atual."
    sb = _get_sb()
    if not sb:
        return False, "Banco de dados indisponível."
    try:
        sb.table("usuarios").update({"senha": hash_senha(nova_senha)}).eq("usuario", usuario).execute()
        from db_supabase import set_senha_trocada
        set_senha_trocada(usuario)
        st.session_state["auth_precisa_trocar_senha"] = False
        return True, "Senha alterada com sucesso!"
    except Exception as e:
        return False, f"Erro ao alterar senha: {e}"


def _tela_troca_senha():
    """Tela bloqueante de troca obrigatória de senha."""
    col_l, col_c, col_r = st.columns([1, 1.2, 1])
    with col_c:
        _logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.exists(_logo_path):
            st.image(_logo_path, use_container_width=True)
        st.markdown(
            "<p style='text-align:center;color:#888;margin-top:0.2rem'>Sistema de Pedidos</p>",
            unsafe_allow_html=True,
        )
        st.markdown("#### 🔒 Alteração de Senha Necessária")
        st.info(
            "Por segurança, você precisa cadastrar uma nova senha antes de continuar. "
            "A senha deve ser trocada a cada 3 meses."
        )
        with st.form("form_troca_senha"):
            senha_atual = st.text_input("Senha atual",              type="password", placeholder="••••••••")
            nova_senha  = st.text_input("Nova senha (mín. 6 chars)", type="password", placeholder="••••••••")
            confirmar   = st.text_input("Confirme a nova senha",     type="password", placeholder="••••••••")
            submitted   = st.form_submit_button("Alterar Senha", use_container_width=True, type="primary")
        if submitted:
            usuario = st.session_state.get("auth_usuario", "")
            ok, msg = _executar_troca_senha(usuario, senha_atual, nova_senha, confirmar)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
        if st.button("Sair", use_container_width=True):
            fazer_logout()
            st.rerun()


def requer_login():
    """
    Exibe tela de login se não autenticado.
    Exibe tela de troca de senha se autenticado mas com senha expirada/nova.
    Retorna True apenas quando autenticado E senha em dia.

    Fluxo em 2 passos:
      Passo 1 — Código do operador (3 dígitos) → exibe nome
      Passo 2 — Senha → autentica e libera o app
    """
    if esta_logado():
        if st.session_state.get("auth_precisa_trocar_senha", False):
            _tela_troca_senha()
            return False
        return True

    _seed_admin_fallback()

    _logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")

    # ── Passo 1: código do operador ───────────────────────────────────────────
    if not st.session_state.get("_login_codigo_ok"):
        col_l, col_c, col_r = st.columns([1, 1.2, 1])
        with col_c:
            if os.path.exists(_logo_path):
                st.image(_logo_path, use_container_width=True)
            st.markdown(
                "<p style='text-align:center;color:#888;margin-top:0.2rem'>Sistema de Pedidos</p>",
                unsafe_allow_html=True,
            )
            with st.form("form_login_codigo"):
                st.markdown("#### Entrar")
                codigo = st.text_input("Código do operador", max_chars=3, placeholder="000")
                submitted = st.form_submit_button("Continuar →", use_container_width=True, type="primary")

            if submitted:
                try:
                    from db_supabase import buscar_login_por_codigo
                    login = buscar_login_por_codigo(codigo.strip())
                except Exception:
                    login = ""
                if login:
                    dados = buscar_usuario(login)
                    if dados:
                        st.session_state["_login_codigo_ok"]    = True
                        st.session_state["_login_usuario_pre"]  = login
                        st.rerun()
                    else:
                        st.error("Código não encontrado.")
                else:
                    st.error("Código não encontrado.")
        return False

    # ── Passo 2: senha ────────────────────────────────────────────────────────
    login_pre = st.session_state.get("_login_usuario_pre", "")
    dados_pre = buscar_usuario(login_pre) if login_pre else None
    if not dados_pre:
        st.session_state.pop("_login_codigo_ok", None)
        st.session_state.pop("_login_usuario_pre", None)
        st.rerun()
        return False

    col_l, col_c, col_r = st.columns([1, 1.2, 1])
    with col_c:
        if os.path.exists(_logo_path):
            st.image(_logo_path, use_container_width=True)
        st.markdown(
            "<p style='text-align:center;color:#888;margin-top:0.2rem'>Sistema de Pedidos</p>",
            unsafe_allow_html=True,
        )
        st.success(f"👋 Olá, **{dados_pre.get('nome', login_pre)}!**")
        st.caption(f"{PERFIS.get(dados_pre.get('perfil', ''), '')} · {dados_pre.get('loja', '')}")

        with st.form("form_login_senha"):
            pwd = st.text_input("Senha", type="password", placeholder="••••••••")
            entrar = st.form_submit_button("Entrar", use_container_width=True, type="primary")

        if st.button("← Voltar", use_container_width=True):
            st.session_state.pop("_login_codigo_ok", None)
            st.session_state.pop("_login_usuario_pre", None)
            st.rerun()

        if entrar:
            ok, msg = fazer_login(login_pre, pwd)
            if ok:
                st.session_state.pop("_login_codigo_ok", None)
                st.session_state.pop("_login_usuario_pre", None)
                st.rerun()
            else:
                st.error(msg)

    return False
