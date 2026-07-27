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

# Atualiza também o nome antigo, caso ele exista, para evitar que abra uma
# instalação anterior do importador.
$legacyShortcutPath = Join-Path $desktop 'Importador PEC BPA.lnk'
if (Test-Path -LiteralPath $legacyShortcutPath) {
    $legacyShortcut = $shell.CreateShortcut($legacyShortcutPath)
    $legacyShortcut.TargetPath = $pythonw
    $legacyShortcut.Arguments = '-m app.main'
    $legacyShortcut.WorkingDirectory = $project
    $legacyShortcut.Description = 'Importador local CEAE - PEC e BPA'
    $legacyShortcut.IconLocation = 'C:\Windows\System32\imageres.dll,14'
    $legacyShortcut.Save()
}

$tunnelShortcutPath = Join-Path $desktop ('BPA System ' + [char]0x2014 + ' Acesso externo.lnk')
$tunnelShortcut = $shell.CreateShortcut($tunnelShortcutPath)
$tunnelShortcut.TargetPath = $pythonw
$tunnelShortcut.Arguments = '-m app.tunnel'
$tunnelShortcut.WorkingDirectory = $project
$tunnelShortcut.Description = 'Abrir acesso externo protegido do BPA System'
$tunnelShortcut.IconLocation = 'C:\Windows\System32\imageres.dll,144'
$tunnelShortcut.Save()
Write-Host "Atalho criado: $shortcutPath"
Write-Host "Atalho externo criado: $tunnelShortcutPath"
