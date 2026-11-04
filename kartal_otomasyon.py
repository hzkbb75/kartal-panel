import os
import sys
import shutil
import requests
import subprocess
from datetime import datetime

PROJE_DIZINI = os.path.dirname(os.path.abspath(__file__))
GELEN_RAPOR_HEDEF = os.path.join(PROJE_DIZINI, "hektor_kutuphane", "PORTFOY", "gelen_raporlar")
GITHUB_API_URL = "https://api.github.com/repos/hzkbb75/kartal-kap-bot/contents/raporlar"

os.makedirs(GELEN_RAPOR_HEDEF, exist_ok=True)

def log(mesaj):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {mesaj}")

def main():
    log("KARTAL Tam Otomasyon Çarkı Çalışıyor...")

    yeni_pdf_geldi = False
    
    # 1. GitHub API üzerinden repodaki 'raporlar' klasörünü kontrol et (Git programı gerektirmez)
    try:
        headers = {"User-Agent": "KARTAL-Automation"}
        res = requests.get(GITHUB_API_URL, headers=headers, timeout=20)
        
        if res.status_code == 200:
            dosyalar = res.json()
            if isinstance(dosyalar, list):
                for dosya_obj in dosyalar:
                    ad = dosya_obj.get("name", "")
                    indir_link = dosya_obj.get("download_url", "")
                    
                    if ad.lower().endswith(".pdf") and indir_link:
                        hedef_yol = os.path.join(GELEN_RAPOR_HEDEF, ad)
                        
                        # Yerelde henüz yoksa doğrudan indir
                        if not os.path.exists(hedef_yol):
                            log(f"  📥 Yeni faaliyet raporu GitHub'dan indiriliyor: {ad}")
                            pdf_data = requests.get(indir_link, timeout=60).content
                            with open(hedef_yol, "wb") as f:
                                f.write(pdf_data)
                            yeni_pdf_geldi = True
            else:
                log("GitHub'da henüz 'raporlar' klasörü oluşmamış.")
        elif res.status_code == 404:
            log("GitHub'da henüz rapor yüklenmemiş (raporlar klasörü henüz boş).")
        else:
            log(f"GitHub bağlantı durumu: {res.status_code}")
    except Exception as e:
        log(f"GitHub sorgulama hatası: {e}")

    # 2. Yeni bir rapor indiyse dipnot ayrıştırıcıyı otomatik tetikle
    if yeni_pdf_geldi:
        log("Yeni faaliyet raporu bulundu! Dipnot ayrıştırıcı devreye giriyor...")
        ayristirici_py = os.path.join(PROJE_DIZINI, "pdf_dipnot_ayristirici.py")
        if os.path.exists(ayristirici_py):
            subprocess.run([sys.executable, ayristirici_py], cwd=PROJE_DIZINI)

	# 2.5. Google Sheets üzerinden hisseleri ve kasayı senkronize et
    portfoy_motoru_py = os.path.join(PROJE_DIZINI, "portfoy_motoru.py")
    if os.path.exists(portfoy_motoru_py):
        subprocess.run([sys.executable, portfoy_motoru_py], cwd=PROJE_DIZINI)

    # 3. KARTAL analiz motorunu çalıştır ve panoyu güncelle
    kartal_py = os.path.join(PROJE_DIZINI, "kartal.py")
    if os.path.exists(kartal_py):
        log("KARTAL Analiz Motoru çalıştırılıyor...")
        subprocess.run([sys.executable, kartal_py], cwd=PROJE_DIZINI)
        log("✅ KARTAL Portföy Kalkanı ve HTML Teftiş Masası güncellendi!")

if __name__ == "__main__":
    main()