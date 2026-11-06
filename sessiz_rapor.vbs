Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd.exe /c python haftalik_rapor.py", 0, False
Set WshShell = Nothing