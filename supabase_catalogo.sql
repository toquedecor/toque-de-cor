-- ============================================================
-- Toque de Cor — Tabela de catálogo de produtos
-- Execute no SQL Editor do Supabase
-- ============================================================

CREATE TABLE IF NOT EXISTS catalogo (
    id            SERIAL PRIMARY KEY,
    uf            TEXT NOT NULL,
    linha         INTEGER NOT NULL,
    cod_sku       TEXT NOT NULL,
    descricao     TEXT,
    embalagem     TEXT,
    embalagem_db  TEXT DEFAULT '',
    cor           TEXT,
    preco         NUMERIC(12,4) DEFAULT 0,
    preco_compra  NUMERIC(12,4) DEFAULT 0,
    cod_citel     TEXT DEFAULT '',
    descricao_db  TEXT DEFAULT '',
    marca         TEXT DEFAULT '',
    grupo         TEXT DEFAULT '',
    desc_final    TEXT DEFAULT '',
    atualizado_em TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (uf, cod_sku)
);

-- Índices para performance
CREATE INDEX IF NOT EXISTS idx_catalogo_uf       ON catalogo(uf);
CREATE INDEX IF NOT EXISTS idx_catalogo_sku      ON catalogo(cod_sku);
CREATE INDEX IF NOT EXISTS idx_catalogo_uf_linha ON catalogo(uf, linha);
CREATE INDEX IF NOT EXISTS idx_catalogo_marca    ON catalogo(marca);
CREATE INDEX IF NOT EXISTS idx_catalogo_grupo    ON catalogo(grupo);

-- Permissão para a role anon
GRANT ALL ON catalogo TO anon;
GRANT USAGE, SELECT ON SEQUENCE catalogo_id_seq TO anon;
