# ─────────────────────────────────────────────────────────────────────────────
# gerar_mac.ps1 — Gera o "Toque de Cor.app" para Mac via GitHub Actions API
#
# USO:
#   .\gerar_mac.ps1 -Token "ghp_SeuTokenAqui" -Repo "seu_usuario/seu_repositorio"
#
# PRÉ-REQUISITOS:
#   1. Código enviado ao GitHub (repositório privado)
#   2. Personal Access Token com permissão "repo" e "workflow"
#      → https://github.com/settings/tokens/new
# ─────────────────────────────────────────────────────────────────────────────
param(
    [Parameter(Mandatory)][string]$Token,
    [Parameter(Mandatory)][string]$Repo   # ex: "joaosilva/toque-de-cor"
)

$headers = @{
    Authorization = "Bearer $Token"
    Accept        = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}
$base = "https://api.github.com/repos/$Repo"

# ── 1. Dispara o workflow ─────────────────────────────────────────────────────
Write-Host "`n🚀 Disparando build no GitHub Actions..." -ForegroundColor Cyan
Invoke-RestMethod -Uri "$base/actions/workflows/build_mac.yml/dispatches" `
    -Method POST `
    -Headers $headers `
    -ContentType "application/json" `
    -Body '{"ref":"main"}' | Out-Null

Start-Sleep -Seconds 5   # aguarda o GitHub registrar o run

# ── 2. Localiza o run mais recente ────────────────────────────────────────────
$run = $null
for ($i = 0; $i -lt 10; $i++) {
    $runs = Invoke-RestMethod -Uri "$base/actions/runs?per_page=5" -Headers $headers
    $run  = $runs.workflow_runs | Where-Object { $_.name -eq "Gerar App Mac" } | Select-Object -First 1
    if ($run) { break }
    Start-Sleep -Seconds 3
}
if (-not $run) { Write-Error "Não foi possível localizar o run. Tente novamente."; exit 1 }

Write-Host "📋 Run #$($run.run_number) iniciado — aguardando compilação (~5 min)..." -ForegroundColor Yellow

# ── 3. Aguarda conclusão ──────────────────────────────────────────────────────
$dots = 0
do {
    Start-Sleep -Seconds 15
    $run = Invoke-RestMethod -Uri "$base/actions/runs/$($run.id)" -Headers $headers
    Write-Host -NoNewline "."
    $dots++
    if ($dots % 20 -eq 0) { Write-Host "" }
} while ($run.status -ne "completed")

Write-Host ""
if ($run.conclusion -ne "success") {
    Write-Host "❌ Build falhou ($($run.conclusion)). Veja: $($run.html_url)" -ForegroundColor Red
    exit 1
}
Write-Host "✅ Build concluído com sucesso!" -ForegroundColor Green

# ── 4. Baixa o artefato ───────────────────────────────────────────────────────
$artifacts = Invoke-RestMethod -Uri "$base/actions/runs/$($run.id)/artifacts" -Headers $headers
$artifact  = $artifacts.artifacts | Where-Object { $_.name -eq "Toque-de-Cor-Mac" } | Select-Object -First 1

if (-not $artifact) { Write-Error "Artefato não encontrado."; exit 1 }

$destZip = "$PSScriptRoot\Toque-de-Cor-Mac.zip"
Write-Host "📥 Baixando $($artifact.name)..." -ForegroundColor Cyan

Invoke-RestMethod `
    -Uri "$base/actions/artifacts/$($artifact.id)/zip" `
    -Headers $headers `
    -OutFile $destZip

Write-Host @"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✅ Arquivo salvo em:
     $destZip

  COMO ENVIAR AO USUÁRIO MAC:
  1. Envie o Toque-de-Cor-Mac.zip por e-mail ou AirDrop
  2. Usuário descompacta e dá DUPLO CLIQUE em "Toque de Cor.app"
  3. Browser abre automaticamente → conectado ao banco MySQL ✅
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"@ -ForegroundColor Green
