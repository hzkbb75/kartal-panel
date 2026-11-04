@echo off
chcp 65001 > nul
echo KARTAL Arka Plan Görevi Kuruluyor...
schtasks /create /tn "KARTAL_Otomasyon_Gorevi" /tr "pythonw.exe C:\Users\Dell\Desktop\proje\kartal_otomasyon.py" /sc DAILY /st 19:00 /f
echo.
echo [BAŞARILI] Görev kuruldu! Hafta içi her gün saat 19:00'da arka planda sessizce çalışacaktır.
pause