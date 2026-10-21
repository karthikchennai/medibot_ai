$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'logs\processes.json'
if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    Write-Host 'No saved MediBot processes were found.'
    return
}

try { $record = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json }
catch { throw 'Could not read logs/processes.json. No processes were stopped.' }
$taskkillPath = Join-Path $env:SystemRoot 'System32\taskkill.exe'
$stopFailed = $false

foreach ($serviceName in @('frontend', 'backend')) {
    $entry = $record.$serviceName
    if ($null -eq $entry) { continue }
    $processId = 0
    $startTicks = [long]0
    if ($null -eq $entry.id -or $null -eq $entry.start_time_utc_ticks -or
        -not [int]::TryParse([string]$entry.id, [ref]$processId) -or $processId -le 0 -or
        -not [long]::TryParse([string]$entry.start_time_utc_ticks, [ref]$startTicks)) {
        Write-Warning "Saved $serviceName identity is incomplete. It has been left untouched; stop any legacy instance manually."
        $stopFailed = $true
        continue
    }

    try {
        $processInfo = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -eq $processInfo) { continue }
        if ($processInfo.StartTime.ToUniversalTime().Ticks -ne $startTicks) {
            Write-Warning "PID $processId now belongs to another process. It has been left untouched."
            continue
        }
        # Check the recorded identity before terminating its Next.js/uvicorn process tree.
        $killOutput = & $taskkillPath /PID $processId /T /F 2>&1
        if ($LASTEXITCODE -ne 0) {
            $remainingProcess = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($remainingProcess -and $remainingProcess.StartTime.ToUniversalTime().Ticks -eq $startTicks) {
                throw ($killOutput | Out-String).Trim()
            }
        }
        Write-Host "Stopped MediBot $serviceName (PID $processId)."
    } catch {
        $stopFailed = $true
        Write-Warning "Could not stop $serviceName (PID $($processId)): $($_.Exception.Message)"
    }
}

if ($stopFailed) {
    Write-Warning 'Some saved processes could not be verified or stopped. The process record was kept for inspection.'
} else {
    Remove-Item -LiteralPath $pidFile
    Write-Host 'MediBot services are stopped.'
}
