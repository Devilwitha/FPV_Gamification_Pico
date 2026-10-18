$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot

$python = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe'
if (-not (Test-Path $python)) {
    $python = 'py'
}

& $python -u (Join-Path $PSScriptRoot 'serve_production.py')
