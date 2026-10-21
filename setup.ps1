param([switch]$SkipIngestion)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { python -m venv .venv }
$requirementsPath = 'backend/requirements.txt'
foreach ($lockPath in @('backend/requirements.lock.txt', 'backend/requirements.lock')) {
    if (Test-Path -LiteralPath $lockPath -PathType Leaf) {
        $requirementsPath = $lockPath
        break
    }
}
& '.\.venv\Scripts\python.exe' -m pip install -r $requirementsPath
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
Push-Location frontend
try {
    npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
} finally { Pop-Location }
if (-not $SkipIngestion) {
    Push-Location backend
    try {
        & '..\.venv\Scripts\python.exe' -m medibot.ingestion
        if ($LASTEXITCODE -ne 0) { throw 'Document ingestion failed.' }
    } finally { Pop-Location }
}
Write-Host 'Setup complete. Add GROQ_API_KEY to .env, then run start.ps1.'
