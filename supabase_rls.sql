-- ============================================================
-- Toque de Cor — Políticas de acesso (RLS)
-- Execute no SQL Editor do Supabase após o supabase_init.sql
-- ============================================================
-- CONTEXTO: O app Streamlit roda no servidor e gerencia sua
-- própria autenticação. Por isso, desabilitamos RLS nas tabelas
-- internas e deixamos o controle de acesso para o app.
-- ============================================================

-- Desabilita RLS nas tabelas do sistema
ALTER TABLE usuarios     DISABLE ROW LEVEL SECURITY;
ALTER TABLE pedidos      DISABLE ROW LEVEL SECURITY;
ALTER TABLE pedido_itens DISABLE ROW LEVEL SECURITY;
ALTER TABLE configuracoes DISABLE ROW LEVEL SECURITY;
ALTER TABLE auditoria    DISABLE ROW LEVEL SECURITY;

-- Garante permissões completas para a role anon (usada pelo app)
GRANT ALL ON usuarios      TO anon;
GRANT ALL ON pedidos       TO anon;
GRANT ALL ON pedido_itens  TO anon;
GRANT ALL ON configuracoes TO anon;
GRANT ALL ON auditoria     TO anon;

-- Garante acesso às sequences (necessário para SERIAL/auto-increment)
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon;
