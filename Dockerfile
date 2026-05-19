FROM python:3.11-slim

# Hugging Face Spaces requires non-root user with UID 1000
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código da aplicação
COPY --chown=appuser:appuser . .

USER appuser

# Porta obrigatória no Hugging Face Spaces
EXPOSE 7860

# Iniciar app
CMD ["streamlit", "run", "app_web.py", \
     "--server.port=7860", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
