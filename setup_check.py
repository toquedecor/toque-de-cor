"""
Script de verificação e setup pós-criação das tabelas Supabase.
Execute após rodar o supabase_init.sql no painel do Supabase.

Uso: python setup_check.py
"""

from dotenv import load_dotenv
load_dotenv()

import os
import sys

print("=" * 60)
print("  Toque de Cor — Verificação do Supabase")
print("=" * 60)

# 1. Verifica variáveis de ambiente
url = os.environ.get("SUPABASE_URL", "")
key = os.environ.get("SUPABASE_KEY", "")
if not url or not key:
    print("\n❌ SUPABASE_URL ou SUPABASE_KEY não encontrados no .env")
    sys.exit(1)
print(f"\n✅ URL:  {url}")
print(f"✅ Key:  {key[:40]}...")

# 2. Testa conexão
from supabase import create_client
sb = create_client(url, key)

tabelas = ["usuarios", "pedidos", "pedido_itens", "configuracoes", "auditoria"]
print("\n--- Verificando tabelas ---")
todas_ok = True
for t in tabelas:
    try:
        sb.table(t).select("*").limit(1).execute()
        print(f"  ✅ {t}")
    except Exception as e:
        print(f"  ❌ {t} — {e}")
        todas_ok = False

if not todas_ok:
    print("\n⚠️  Execute o supabase_init.sql no painel do Supabase primeiro.")
    sys.exit(1)

# 3. Verifica usuário admin
print("\n--- Verificando usuário admin ---")
r = sb.table("usuarios").select("usuario,perfil,ativo").eq("usuario", "admin").execute()
if r.data:
    u = r.data[0]
    print(f"  ✅ admin encontrado — perfil: {u['perfil']} | ativo: {u['ativo']}")
else:
    print("  ⚠️  Usuário admin não encontrado. Criando...")
    import bcrypt
    senha_hash = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode()
    sb.table("usuarios").insert({
        "usuario": "admin",
        "nome":    "Administrador",
        "senha":   senha_hash,
        "perfil":  "admin",
        "loja":    "Matriz",
        "ativo":   True,
    }).execute()
    print("  ✅ Usuário admin criado com senha: admin123")

# 4. Verifica configurações
print("\n--- Configurações iniciais ---")
cfgs = [
    ("pedido_aprovacao", "false"),
    ("session_hours",    "8"),
]
for chave, padrao in cfgs:
    r2 = sb.table("configuracoes").select("valor").eq("chave", chave).execute()
    if not r2.data:
        sb.table("configuracoes").insert({"chave": chave, "valor": padrao}).execute()
        print(f"  ✅ {chave} = {padrao} (inserido)")
    else:
        print(f"  ✅ {chave} = {r2.data[0]['valor']}")

print("\n" + "=" * 60)
print("  ✅ Tudo OK! Inicie o app com:")
print("     streamlit run app_web.py")
print("=" * 60)
