"""
Módulo Supabase — Toque de Cor Web

Responsabilidades:
  - Conexão com o Supabase (PostgreSQL gratuito)
  - Inicialização das tabelas necessárias via SQL
  - Funções de pedidos: salvar, listar, atualizar status
  - Configurações do sistema (e-mail, última importação)

Variáveis de ambiente necessárias (.env):
  SUPABASE_URL  = https://xxxx.supabase.co
  SUPABASE_KEY  = eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
"""

import os
import streamlit as st

# ── SQL de inicialização das tabelas ─────────────────────────────────────────
_SQL_INIT = """
-- Usuários do sistema
CREATE TABLE IF NOT EXISTS usuarios (
    id        SERIAL PRIMARY KEY,
    usuario   TEXT UNIQUE NOT NULL,
    nome      TEXT NOT NULL,
    senha     TEXT NOT NULL,
    perfil    TEXT NOT NULL DEFAULT 'vendedor',
    loja      TEXT NOT NULL DEFAULT '',
    ativo     BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em TIMESTAMPTZ DEFAULT NOW()
);

-- Pedidos
CREATE TABLE IF NOT EXISTS pedidos (
    id          SERIAL PRIMARY KEY,
    numero      INTEGER NOT NULL,
    usuario     TEXT NOT NULL,
    loja        TEXT NOT NULL DEFAULT '',
    uf          TEXT NOT NULL,
    desconto_pct NUMERIC(6,2) DEFAULT 0,
    total_geral  NUMERIC(12,2) DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pendente',
    criado_em   TIMESTAMPTZ DEFAULT NOW(),
    enviado_em  TIMESTAMPTZ
);

-- Itens dos pedidos
CREATE TABLE IF NOT EXISTS pedido_itens (
    id          SERIAL PRIMARY KEY,
    pedido_id   INTEGER REFERENCES pedidos(id) ON DELETE CASCADE,
    cod_sku     TEXT,
    cod_citel   TEXT,
    marca       TEXT,
    descricao   TEXT,
    embalagem   TEXT,
    qtd         INTEGER NOT NULL DEFAULT 0,
    preco_unit  NUMERIC(12,2) DEFAULT 0,
    total       NUMERIC(12,2) DEFAULT 0
);

-- Configurações do sistema
CREATE TABLE IF NOT EXISTS configuracoes (
    chave TEXT PRIMARY KEY,
    valor TEXT
);

-- Log de auditoria
CREATE TABLE IF NOT EXISTS auditoria (
    id        SERIAL PRIMARY KEY,
    usuario   TEXT,
    acao      TEXT,
    detalhe   TEXT,
    criado_em TIMESTAMPTZ DEFAULT NOW()
);

-- Espelho CITEL: sincronizado pelo GitHub Actions diariamente
CREATE TABLE IF NOT EXISTS citel_itens (
    cod_fab          TEXT PRIMARY KEY,
    cod_citel        TEXT NOT NULL DEFAULT '',
    descricao_db     TEXT NOT NULL DEFAULT '',
    marca            TEXT NOT NULL DEFAULT '',
    grupo            TEXT NOT NULL DEFAULT '',
    embalagem_db     TEXT NOT NULL DEFAULT '',
    preco_compra_rn  NUMERIC(15,4) DEFAULT 0,
    preco_compra_ba  NUMERIC(15,4) DEFAULT 0,
    preco_compra_pe  NUMERIC(15,4) DEFAULT 0,
    preco_compra_al  NUMERIC(15,4) DEFAULT 0,
    preco_compra_pb  NUMERIC(15,4) DEFAULT 0,
    atualizado_em TIMESTAMPTZ DEFAULT NOW()
);
"""


# ── Cliente Supabase (singleton via cache_resource) ──────────────────────────
@st.cache_resource(show_spinner=False)
def get_supabase():
    """
    Retorna cliente Supabase autenticado.
    Cached permanentemente — reconecta apenas em restart do servidor.
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None


def supabase_ok() -> bool:
    """Verifica se o Supabase está configurado e acessível."""
    sb = get_supabase()
    if not sb:
        return False
    try:
        sb.table("configuracoes").select("chave").limit(1).execute()
        return True
    except Exception:
        return False


# ── Configurações do sistema ─────────────────────────────────────────────────
@st.cache_data(ttl=60)
def get_all_configs() -> dict:
    """Busca todas as configurações do sistema em uma única requisição. Cache 60s."""
    sb = get_supabase()
    if not sb:
        return {}
    try:
        r = sb.table("configuracoes").select("chave,valor").execute()
        return {row["chave"]: row["valor"] for row in (r.data or [])}
    except Exception:
        return {}


def get_config(chave: str, padrao: str = "") -> str:
    sb = get_supabase()
    if not sb:
        return padrao
    try:
        r = sb.table("configuracoes").select("valor").eq("chave", chave).single().execute()
        return r.data.get("valor", padrao) if r.data else padrao
    except Exception:
        return padrao


def set_config(chave: str, valor: str) -> bool:
    sb = get_supabase()
    if not sb:
        return False
    try:
        sb.table("configuracoes").upsert({"chave": chave, "valor": valor}).execute()
        get_all_configs.clear()  # invalida cache após qualquer gravação
        return True
    except Exception:
        return False


def get_permissoes_perfil(perfil: str) -> set:
    """Retorna o conjunto de permissões de um perfil salvo no Supabase.
    Retorna set vazio se não houver configuração (auth.py usa fallback padrão)."""
    import json
    val = get_config(f"permissoes_{perfil}", "")
    if val:
        try:
            return set(json.loads(val))
        except Exception:
            return set()
    return set()


def set_permissoes_perfil(perfil: str, permissoes: set) -> bool:
    """Salva as permissões de um perfil no Supabase."""
    import json
    return set_config(f"permissoes_{perfil}", json.dumps(sorted(permissoes)))


# ── Pedidos ──────────────────────────────────────────────────────────────────
def proximo_numero_pedido() -> int:
    """Retorna o próximo número sequencial de pedido."""
    sb = get_supabase()
    if not sb:
        return 1
    try:
        r = sb.table("pedidos").select("numero").order("numero", desc=True).limit(1).execute()
        if r.data:
            return r.data[0]["numero"] + 1
        return 1
    except Exception:
        return 1


def salvar_pedido(pedido: dict, itens: list[dict]) -> tuple[bool, str, int]:
    """
    Persiste um pedido e seus itens no Supabase.
    Retorna (sucesso, mensagem, id_pedido).
    """
    sb = get_supabase()
    if not sb:
        return False, "Banco de dados indisponível.", -1
    try:
        numero = proximo_numero_pedido()
        r = sb.table("pedidos").insert({
            "numero":      numero,
            "usuario":     pedido.get("usuario", ""),
            "loja":        pedido.get("loja", ""),
            "uf":          pedido.get("uf", ""),
            "desconto_pct": pedido.get("desconto_pct", 0),
            "total_geral":  pedido.get("total_geral", 0),
            "status":      "pendente",
        }).execute()
        pedido_id = r.data[0]["id"]

        linhas = [
            {
                "pedido_id":  pedido_id,
                "cod_sku":    it.get("cod_sku", ""),
                "cod_citel":  it.get("cod_citel", ""),
                "marca":      it.get("marca", ""),
                "descricao":  it.get("descricao", ""),
                "embalagem":  it.get("embalagem", ""),
                "qtd":        int(it.get("qtd", 0)),
                "preco_unit": float(it.get("preco_unit", 0)),
                "total":      float(it.get("total", 0)),
            }
            for it in itens
        ]
        sb.table("pedido_itens").insert(linhas).execute()
        registrar_auditoria(pedido.get("usuario", ""), "PEDIDO_CRIADO", f"Pedido #{numero}")
        return True, f"Pedido #{numero:04d} salvo.", pedido_id
    except Exception as e:
        return False, str(e), -1


def listar_pedidos(usuario: str = "", loja: str = "", perfil: str = "") -> list[dict]:
    """
    Lista pedidos. Admin/Supervisor veem todos; Vendedor vê apenas os próprios.
    """
    sb = get_supabase()
    if not sb:
        return []
    try:
        q = sb.table("pedidos").select(
            "id, numero, usuario, loja, uf, desconto_pct, total_geral, status, criado_em, enviado_em"
        ).order("numero", desc=True)

        if perfil == "vendedor" and usuario:
            q = q.eq("usuario", usuario)
        elif loja and perfil == "supervisor":
            q = q.eq("loja", loja)

        r = q.execute()
        return r.data or []
    except Exception:
        return []


def buscar_pedido_completo(pedido_id: int) -> dict | None:
    """Busca pedido + itens pelo id."""
    sb = get_supabase()
    if not sb:
        return None
    try:
        rp = sb.table("pedidos").select("*").eq("id", pedido_id).single().execute()
        ri = sb.table("pedido_itens").select("*").eq("pedido_id", pedido_id).execute()
        if rp.data:
            return {**rp.data, "itens": ri.data or []}
        return None
    except Exception:
        return None


def atualizar_status_pedido(pedido_id: int, status: str, usuario: str = "") -> bool:
    """Atualiza o status de um pedido (pendente → enviado → aprovado)."""
    sb = get_supabase()
    if not sb:
        return False
    try:
        upd: dict = {"status": status}
        if status == "enviado":
            from datetime import datetime
            upd["enviado_em"] = datetime.utcnow().isoformat()
        sb.table("pedidos").update(upd).eq("id", pedido_id).execute()
        registrar_auditoria(usuario, f"STATUS_{status.upper()}", f"Pedido id={pedido_id}")
        return True
    except Exception:
        return False


def excluir_pedido(pedido_id: int, usuario: str = "") -> bool:
    sb = get_supabase()
    if not sb:
        return False
    try:
        sb.table("pedidos").delete().eq("id", pedido_id).execute()
        registrar_auditoria(usuario, "PEDIDO_EXCLUIDO", f"Pedido id={pedido_id}")
        return True
    except Exception:
        return False


def atualizar_pedido(
    pedido_id: int,
    uf: str,
    desconto_pct: float,
    total_geral: float,
    itens: list[dict],
    usuario: str = "",
) -> tuple[bool, str]:
    """Atualiza UF, desconto, total e itens de um pedido existente."""
    sb = get_supabase()
    if not sb:
        return False, "Banco de dados indisponível."
    try:
        sb.table("pedidos").update({
            "uf":          uf,
            "desconto_pct": round(float(desconto_pct), 4),
            "total_geral":  round(float(total_geral), 2),
        }).eq("id", pedido_id).execute()

        # Reinsere os itens do pedido
        sb.table("pedido_itens").delete().eq("pedido_id", pedido_id).execute()
        linhas = [
            {
                "pedido_id":  pedido_id,
                "cod_sku":    it.get("cod_sku", ""),
                "cod_citel":  it.get("cod_citel", ""),
                "marca":      it.get("marca", ""),
                "descricao":  it.get("descricao", ""),
                "embalagem":  it.get("embalagem", ""),
                "qtd":        int(it.get("qtd", 0)),
                "preco_unit": float(it.get("preco_unit", 0)),
                "total":      float(it.get("total", 0)),
            }
            for it in itens if int(it.get("qtd", 0)) > 0
        ]
        if linhas:
            sb.table("pedido_itens").insert(linhas).execute()
        registrar_auditoria(usuario, "PEDIDO_EDITADO", f"Pedido id={pedido_id}")
        return True, "Pedido atualizado com sucesso."
    except Exception as e:
        return False, str(e)


# ── Auditoria ────────────────────────────────────────────────────────────────
def registrar_auditoria(usuario: str, acao: str, detalhe: str = "") -> None:
    sb = get_supabase()
    if not sb:
        return
    try:
        sb.table("auditoria").insert({
            "usuario": usuario,
            "acao":    acao,
            "detalhe": detalhe,
        }).execute()
    except Exception:
        pass


@st.cache_data(ttl=60)
def listar_auditoria(limit: int = 100) -> list[dict]:
    sb = get_supabase()
    if not sb:
        return []
    try:
        r = (
            sb.table("auditoria")
            .select("*")
            .order("criado_em", desc=True)
            .limit(limit)
            .execute()
        )
        return r.data or []
    except Exception:
        return []


# ── Catálogo de produtos ──────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def catalogo_disponivel() -> bool:
    """
    Verifica se o catálogo já foi importado para o Supabase.
    Resultado em cache por 1h — evita chamadas HTTP repetidas a cada acesso.
    """
    return get_config("catalogo_no_supabase", "") == "true"


@st.cache_resource
def get_catalogo_uf(uf: str) -> "pd.DataFrame":
    """
    Retorna todos os produtos de uma UF a partir do Supabase.
    Resultado mantido em memória (@cache_resource) — zero latência após 1ª carga.
    Faz paginação automática para contornar o limite de 1000 rows do PostgREST.

    IMPORTANTE: levanta RuntimeError em caso de falha de conexão para que
    @st.cache_resource NÃO armazene o resultado vazio — a próxima chamada retentará.
    """
    import pandas as pd
    sb = get_supabase()
    if not sb:
        raise RuntimeError("Supabase indisponível — cliente não inicializado")

    PAGE = 1000

    # Tenta primeiro com colunas extras; se não existirem, usa só as base
    BASE_COLS  = "linha,cod_sku,descricao,embalagem,cor,preco,cod_citel,descricao_db,marca,grupo,desc_final"
    EXTRA_COLS = "embalagem_db,preco_compra"
    has_extra  = True  # assume que existem; ajusta se der erro 42703

    for attempt in range(2):
        select_cols = f"{BASE_COLS},{EXTRA_COLS}" if has_extra else BASE_COLS
        rows: list[dict] = []
        offset = 0
        _col_missing = False
        while True:
            try:
                r = (
                    sb.table("catalogo")
                    .select(select_cols)
                    .eq("uf", uf)
                    .order("linha")
                    .range(offset, offset + PAGE - 1)
                    .execute()
                )
                batch = r.data or []
                rows.extend(batch)
                if len(batch) < PAGE:
                    break
                offset += PAGE
            except Exception as _exc:
                err_msg = str(_exc)
                if "42703" in err_msg or "does not exist" in err_msg:
                    # Coluna inexistente: tenta de novo sem as extras
                    _col_missing = True
                    break
                if not rows:
                    raise RuntimeError(f"Falha ao carregar catálogo UF={uf}") from _exc
                break  # dados parciais: retorna o que foi obtido

        if _col_missing and has_extra:
            has_extra = False
            continue  # tenta novamente com BASE_COLS apenas
        break  # sucesso (com ou sem extras)

    if not rows:
        return pd.DataFrame()  # UF vazia — resultado legítimo, pode cachear

    df = pd.DataFrame(rows)
    df.rename(columns={
        "linha":        "LINHA",
        "cod_sku":      "COD_SKU",
        "descricao":    "DESCRICAO",
        "embalagem":    "EMBALAGEM",
        "cor":          "COR",
        "preco":        "PRECO",
        "cod_citel":    "COD_CITEL",
        "descricao_db": "DESCRICAO_DB",
        "marca":        "MARCA",
        "grupo":        "GRUPO",
        "desc_final":   "DESC_FINAL",
        "embalagem_db": "EMBALAGEM_DB",
        "preco_compra":  "PRECO_COMPRA",
    }, inplace=True)

    df["PRECO"] = pd.to_numeric(df["PRECO"], errors="coerce").fillna(0.0)
    if "PRECO_COMPRA" in df.columns:
        df["PRECO_COMPRA"] = pd.to_numeric(df["PRECO_COMPRA"], errors="coerce").fillna(0.0)
    else:
        df["PRECO_COMPRA"] = 0.0
    if "EMBALAGEM_DB" not in df.columns:
        df["EMBALAGEM_DB"] = ""
    df["LINHA"] = pd.to_numeric(df["LINHA"], errors="coerce").fillna(0).astype(int)
    for col in ("COD_SKU","DESCRICAO","EMBALAGEM","COR","COD_CITEL","DESCRICAO_DB","MARCA","GRUPO","DESC_FINAL","EMBALAGEM_DB"):
        df[col] = df[col].fillna("").astype(str)

    # UF não vem na query — adiciona
    df["UF"] = uf
    return df


# ── Storage de Excel (persistência entre restarts do container) ───────────────
_STORAGE_BUCKET = "planilhas"
_STORAGE_PATH   = "excel/tabela_ativa.xlsx"


def upload_excel_storage(file_bytes: bytes, filename: str) -> bool:
    """
    Salva o Excel no Supabase Storage (bucket 'planilhas'), path fixo excel/tabela_ativa.xlsx.
    Usa upsert para sobrescrever sempre — garante que apenas um arquivo existe no bucket.
    """
    sb = get_supabase()
    if sb is None:
        return False
    try:
        # Garante que o bucket existe
        try:
            sb.storage.create_bucket(_STORAGE_BUCKET, options={"public": False})
        except Exception:
            pass  # Já existe

        # Upsert: sobrescreve se existir, cria se não existir — sem acumulação de arquivos
        sb.storage.from_(_STORAGE_BUCKET).upload(
            _STORAGE_PATH,
            file_bytes,
            file_options={
                "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "upsert": "true",
            },
        )
        set_config("excel_storage_filename", filename)
        return True
    except Exception:
        return False


def download_excel_storage() -> tuple:
    """
    Baixa o Excel do Supabase Storage.
    Retorna (bytes, filename) ou (None, '') se não disponível.
    """
    sb = get_supabase()
    if sb is None:
        return None, ""
    try:
        filename = get_config("excel_storage_filename", "")
        if not filename:
            return None, ""
        data = sb.storage.from_(_STORAGE_BUCKET).download(_STORAGE_PATH)
        return data, filename
    except Exception:
        return None, ""


# ── Espelho CITEL (fallback quando MySQL não está acessível) ──────────────────
@st.cache_resource(ttl=3600)
def get_citel_itens() -> "pd.DataFrame":
    """
    Retorna todos os registros da tabela citel_itens (espelho do MySQL CITEL).
    Sincronizada pelo GitHub Actions diariamente.
    Cache de 1h — zero latência após primeira carga.
    """
    import pandas as pd
    sb = get_supabase()
    if sb is None:
        return pd.DataFrame(columns=["COD_FAB", "COD_CITEL", "DESCRICAO_DB", "MARCA", "GRUPO"])

    PAGE = 1000
    BASE_COLS  = "cod_fab,cod_citel,descricao_db,marca,grupo"
    EXTRA_COLS = "embalagem_db,preco_compra_rn,preco_compra_ba,preco_compra_pe,preco_compra_al,preco_compra_pb"
    has_extra  = True

    for _attempt in range(2):
        select_cols = f"{BASE_COLS},{EXTRA_COLS}" if has_extra else BASE_COLS
        rows = []
        offset = 0
        _col_missing = False
        while True:
            try:
                r = (
                    sb.table("citel_itens")
                    .select(select_cols)
                    .range(offset, offset + PAGE - 1)
                    .execute()
                )
                batch = r.data or []
                rows.extend(batch)
                if len(batch) < PAGE:
                    break
                offset += PAGE
            except Exception as _exc:
                err_msg = str(_exc)
                if ("42703" in err_msg or "does not exist" in err_msg) and has_extra:
                    _col_missing = True
                    break
                break  # outra falha: sai com o que foi obtido
        if _col_missing:
            has_extra = False
            continue
        break

    if not rows:
        return pd.DataFrame(columns=["COD_FAB", "COD_CITEL", "DESCRICAO_DB", "MARCA", "GRUPO",
                                     "EMBALAGEM_DB", "PRECO_COMPRA_RN", "PRECO_COMPRA_BA",
                                     "PRECO_COMPRA_PE", "PRECO_COMPRA_AL", "PRECO_COMPRA_PB"])

    import pandas as pd
    df = pd.DataFrame(rows)
    df.rename(columns={
        "cod_fab":         "COD_FAB",
        "cod_citel":       "COD_CITEL",
        "descricao_db":    "DESCRICAO_DB",
        "marca":           "MARCA",
        "grupo":           "GRUPO",
        "embalagem_db":    "EMBALAGEM_DB",
        "preco_compra_rn": "PRECO_COMPRA_RN",
        "preco_compra_ba": "PRECO_COMPRA_BA",
        "preco_compra_pe": "PRECO_COMPRA_PE",
        "preco_compra_al": "PRECO_COMPRA_AL",
        "preco_compra_pb": "PRECO_COMPRA_PB",
    }, inplace=True)
    for col in ("COD_FAB", "COD_CITEL", "DESCRICAO_DB", "MARCA", "GRUPO"):
        df[col] = df[col].fillna("").astype(str)
    if "EMBALAGEM_DB" in df.columns:
        df["EMBALAGEM_DB"] = df["EMBALAGEM_DB"].fillna("").astype(str)
    else:
        df["EMBALAGEM_DB"] = ""
    for col in ("PRECO_COMPRA_RN", "PRECO_COMPRA_BA", "PRECO_COMPRA_PE",
                "PRECO_COMPRA_AL", "PRECO_COMPRA_PB"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0
    return df


# ── Código de operador (3 dígitos) na tabela usuarios ────────────────────────
def get_codigo_usuario(login: str) -> str:
    """Retorna o código de 3 dígitos do operador, ou '' se não configurado."""
    sb = get_supabase()
    if not sb:
        return ""
    try:
        r = sb.table("usuarios").select("codigo").eq("usuario", login).single().execute()
        return str(r.data.get("codigo") or "") if r.data else ""
    except Exception:
        return ""


def set_codigo_usuario(login: str, codigo: str) -> tuple[bool, str]:
    """Salva o código de 3 dígitos para o operador. Retorna (ok, mensagem)."""
    sb = get_supabase()
    if not sb:
        return False, "Supabase indisponível"
    try:
        sb.table("usuarios").update({"codigo": codigo.strip()}).eq("usuario", login).execute()
        return True, "Código salvo com sucesso"
    except Exception as e:
        return False, str(e)


def buscar_login_por_codigo(codigo: str) -> str:
    """Retorna o login do operador associado ao código, ou '' se não encontrado.
    Normaliza a entrada para 3 dígitos com zero-padding (ex: '1' → '001').
    """
    if not codigo:
        return ""
    sb = get_supabase()
    if not sb:
        return ""
    # Normaliza: zero-pad até 3 dígitos se for numérico
    codigo_norm = codigo.strip()
    try:
        codigo_norm = str(int(codigo_norm)).zfill(3)
    except ValueError:
        pass  # mantém original se não for numérico
    try:
        r = (
            sb.table("usuarios")
            .select("usuario")
            .eq("codigo", codigo_norm)
            .eq("ativo", True)
            .limit(1)
            .execute()
        )
        return r.data[0]["usuario"] if r.data else ""
    except Exception:
        return ""


def set_precisa_trocar_senha(login: str) -> None:
    """Marca que o usuário deve trocar a senha no próximo login."""
    sb = get_supabase()
    if not sb:
        return
    try:
        sb.table("usuarios").update({"precisa_trocar_senha": True}).eq("usuario", login).execute()
    except Exception:
        pass  # Coluna pode não existir — ignora silenciosamente


# ── Disparo do GitHub Actions sync_citel.yml ─────────────────────────────────
def dispatch_citel_sync(force: bool = False) -> tuple[bool, str]:
    """
    Dispara o workflow sync_citel.yml via GitHub Actions API.
    Requer a secret GITHUB_DISPATCH_TOKEN com permissão actions:write.
    Quando force=True passa SYNC_FORCE=true para pular a detecção de mudanças.
    """
    import os
    import requests

    token = os.environ.get("GITHUB_DISPATCH_TOKEN", "")
    if not token:
        return False, "GITHUB_DISPATCH_TOKEN não configurado"

    payload: dict = {"ref": "main"}
    if force:
        payload["inputs"] = {"force": "true"}

    try:
        r = requests.post(
            "https://api.github.com/repos/toquedecor/toque-de-cor/actions/workflows/sync_citel.yml/dispatches",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            json=payload,
            timeout=10,
        )
    except Exception as e:
        return False, f"Erro de rede: {e}"

    if r.status_code == 204:
        return True, "Sync CITEL disparado com sucesso"
    return False, f"GitHub retornou {r.status_code}"
