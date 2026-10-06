param([switch]$Offline, [switch]$Candidate)
. (Join-Path $PSScriptRoot 'runtime.ps1')
$taskRoot = $PSScriptRoot
$pythonRuntime = Get-ProjectPython
Push-Location $taskRoot
try {
    & $pythonRuntime (Join-Path $taskRoot 'core.py') --bootstrap
    if ($LASTEXITCODE -ne 0) { throw 'No verified installation. Run prepare.py --source PATH first.' }
    if (-not $Offline -and -not $Candidate) {
        & $pythonRuntime (Join-Path $taskRoot 'update.py')
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Update unavailable; starting cached installation.' }
    }
    $stateFile = if ($Candidate) { 'data\candidate.json' } else { 'data\active.json' }
    $selected = Get-Content -LiteralPath (Join-Path $taskRoot $stateFile) -Raw | ConvertFrom-Json
    & $pythonRuntime (Join-Path $taskRoot 'core.py') --verify (Join-Path $taskRoot $stateFile)
    if ($LASTEXITCODE -ne 0) { throw 'Selected installation failed integrity validation.' }
    $appDirectory = [IO.Path]::GetFullPath((Join-Path $taskRoot $selected.app))
    $manifestDirectory = [IO.Path]::GetFullPath((Join-Path $taskRoot $selected.manifests))
    $serverReady = $false
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:33875/health' -TimeoutSec 2
        $serverReady = $health.project -eq $taskRoot -and $health.manifests -eq $manifestDirectory -and $health.version -eq $selected.version
    } catch { }
    if (-not $serverReady) {
        $serverConnection = Get-NetTCPConnection -LocalPort 33875 -State Listen -ErrorAction SilentlyContinue
        if ($serverConnection) {
            throw 'Port 33875 serves another snapshot. Close Sideloadly and run stop-server.ps1, then restart.'
        }
        $serverArgs = '"' + (Join-Path $taskRoot 'server.py') + '" --manifests "' + $manifestDirectory + '"'
        $serverProcess = Start-Process -FilePath $pythonRuntime -ArgumentList $serverArgs -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRoot 'server-output.log') -RedirectStandardError (Join-Path $taskRoot 'server-access.log') -PassThru
        $serverProcess.Id | Set-Content -LiteralPath (Join-Path $taskRoot 'server.pid')
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            try {
                $health = Invoke-RestMethod -Uri 'http://127.0.0.1:33875/health' -TimeoutSec 1
                if ($health.project -eq $taskRoot -and $health.manifests -eq $manifestDirectory) { $serverReady = $true; break }
            } catch { Start-Sleep -Milliseconds 100 }
        }
        if (-not $serverReady) { throw 'Local update server failed to start. See server-access.log.' }
    }
    $selectedExe = Join-Path $appDirectory 'sideloadly.exe'
    $existingApp = Get-Process -Name sideloadly -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $selectedExe -and $_.MainWindowHandle -ne 0 }
    if ($existingApp) {
        Write-Output "Sideloadly $($selected.version) is already running; update server is ready."
    } else {
        $appProcess = Start-Process -FilePath $selectedExe -WorkingDirectory $appDirectory -PassThru
        Write-Output "Sideloadly $($selected.version) PID: $($appProcess.Id); update server: 127.0.0.1:33875"
    }
} finally { Pop-Location }
