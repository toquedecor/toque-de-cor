# ─────────────────────────────────────────────────────────────────────────────
# configurar_secrets_hf.ps1
# Configura os secrets do HuggingFace Space (Docker) para o Supabase funcionar.
#
# Uso: clique com botão direito → "Executar com PowerShell"
# ─────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"
$scriptDir = $PSScriptRoot
$HF_SPACE  = "simuladorpedidos/toquedecor"

Write-Host ""
Write-Host "Configurando secrets no HF Space: $HF_SPACE" -ForegroundColor Cyan
Write-Host ""

$hfToken = $env:HF_TOKEN
if (-not $hfToken) {
    $hfToken = Read-Host "Cole seu HuggingFace token (hf_...)"
}
if (-not $hfToken) {
    Write-Error "Token HF obrigatorio."
}

$pyScript = @"
import os, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(r'$scriptDir') / '.env')

try:
    from huggingface_hub import HfApi
except ImportError:
    print('ERRO: pip install huggingface_hub')
    sys.exit(1)

repo_id = r'$HF_SPACE'
api = HfApi(token=os.environ['HF_TOKEN'])

keys = {
    'SUPABASE_URL': os.environ.get('SUPABASE_URL', ''),
    'SUPABASE_KEY': os.environ.get('SUPABASE_KEY', ''),
    'SESSION_HOURS': os.environ.get('SESSION_HOURS', '8'),
    'MYSQL_HOST': os.environ.get('MYSQL_HOST', ''),
    'MYSQL_USER': os.environ.get('MYSQL_USER', ''),
    'MYSQL_PASSWORD': os.environ.get('MYSQL_PASSWORD', ''),
    'MYSQL_PORT': os.environ.get('MYSQL_PORT', ''),
    'MYSQL_DATABASE': os.environ.get('MYSQL_DATABASE', ''),
}

missing = [k for k, v in keys.items() if k.startswith('SUPABASE') and not v]
if missing:
    print('ERRO: faltam no .env:', ', '.join(missing))
    sys.exit(1)

try:
    info = api.space_info(repo_id)
    print('Space encontrado:', repo_id, '| sdk:', info.sdk)
except Exception as e:
    print('ERRO: Space nao encontrado:', e)
    sys.exit(1)

for key, value in keys.items():
    if not value:
        continue
    api.add_space_secret(repo_id=repo_id, key=key, value=value)
    print('OK', key)

print('DONE')
"@

$tmpPy = Join-Path $env:TEMP "config_hf_secrets.py"
$pyScript | Out-File -FilePath $tmpPy -Encoding utf8
$env:HF_TOKEN = $hfToken.Trim()

python $tmpPy
$code = $LASTEXITCODE
Remove-Item $tmpPy -ErrorAction SilentlyContinue
Remove-Item Env:HF_TOKEN -ErrorAction SilentlyContinue

if ($code -ne 0) {
    Write-Host ""
    Write-Host "Falhou. Configure manualmente em:" -ForegroundColor Red
    Write-Host "  https://huggingface.co/spaces/$HF_SPACE/settings" -ForegroundColor Gray
    Read-Host "Pressione Enter para fechar"
    exit 1
}

Write-Host ""
Write-Host "Secrets configurados! O Space reinicia em ~1-2 min." -ForegroundColor Green
Write-Host "App: https://simuladorpedidos-toquedecor.hf.space/" -ForegroundColor Gray
Write-Host ""
Read-Host "Pressione Enter para fechar"
