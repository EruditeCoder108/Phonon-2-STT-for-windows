Set WshShell = CreateObject("WScript.Shell")
ScriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = ScriptDir
WshShell.Run """" & ScriptDir & "\.venv\Scripts\pythonw.exe"" """ & ScriptDir & "\run.py""", 0, False
