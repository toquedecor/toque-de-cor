FROM python:3.11-slim

WORKDIR /app

# Dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Dependências Python
COPY requirements_web.txt .
RUN pip install --no-cache-dir -r requirements_web.txt

# Código da aplicação
COPY . .

# Porta do Streamlit
EXPOSE 8501

# Health check
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Iniciar app
ENTRYPOINT ["streamlit", "run", "app_web.py", \
            "--server.port=8501", \
            "--server.address=0.0.0.0", \
            "--server.headless=true", \
            "--browser.gatherUsageStats=false"]
