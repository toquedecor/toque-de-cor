#!/bin/bash
# ─────────────────────────────────────────────────────────────
# build_mac.sh — Gera o "Toque de Cor.app" no Mac
# Execute UMA VEZ em qualquer Mac com Python 3.11+:
#   bash build_mac.sh
# O .app gerado em dist/ pode ser copiado para qualquer Mac.
# ─────────────────────────────────────────────────────────────
set -e
cd "$(dirname "$0")"

echo "📦 Instalando dependências de build..."
pip install -q pyinstaller
pip install -q -r requirements.txt

# Localiza os dados estáticos do Streamlit (HTML/CSS/JS do servidor)
ST_DIR=$(python3 -c "import streamlit, os; print(os.path.dirname(streamlit.__file__))")

echo "🔨 Compilando .app com PyInstaller..."
pyinstaller \
    --noconfirm \
    --clean \
    --name "Toque de Cor" \
    --onedir \
    --windowed \
    --add-data "$ST_DIR:streamlit" \
    --add-data "app.py:." \
    --add-data "db.py:." \
    --add-data "Tabela SW Suvinil Geral.xlsx:." \
    --add-data "pedidos.json:." \
    --add-data "similares.json:." \
    --hidden-import streamlit \
    --hidden-import streamlit.web.bootstrap \
    --hidden-import streamlit.web.server \
    --hidden-import streamlit.runtime \
    --hidden-import mysql.connector \
    --hidden-import openpyxl \
    --hidden-import altair \
    --collect-all streamlit \
    --collect-all altair \
    --collect-all pydeck \
    launcher.py

echo ""
echo "🔓 Removendo bloqueio de segurança do Mac (Gatekeeper)..."
xattr -rd com.apple.quarantine "dist/Toque de Cor.app" 2>/dev/null || true

echo ""
echo "✅ Pronto! App gerado em: dist/Toque de Cor.app"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  COMO DISTRIBUIR PARA O USUÁRIO LEIGO:"
echo ""
echo "  1. Compacte o app:"
echo "     zip -r 'Toque de Cor.zip' 'dist/Toque de Cor.app'"
echo ""
echo "  2. Envie por AirDrop, pen drive ou e-mail"
echo ""
echo "  3. O usuário descompacta e dá DUPLO CLIQUE no .app"
echo "     → Browser abre automaticamente em http://localhost:8502"
echo "     → Conectado direto ao banco MySQL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
