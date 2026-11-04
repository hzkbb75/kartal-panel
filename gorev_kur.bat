@echo off
chcp 65001 > nul
echo KARTAL Arka Plan Görevi Kuruluyor...

:: Varsa eski kaydı temizle
schtasks /delete /tn "KARTAL_Otomasyon_Gorevi" /f > nul 2>&1

:: Hafta içi her gün sabah 10:00'da proje dizininde sessizce çalıştır
schtasks /create /tn "KARTAL_Otomasyon_Gorevi" /tr "cmd.exe /c \"cd /d C:\Users\Dell\Desktop\proje && pythonw.exe kartal_otomasyon.py\"" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 10:00 /f

echo.
echo ======================================================================
echo [BAŞARILI] Görev kuruldu!
echo Hafta içi her gün sabah 10:00'da arka planda sessizce çalışacak,
echo dün akşamdan kalan taze KAP raporlarını alıp panoları güncelleyecektir.
echo ======================================================================
pause