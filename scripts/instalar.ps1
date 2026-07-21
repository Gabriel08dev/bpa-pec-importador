param([switch]$ComOCR)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
  try { & py -3.12 -c "import sys; assert sys.version_info[:2] == (3,12)"; if ($LASTEXITCODE -eq 0) { $python = 'py'; $pyArgs = @('-3.12') } } catch {}
}
if (-not $python) {
  $installed = Get-Command python -ErrorAction SilentlyContinue
  if ($installed) {
    try { & $installed.Source -c "import sys; assert sys.version_info[:2] == (3,12)"; if ($LASTEXITCODE -eq 0) { $python = $installed.Source; $pyArgs = @() } } catch {}
  }
}
if (-not $python) { throw 'Python 3.12 não encontrado. Instale Python 3.12 x64 e execute novamente.' }
if (-not (Test-Path -LiteralPath (Join-Path $project '.venv\Scripts\python.exe'))) {
  & $python @pyArgs -m venv (Join-Path $project '.venv')
}
$venvPython = Join-Path $project '.venv\Scripts\python.exe'
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $project 'requirements.txt')
if ($ComOCR) { & $venvPython -m pip install -r (Join-Path $project 'requirements-ocr.txt') }
& $venvPython -c "from pathlib import Path; from app.services.spreadsheet_service import create_template; from app.services.professional_service import ProfessionalRegistry; p=Path(r'$project'); r=ProfessionalRegistry(p/'data/profissionais.json'); create_template(p/'outputs/modelo_controle_atendimentos.xlsx',[x.name for x in r.items])"
& (Join-Path $PSScriptRoot 'criar_atalho.ps1')
Write-Host 'Instalação concluída. Use o atalho Importador CEAE — PEC e BPA.' -ForegroundColor Green
