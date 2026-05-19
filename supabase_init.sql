-- ============================================================
-- Toque de Cor — Script de inicialização do banco Supabase
-- Execute uma única vez no SQL Editor do Supabase:
--   https://supabase.com/dashboard → SQL Editor → New query
-- ============================================================

-- 1. Usuários do sistema
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

-- 2. Pedidos
CREATE TABLE IF NOT EXISTS pedidos (
    id           SERIAL PRIMARY KEY,
    numero       INTEGER NOT NULL,
    usuario      TEXT NOT NULL,
    loja         TEXT NOT NULL DEFAULT '',
    uf           TEXT NOT NULL,
    desconto_pct NUMERIC(6,2)  DEFAULT 0,
    total_geral  NUMERIC(12,2) DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pendente',
    criado_em    TIMESTAMPTZ DEFAULT NOW(),
    enviado_em   TIMESTAMPTZ
);

-- 3. Itens dos pedidos
CREATE TABLE IF NOT EXISTS pedido_itens (
    id         SERIAL PRIMARY KEY,
    pedido_id  INTEGER REFERENCES pedidos(id) ON DELETE CASCADE,
    cod_sku    TEXT,
    cod_citel  TEXT,
    marca      TEXT,
    descricao  TEXT,
    embalagem  TEXT,
    qtd        INTEGER       NOT NULL DEFAULT 0,
    preco_unit NUMERIC(12,2) DEFAULT 0,
    total      NUMERIC(12,2) DEFAULT 0
);

-- 4. Configurações do sistema (chave-valor)
CREATE TABLE IF NOT EXISTS configuracoes (
    chave TEXT PRIMARY KEY,
    valor TEXT
);

-- 5. Log de auditoria
CREATE TABLE IF NOT EXISTS auditoria (
    id        SERIAL PRIMARY KEY,
    usuario   TEXT,
    acao      TEXT,
    detalhe   TEXT,
    criado_em TIMESTAMPTZ DEFAULT NOW()
);

-- 6. Usuário administrador inicial
-- IMPORTANTE: troque a senha antes de colocar em produção!
-- A senha abaixo é 'admin123' com bcrypt (gerada pelo auth.py)
INSERT INTO usuarios (usuario, nome, senha, perfil, loja, ativo)
VALUES (
    'admin',
    'Administrador',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMUBJKfHFNtF.ZIQoGBP0XT0H2',
    'admin',
    'Matriz',
    TRUE
)
ON CONFLICT (usuario) DO NOTHING;

-- ============================================================
-- Índices para performance
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_pedidos_usuario  ON pedidos(usuario);
CREATE INDEX IF NOT EXISTS idx_pedidos_loja     ON pedidos(loja);
CREATE INDEX IF NOT EXISTS idx_pedidos_status   ON pedidos(status);
CREATE INDEX IF NOT EXISTS idx_pedido_itens_pid ON pedido_itens(pedido_id);
CREATE INDEX IF NOT EXISTS idx_auditoria_usr    ON auditoria(usuario);
