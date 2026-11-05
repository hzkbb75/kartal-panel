Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Dell\Desktop\proje"
WshShell.Run "python kartal.py", 0, False