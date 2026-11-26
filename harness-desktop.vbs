' harness-desktop.vbs: `harness desktop`'ı hiç konsol penceresi açmadan başlatır.
' harness-desktop.bat ile aynı komutu çalıştırır, yalnız pencere stili gizli (0).
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Run "uv run harness desktop", 0, False
