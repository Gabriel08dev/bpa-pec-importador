Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
project = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
python = fso.BuildPath(project, ".venv\Scripts\pythonw.exe")
If Not fso.FileExists(python) Then
  MsgBox "Execute scripts\instalar.ps1 antes.", 16, "Importador CEAE"
  WScript.Quit 1
End If
shell.CurrentDirectory = project
shell.Run Chr(34) & python & Chr(34) & " -m app.main", 0, False
