Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Dell\Desktop\proje"
WshShell.Run "python kartal_otomasyon.py", 0, False