. (Join-Path $PSScriptRoot 'runtime.ps1')
$pythonRuntime = Get-ProjectPython
& $pythonRuntime (Join-Path $PSScriptRoot 'update.py') --force @args
exit $LASTEXITCODE
