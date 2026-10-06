$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'server.pid'
if (Test-Path -LiteralPath $pidFile) {
    $serverPid = [int](Get-Content -LiteralPath $pidFile)
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
    $expectedScript = Join-Path $PSScriptRoot 'server.py'
    if ($processInfo -and $processInfo.CommandLine.Contains($expectedScript)) {
        Stop-Process -Id $serverPid
        Write-Output 'Local update server stopped.'
    }
}
