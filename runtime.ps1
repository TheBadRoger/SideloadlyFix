$ErrorActionPreference = 'Stop'
function Get-ProjectPython {
    if ($env:SIDELOADLYFIX_PYTHON) {
        if (-not (Test-Path -LiteralPath $env:SIDELOADLYFIX_PYTHON)) { throw 'SIDELOADLYFIX_PYTHON does not exist.' }
        return $env:SIDELOADLYFIX_PYTHON
    }
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source -notlike '*\WindowsApps\*') { return $pythonCommand.Source }
    throw 'Python 3.10+ required. Set SIDELOADLYFIX_PYTHON to its python.exe path.'
}
