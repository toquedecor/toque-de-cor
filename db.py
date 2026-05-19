"""
Módulo de conexão com o banco de dados MySQL (AUTCOM / CITEL).

Otimizações de performance:
  1. Conexão persistente via st.cache_resource (sem custo de reconexão a cada render)
  2. Cache em disco (pickle) com TTL de 1 hora — sobrevive a reinicializações do app
  3. Cache incremental: consulta o BD apenas para SKUs ausentes no cache existente
"""

import pandas as pd
import pickle
import os as _os
import streamlit as st
from pathlib import Path
from datetime import datetime

try:
    import mysql.connector
    _MYSQL_OK = True
except Exception:
    _MYSQL_OK = False

_DB_CONFIG = {
    "host":               _os.environ.get("MYSQL_HOST",     ""),
    "user":               _os.environ.get("MYSQL_USER",     ""),
    "password":           _os.environ.get("MYSQL_PASSWORD", ""),
    "port":               int(_os.environ.get("MYSQL_PORT", "3306")),
    "database":           _os.environ.get("MYSQL_DATABASE", ""),
    "connection_timeout": 10,
    "connect_timeout":    10,
}

_EMPTY      = pd.DataFrame(columns=["COD_FAB", "COD_CITEL", "DESCRICAO_DB", "MARCA", "GRUPO"])
# Cache gravável: usa DATA_DIR do launcher quando rodando como .app
import os as _os
_data_dir   = Path(_os.environ.get("TOQUEDECOR_DATA_DIR", "") or Path(__file__).parent)
_CACHE_PATH = _data_dir / ".db_cache.pkl"
_CACHE_TTL  = 3600  # 1 hora


# ── Conexão persistente ────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _get_conn():
    """
    Conexão MySQL mantida viva entre reruns pelo st.cache_resource.
    Elimina o custo de handshake TCP+auth a cada render.
    """
    if not _MYSQL_OK:
        raise RuntimeError("mysql-connector-python não está disponível")
    return mysql.connector.connect(**_DB_CONFIG)


def _run(sql: str, params: list) -> list:
    """Executa SQL com reconexão automática em caso de timeout do servidor."""
    for _ in range(2):
        try:
            conn = _get_conn()
            if not conn.is_connected():
                conn.reconnect(attempts=2, delay=1)
            cur = conn.cursor(dictionary=True)
            cur.execute(sql, params)
            rows = cur.fetchall()
            cur.close()
            return rows
        except mysql.connector.Error:
            _get_conn.clear()   # força nova conexão na próxima tentativa
    return []


# ── Cache em disco ─────────────────────────────────────────────────────────────
def _disk_load() -> tuple:
    """
    Carrega cache do disco.
    Retorna (DataFrame, set_de_skus) se válido, (None, None) se expirado/ausente.
    """
    if not _CACHE_PATH.exists():
        return None, None
    try:
        payload = pickle.loads(_CACHE_PATH.read_bytes())
        age = (datetime.now() - payload["ts"]).total_seconds()
        if age < _CACHE_TTL:
            return payload["df"], payload["skus"]
    except Exception:
        pass
    return None, None


def _disk_save(df: pd.DataFrame, skus: set) -> None:
    try:
        _CACHE_PATH.write_bytes(
            pickle.dumps({"ts": datetime.now(), "df": df.copy(), "skus": skus})
        )
    except Exception:
        pass


def clear_disk_cache() -> None:
    """Remove o cache de disco forçando re-consulta ao BD."""
    _CACHE_PATH.unlink(missing_ok=True)


# ── Query ──────────────────────────────────────────────────────────────────────
def _fetch(skus_list: list) -> pd.DataFrame:
    """Consulta direta ao BD para uma lista de SKUs."""
    if not skus_list:
        return _EMPTY.copy()
    ph  = ", ".join(["%s"] * len(skus_list))
    sql = f"""
        SELECT
            CAST(c.ITE_CODFAB AS CHAR)    AS COD_FAB,
            c.ITE_CODITE                  AS COD_CITEL,
            c.ITE_DESITE                  AS DESCRICAO_DB,
            COALESCE(m.MAR_DESMAR, '')    AS MARCA,
            COALESCE(g.GRU_DESGRU, '')    AS GRUPO
        FROM CADITE c
        LEFT JOIN CADMAR m
            ON c.ITE_CODMAR = m.MAR_CODMAR
        LEFT JOIN CADGRU g
            ON c.ITE_CODGRU = g.GRU_CODGRU
        WHERE CAST(c.ITE_CODFAB AS CHAR) IN ({ph})
    """
    rows = _run(sql, [str(s) for s in skus_list])
    if not rows:
        return _EMPTY.copy()
    df = pd.DataFrame(rows)
    df["COD_FAB"] = df["COD_FAB"].astype(str).str.strip()
    if "GRUPO" not in df.columns:
        df["GRUPO"] = ""
    return df.drop_duplicates("COD_FAB", keep="first")


# ── API pública ────────────────────────────────────────────────────────────────
def test_connection() -> tuple:
    """Testa a conexão com o BD. Retorna (bool, mensagem)."""
    if not _MYSQL_OK:
        return False, "mysql-connector-python não instalado"
    try:
        conn = _get_conn()
        if not conn.is_connected():
            conn.reconnect(attempts=1, delay=0)
        return True, "Banco de dados conectado."
    except Exception as exc:
        return False, str(exc)


def query_items(skus: list) -> pd.DataFrame:
    """
    Retorna COD_CITEL, DESCRICAO_DB e MARCA para a lista de SKUs.

    Estratégia de 3 camadas:
      1. Cache em disco  → sem consulta remota (rápido mesmo após reiniciar o app)
      2. Cache incremental → busca apenas SKUs ausentes (evita re-query completa)
      3. Consulta BD completa → somente na primeira execução ou após expiração
    """
    if not skus:
        return _EMPTY.copy()

    skus_set = {str(s).strip() for s in skus if str(s).strip()}

    cached_df, cached_skus = _disk_load()

    if cached_df is not None:
        missing = skus_set - cached_skus
        if not missing:
            # Cache completo: retorna filtrando pelos SKUs solicitados
            return cached_df[cached_df["COD_FAB"].isin(skus_set)].reset_index(drop=True)

        # Cache parcial: busca apenas os SKUs ausentes
        new_df = _fetch(list(missing))
        full   = pd.concat([cached_df, new_df], ignore_index=True)
        full   = full.drop_duplicates("COD_FAB", keep="first")
        _disk_save(full, cached_skus | missing)
        return full[full["COD_FAB"].isin(skus_set)].reset_index(drop=True)

    # Sem cache: consulta tudo de uma vez
    df = _fetch(list(skus_set))
    if not df.empty:
        _disk_save(df, skus_set)
    return df


def get_cached_data() -> "pd.DataFrame | None":
    """
    Retorna o DataFrame do cache em disco se ainda válido, sem consultar o BD.
    Usado pelo get_db_data de app.py para evitar leitura do Excel no fast path.
    """
    df, _ = _disk_load()
    return df
