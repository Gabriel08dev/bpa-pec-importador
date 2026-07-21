$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutName = 'Importador CEAE ' + [char]0x2014 + ' PEC e BPA.lnk'
$shortcutPath = Join-Path $desktop $shortcutName
$pythonw = Join-Path $project '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'Execute scripts\instalar.ps1 antes de criar o atalho.' }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '-m app.main'
$shortcut.WorkingDirectory = $project
$shortcut.Description = 'Importador local CEAE - PEC e BPA'
$shortcut.IconLocation = 'C:\Windows\System32\imageres.dll,14'
$shortcut.Save()
Write-Host "Atalho criado: $shortcutPath"
