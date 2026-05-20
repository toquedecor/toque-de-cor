# agendar_sync_citel.ps1
# Cria uma tarefa agendada no Windows Task Scheduler para sincronizar
# o MySQL CITEL → Supabase a cada hora, rodando localmente onde o
# servidor CITEL é acessível (rede privada).
#
# Execute UMA VEZ como Administrador para registrar a tarefa:
#   Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
#   .\agendar_sync_citel.ps1

$ErrorActionPreference = "Stop"

# ── Caminhos ─────────────────────────────────────────────────────────────────
$scriptDir  = $PSScriptRoot
$pythonExe  = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonExe) {
    Write-Error "Python não encontrado no PATH. Instale o Python e tente novamente."
    exit 1
}
$syncScript = Join-Path $scriptDir "sync_citel_supabase.py"
if (-not (Test-Path $syncScript)) {
    Write-Error "sync_citel_supabase.py não encontrado em $scriptDir"
    exit 1
}

# ── Variáveis de ambiente para o MySQL CITEL e Supabase ──────────────────────
# Ajuste aqui se as credenciais mudarem
$envVars = @{
    MYSQL_HOST     = "SRVORACLEBR18.CITELSOFTWARE.COM.BR"
    MYSQL_PORT     = "61670"
    MYSQL_USER     = "converte_toquedecor"
    MYSQL_PASSWORD = "converte13347"
    MYSQL_DATABASE = "AUTCOM"
    SUPABASE_URL   = "https://hevhowwfweobmihzvenf.supabase.co"
    SUPABASE_KEY   = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imhldmhvd3dmd2VvYm1paHp2ZW5mIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkxNDc3ODUsImV4cCI6MjA5NDcyMzc4NX0.DNFSm2ZK1mimRPR_rxc48JJRNr8CSSw3jAtKQcCXtpY"
}

# ── Monta string de envs para o comando (SET var=value && ...)  ───────────────
$setEnvs = ($envVars.GetEnumerator() | ForEach-Object { "SET $($_.Key)=$($_.Value)" }) -join " && "
$argument = "/C $setEnvs && `"$pythonExe`" `"$syncScript`" 2>&1 >> `"$scriptDir\sync_citel.log`""

# ── Nome da tarefa ────────────────────────────────────────────────────────────
$taskName = "ToqueDeCor_SyncCITEL"

# ── Remove tarefa existente (se houver) ──────────────────────────────────────
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Tarefa existente removida."
}

# ── Cria a ação ──────────────────────────────────────────────────────────────
$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $argument -WorkingDirectory $scriptDir

# ── Cria o gatilho: a cada 1 hora, início agora, por 10 anos ─────────────────
$trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Hours 1) `
                                    -Once -At (Get-Date).AddMinutes(2) `
                                    -RepetitionDuration (New-TimeSpan -Days 3650)

# ── Configurações ─────────────────────────────────────────────────────────────
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -WakeToRun:$false

# ── Registra a tarefa com o usuário atual (sem senha, roda em background) ────
Register-ScheduledTask `
    -TaskName  $taskName `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -RunLevel  Highest `
    -Force | Out-Null

Write-Host ""
Write-Host "✅ Tarefa '$taskName' registrada com sucesso!"
Write-Host "   Executará 'sync_citel_supabase.py' a cada 1 hora."
Write-Host "   Log em: $scriptDir\sync_citel.log"
Write-Host ""
Write-Host "Para executar agora manualmente:"
Write-Host "   Start-ScheduledTask -TaskName '$taskName'"
Write-Host ""
Write-Host "Para remover:"
Write-Host "   Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
