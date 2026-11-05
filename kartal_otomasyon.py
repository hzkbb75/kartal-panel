import os
import sys
import subprocess
from datetime import datetime

PROJE_DIZINI = os.path.dirname(os.path.abspath(__file__))
GELEN_RAPOR_HEDEF = os.path.join(PROJE_DIZINI, "hektor_kutuphane", "PORTFOY", "gelen_raporlar")
os.makedirs(GELEN_RAPOR_HEDEF, exist_ok=True)

def log(mesaj):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {mesaj}")

def main():
    log("KARTAL Tam Otomasyon Çarkı Çalışıyor (YÜZDE 100 YEREL MOD)...")

    # 1. KAP'tan Yeni PDF'leri Otomatik İndir (YENİ EKLENEN KISIM)
    pdf_indirici_py = os.path.join(PROJE_DIZINI, "kap_pdf_indirici.py")
    if os.path.exists(pdf_indirici_py):
        log("📥 KAP'tan yeni Faaliyet Raporları taranıyor ve indiriliyor...")
        subprocess.run([sys.executable, pdf_indirici_py], cwd=PROJE_DIZINI)

    # 2. Yerel klasörü kontrol et ve varsa ayrıştırıcıyı tetikle
    pdf_dosyalar = [f for f in os.listdir(GELEN_RAPOR_HEDEF) if f.lower().endswith(".pdf")]
    if pdf_dosyalar:
        log(f"Yerel klasörde {len(pdf_dosyalar)} adet PDF bulundu. Dipnot ayrıştırıcı devreye giriyor...")
        ayristirici_py = os.path.join(PROJE_DIZINI, "pdf_dipnot_ayristirici.py")
        if os.path.exists(ayristirici_py):
            subprocess.run([sys.executable, ayristirici_py], cwd=PROJE_DIZINI)
    else:
        log("İşlenecek yeni PDF bulunamadı. Ayrıştırma atlandı.")

    # 3. Google Sheets Senkronizasyonu
    portfoy_motoru_py = os.path.join(PROJE_DIZINI, "portfoy_motoru.py")
    if os.path.exists(portfoy_motoru_py):
        subprocess.run([sys.executable, portfoy_motoru_py], cwd=PROJE_DIZINI)

    # 4. Yerel BIST KAP ve Haber Motoru
    kap_motoru_py = os.path.join(PROJE_DIZINI, "yerel_kap_motoru.py")
    if os.path.exists(kap_motoru_py):
        log("📰 Yerel KAP & Haber Motoru güncelleniyor...")
        subprocess.run([sys.executable, kap_motoru_py], cwd=PROJE_DIZINI)

    # 5. Make.com (LLM) Bildirimleri
    kap_takip_py = os.path.join(PROJE_DIZINI, "portfoy_kap_takip.py")
    if os.path.exists(kap_takip_py):
        log("🤖 Make.com KAP taraması başlatılıyor...")
        subprocess.run([sys.executable, kap_takip_py], cwd=PROJE_DIZINI)

    # 6. KARTAL Panolarını Üret (HTML)
    kartal_py = os.path.join(PROJE_DIZINI, "kartal.py")
    if os.path.exists(kartal_py):
        log("🚀 KARTAL Analiz Motoru HTML üretiyor...")
        subprocess.run([sys.executable, kartal_py], cwd=PROJE_DIZINI)
        log("✅ Otomasyon Tamamlandı! index.html güncellendi.")

if __name__ == "__main__":
    main()