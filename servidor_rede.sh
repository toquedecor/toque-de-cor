#!/bin/bash
# ─────────────────────────────────────────────────────────────
# servidor_rede.sh — Expõe o app na rede local (LAN)
# Execute no Windows (WSL) ou Mac que tem Python instalado.
# Os outros Macs da rede acessam pelo browser sem instalar nada.
# ─────────────────────────────────────────────────────────────
cd "$(dirname "$0")"

# Descobre o IP local da máquina
IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')

echo "🌐 Iniciando servidor na rede local..."
echo "   Acesse de qualquer Mac/computador da rede:"
echo "   👉  http://${IP}:8502"
echo ""

source .venv/bin/activate 2>/dev/null || true

streamlit run app.py \
    --server.port 8502 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false
