$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Execute scripts\instalar.ps1 antes.' }
Start-Process -FilePath $python -ArgumentList '-m','app.main' -WorkingDirectory $project -WindowStyle Hidden
