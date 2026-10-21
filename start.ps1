param([string]$NodePath = '')

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$backendDirectory = Join-Path $projectRoot 'backend'
$frontendDirectory = Join-Path $projectRoot 'frontend'
$nextPath = Join-Path $frontendDirectory 'node_modules\next\dist\bin\next'
$logDirectory = Join-Path $projectRoot 'logs'
$pidFile = Join-Path $logDirectory 'processes.json'

function Assert-FreePort {
    param([int]$Port)
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.ExclusiveAddressUse = $true
        $listener.Start()
    } catch {
        throw "Port $Port is unavailable. Stop the service using it, then run start.ps1 again. $($_.Exception.Message)"
    } finally {
        $listener.Stop()
    }
}

function Get-ProcessIdentity {
    param([System.Diagnostics.Process]$Process)
    $Process.Refresh()
    if ($Process.HasExited) { throw "A MediBot service exited during startup. Inspect logs in $logDirectory." }
    return @{
        id = $Process.Id
        start_time_utc_ticks = [string]$Process.StartTime.ToUniversalTime().Ticks
    }
}

function Stop-LaunchedProcess {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process) { return }
    try {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            # This process object belongs to the current launch.
            $taskkillPath = Join-Path $env:SystemRoot 'System32\taskkill.exe'
            & $taskkillPath /PID $Process.Id /T /F 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { Stop-Process -InputObject $Process -Force -ErrorAction SilentlyContinue }
        }
    } catch { Write-Warning "Could not completely clean up process $($Process.Id): $($_.Exception.Message)" }
}

# Complete all dependency and port checks before starting either service.
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Python environment is missing. Run setup.ps1 first.' }
if (-not (Test-Path -LiteralPath (Join-Path $backendDirectory 'medibot\main.py') -PathType Leaf)) { throw 'The backend application is missing.' }
if (-not (Test-Path -LiteralPath $nextPath -PathType Leaf)) { throw 'Next.js is missing. Run setup.ps1 first.' }
if ([string]::IsNullOrWhiteSpace($NodePath)) {
    $nodeCommand = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $nodeCommand) { throw 'Node.js was not found. Install Node.js 20.9 or later, or pass -NodePath to start.ps1.' }
    $NodePath = $nodeCommand.Source
}
if (-not (Test-Path -LiteralPath $NodePath -PathType Leaf)) { throw "Node.js executable does not exist: $NodePath" }
$nodeVersionText = (& $NodePath --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $nodeVersionText -notmatch '^v(\d+\.\d+\.\d+)') { throw 'The Node.js executable did not return a valid version.' }
if ([version]$Matches[1] -lt [version]'20.9.0') { throw "Next.js requires Node.js 20.9 or later; found $nodeVersionText." }
& $pythonPath -c 'import uvicorn' 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Backend dependencies are missing. Run setup.ps1 first.' }

if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
    try { $previousRecord = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json }
    catch { throw 'logs/processes.json is unreadable. Inspect it before starting another instance.' }
    foreach ($serviceName in @('backend', 'frontend')) {
        $entry = $previousRecord.$serviceName
        if ($null -eq $entry -or $null -eq $entry.id -or $null -eq $entry.start_time_utc_ticks) { continue }
        $running = Get-Process -Id ([int]$entry.id) -ErrorAction SilentlyContinue
        if ($running -and [string]$running.StartTime.ToUniversalTime().Ticks -eq [string]$entry.start_time_utc_ticks) {
            throw "MediBot $serviceName is already running (PID $($entry.id)). Run stop.ps1 first."
        }
    }
}
Assert-FreePort -Port 8000
Assert-FreePort -Port 3000

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$frontendMode = if (Test-Path -LiteralPath (Join-Path $frontendDirectory '.next\BUILD_ID') -PathType Leaf) { 'start' } else { 'dev' }
$backendProcess = $null
$frontendProcess = $null
$recordWritten = $false
try {
    $backendProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'uvicorn', 'medibot.main:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $backendDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'backend.log') -RedirectStandardError (Join-Path $logDirectory 'backend-error.log')
    $backendIdentity = Get-ProcessIdentity -Process $backendProcess
    $frontendProcess = Start-Process -FilePath $NodePath -ArgumentList @(('"' + $nextPath + '"'), $frontendMode, '--hostname', '127.0.0.1', '--port', '3000') -WorkingDirectory $frontendDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'frontend.log') -RedirectStandardError (Join-Path $logDirectory 'frontend-error.log')
    $frontendIdentity = Get-ProcessIdentity -Process $frontendProcess
    @{
        backend = $backendIdentity
        frontend = $frontendIdentity
        frontend_mode = $frontendMode
        started_at_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $pidFile -Encoding UTF8
    $recordWritten = $true

    # Catch immediate import errors, occupied ports, and invalid production builds.
    Start-Sleep -Seconds 2
    $null = Get-ProcessIdentity -Process $backendProcess
    $null = Get-ProcessIdentity -Process $frontendProcess
} catch {
    Stop-LaunchedProcess -Process $frontendProcess
    Stop-LaunchedProcess -Process $backendProcess
    if ($recordWritten -and (Test-Path -LiteralPath $pidFile)) { Remove-Item -LiteralPath $pidFile }
    throw "MediBot failed to start. Check logs/backend-error.log and logs/frontend-error.log. $($_.Exception.Message)"
}

Write-Host "MediBot started in frontend '$frontendMode' mode. Services may take a moment to become ready."
Write-Host 'MediBot: http://localhost:3000  |  API docs: http://localhost:8000/docs'
Write-Host 'Logs are in logs/. Stop both services with stop.ps1.'
