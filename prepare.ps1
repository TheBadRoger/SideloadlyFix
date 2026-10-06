. (Join-Path $PSScriptRoot 'runtime.ps1')
$pythonRuntime = Get-ProjectPython
& $pythonRuntime (Join-Path $PSScriptRoot 'prepare.py') @args
exit $LASTEXITCODE
