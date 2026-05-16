@echo off
chcp 65001 > nul
title Toque de Cor - Simulador de Pedidos

echo.
echo  ================================================
echo   Toque de Cor - Simulador de Pedidos
echo  ================================================
echo.

:: Verificar se Python está instalado
python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado. Instale o Python em https://python.org
    pause
    exit /b 1
)

:: Instalar / atualizar dependências silenciosamente
echo Verificando dependencias...
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [ERRO] Falha ao instalar dependencias.
    pause
    exit /b 1
)

echo.
echo Iniciando aplicacao...
echo O navegador abrira automaticamente em http://localhost:8501
echo Para encerrar, feche esta janela ou pressione Ctrl+C
echo.

streamlit run app.py --server.headless false --browser.gatherUsageStats false

pause
