#!/bin/bash
# Inicializador do Simulador Toque de Cor — Mac/Linux
# Uso: bash iniciar.sh

cd "$(dirname "$0")"

# Verifica Python 3
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 não encontrado."
    echo "   Instale via Homebrew: brew install python"
    exit 1
fi

# Cria e/ou ativa ambiente virtual (.venv) na primeira vez
if [ ! -d ".venv" ]; then
    echo "🔧 Criando ambiente virtual..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# Instala/atualiza dependências
echo "📦 Verificando dependências..."
pip install -q -r requirements.txt

# Inicia o app
echo "🚀 Iniciando Simulador em http://localhost:8502"
streamlit run app.py --server.port 8502
