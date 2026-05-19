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
import uuid
import streamlit as st
from datetime import datetime, timedelta

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
    """Retorna CookieController criando uma única instância por sessão WebSocket."""
    _KEY = "_tdc_cookie_ctrl"
    if _KEY not in st.session_state:
        try:
            from streamlit_cookies_controller import CookieController
            st.session_state[_KEY] = CookieController(key="tdc_auth")
        except Exception:
            st.session_state[_KEY] = None
    return st.session_state.get(_KEY)

PERFIS = {
    "admin":      "Administrador",
    "supervisor": "Supervisor",
    "vendedor":   "Vendedor",
}

# Permissões padrão (usadas como fallback se não houver configuração no Supabase)
_PERMISSOES_PADRAO = {
    "admin":      {"ver_precos", "fazer_pedidos", "gerenciar_usuarios", "importar_planilha", "aprovar_pedidos"},
    "supervisor": {"ver_precos", "fazer_pedidos", "aprovar_pedidos"},
    "vendedor":   {"fazer_pedidos"},
}

# Rótulos legíveis de cada permissão
TODAS_PERMISSOES = {
    "fazer_pedidos":      "📋 Montar e enviar pedidos",
    "ver_precos":         "👁️ Ver preços dos produtos",
    "aprovar_pedidos":    "✅ Aprovar / rejeitar pedidos",
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
        r = sb.table("usuarios").select("id, usuario, nome, perfil, loja, ativo").execute()
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


def criar_usuario(usuario: str, nome: str, senha: str, perfil: str, loja: str) -> tuple[bool, str]:
    """Cria novo usuário. Retorna (sucesso, mensagem)."""
    if perfil not in PERFIS:
        return False, f"Perfil inválido: {perfil}"
    if len(senha) < 6:
        return False, "Senha deve ter no mínimo 6 caracteres."
    sb = _get_sb()
    if not sb:
        return False, "Banco de dados indisponível."
    try:
        sb.table("usuarios").insert({
            "usuario": usuario.strip().lower(),
            "nome":    nome.strip(),
            "senha":   hash_senha(senha),
            "perfil":  perfil,
            "loja":    loja.strip(),
            "ativo":   True,
        }).execute()
        return True, f"Usuário '{usuario}' criado com sucesso."
    except Exception as e:
        msg = str(e)
        if "duplicate" in msg.lower() or "unique" in msg.lower():
            return False, f"Usuário '{usuario}' já existe."
        return False, f"Erro ao criar usuário: {msg}"


def alterar_senha(usuario: str, nova_senha: str) -> tuple[bool, str]:
    """Altera a senha de um usuário."""
    if len(nova_senha) < 6:
        return False, "Senha deve ter no mínimo 6 caracteres."
    sb = _get_sb()
    if not sb:
        return False, "Banco de dados indisponível."
    try:
        sb.table("usuarios").update({"senha": hash_senha(nova_senha)}).eq("usuario", usuario).execute()
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
    _token_store()[_token] = {
        "usuario": dados["usuario"],
        "nome":    dados.get("nome", usuario),
        "perfil":  _perfil,
        "loja":    dados.get("loja", ""),
    }
    st.session_state["auth_usuario"]    = dados["usuario"]
    st.session_state["auth_nome"]       = dados.get("nome", usuario)
    st.session_state["auth_perfil"]     = _perfil
    st.session_state["auth_loja"]       = dados.get("loja", "")
    st.session_state["auth_permissoes"] = _permissoes_do_perfil(_perfil)
    st.session_state["auth_token"]      = _token
    # Persiste token no cookie — sobrevive a reconexões WebSocket
    ctrl = _get_cookie_ctrl()
    if ctrl:
        ctrl.set("tdc_session", _token)
    return True, "Login realizado com sucesso."


def fazer_logout():
    """Encerra a sessão atual."""
    token = st.session_state.get("auth_token")
    if token:
        _token_store().pop(token, None)
    ctrl = _get_cookie_ctrl()
    if ctrl:
        try:
            ctrl.remove("tdc_session")
        except Exception:
            pass
    for key in ["auth_usuario", "auth_nome", "auth_perfil", "auth_loja",
                "auth_expira", "auth_permissoes", "auth_token", "_tdc_cookie_ctrl"]:
        st.session_state.pop(key, None)


def esta_logado() -> bool:
    """Verifica se há sessão ativa. Restaura automaticamente via cookie após reconexão."""
    if "auth_usuario" in st.session_state:
        return True
    # session_state vazio (reconexão WebSocket ou refresh) — tenta restaurar pelo cookie
    ctrl = _get_cookie_ctrl()
    if ctrl:
        try:
            token = ctrl.get("tdc_session")
            if token and token in _token_store():
                dados = _token_store()[token]
                st.session_state["auth_usuario"]    = dados["usuario"]
                st.session_state["auth_nome"]       = dados["nome"]
                st.session_state["auth_perfil"]     = dados["perfil"]
                st.session_state["auth_loja"]       = dados["loja"]
                st.session_state["auth_permissoes"] = _permissoes_do_perfil(dados["perfil"])
                st.session_state["auth_token"]      = token
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


def requer_login():
    """
    Exibe tela de login se não autenticado.
    Deve ser chamado no início de cada página.
    Retorna True se autenticado, False se exibiu tela de login.
    """
    if esta_logado():
        return True

    _seed_admin_fallback()

    st.markdown(
        "<h2 style='text-align:center;margin-top:3rem'>🎨 Toque de Cor</h2>"
        "<p style='text-align:center;color:#888'>Sistema de Pedidos</p>",
        unsafe_allow_html=True,
    )

    col_l, col_c, col_r = st.columns([1, 1.2, 1])
    with col_c:
        with st.form("form_login"):
            st.markdown("#### Entrar")
            usr = st.text_input("Usuário", placeholder="seu.usuario")
            pwd = st.text_input("Senha", type="password", placeholder="••••••••")
            submitted = st.form_submit_button("Entrar", use_container_width=True, type="primary")

        if submitted:
            ok, msg = fazer_login(usr, pwd)
            if ok:
                st.rerun()
            else:
                st.error(msg)

    return False
