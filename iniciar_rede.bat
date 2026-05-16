@echo off
chcp 65001 > nul
title Toque de Cor - Servidor de Rede

echo.
echo  ================================================
echo   Toque de Cor - Simulador de Pedidos
echo   MODO REDE - acesso pelo Mac / outros PCs
echo  ================================================
echo.

:: Verificar Python
python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado. Instale o Python em https://python.org
    pause
    exit /b 1
)

:: Instalar dependências
echo Verificando dependencias...
pip install -r requirements.txt -q

:: Descobrir IP local da máquina Windows
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    set IP=%%a
    goto :found
)
:found
set IP=%IP: =%

echo.
echo  ================================================
echo.
echo   App rodando! Abra o navegador nos Macs e
echo   acesse o endereco abaixo:
echo.
echo   http://%IP%:8502
echo.
echo   (todos na mesma rede Wi-Fi / cabo)
echo.
echo  ================================================
echo.
echo  Pressione CTRL+C para encerrar o servidor.
echo.

streamlit run app.py ^
    --server.port 8502 ^
    --server.address 0.0.0.0 ^
    --server.headless true ^
    --browser.gatherUsageStats false

pause
