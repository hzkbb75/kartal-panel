Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Dell\Desktop\proje"
WshShell.Run "cmd /c python ziraat_temel.py && python bilanco_guncelle.py", 0, False