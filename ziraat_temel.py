import os
import re
import json
from datetime import datetime, timedelta
import requests
import pypdf

BULTEN_PDF = "ziraat_bulten.pdf"
CIKTI_JSON = "bist_analiz_tabani.json"

def bulten_indir(hedef_dosya=BULTEN_PDF):
    """Son 7 gün içindeki en güncel Ziraat Bültenini internetten indirir."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.ziraatyatirim.com.tr/"
    }
    bugun = datetime.today()
    for i in range(7):
        tarih_str = (bugun - timedelta(days=i)).strftime("%d%m%Y")
        url = f"https://www.ziraatyatirim.com.tr/documents/category/GG_{tarih_str}.pdf"
        try:
            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code == 200 and len(res.content) > 5000:
                with open(hedef_dosya, "wb") as f:
                    f.write(res.content)
                print(f"🎯 Ziraat Bülteni indirildi: GG_{tarih_str}.pdf ({len(res.content)//1024} KB)")
                return True
        except Exception:
            continue
    print("⚠️ Yeni bülten indirilemedi, mevcut dosya kontrol ediliyor.")
    return os.path.exists(hedef_dosya)

def sayi_yap(val):
    if not val or val in ["a.d.", "ad", "-", "a.d", "None", "a.d."]:
        return 0.0
    temiz = str(val).replace("%", "").replace(".", "").replace(",", ".").strip()
    try:
        return float(temiz)
    except ValueError:
        return 0.0

def ziraat_tek_basina_uret():
    """Dışarıdan sözlük gerektirmeden PDF'i tek başına okur ve JSON üretir."""
    if not os.path.exists(BULTEN_PDF):
        bulten_indir(BULTEN_PDF)

    if not os.path.exists(BULTEN_PDF):
        print(f"❌ HATA: {BULTEN_PDF} bulunamadı!")
        return {}

    reader = pypdf.PdfReader(BULTEN_PDF)
    birlesik_taban = {}
    aktif_sektor = "DİĞER"

    # Bilinen ana sektör başlıkları
    SEKTOR_LISTESI = [
        "FİNANS-DIŞI HOLDİNG", "CAM & SERAMİK", "ÇİMENTO", "İNŞAAT", "DEMİR & ÇELİK",
        "YAPI MALZEMELERİ", "DAYANIKLI TÜKETİM", "ELEKTRİK MALZEMELERİ", "MADENCİLİK",
        "MAKİNE", "OTOMOTİV YAN SANAYİ", "OTOMOTİV", "İÇECEK", "GIDA", "PERAKENDE",
        "DİĞER İMALAT", "PAZARLAMA", "MEDYA", "SPOR", "İLAÇ & SAĞLIK", "TEKSTİL & TEKSTİL YAN SANAYİ",
        "PETROL & PETRO-KİMYA & GAZ", "KİMYASAL MADDELER", "ORMAN, KAĞIT & KAĞIT ÜRÜNLERİ",
        "HAVAYOLU", "LOJİSTİK", "SAVUNMA SANAYİ", "BİLGİSAYAR & YAZILIM",
        "MESLEKİ, BİLİMSEL VE TEKNİK FAALİYETLER", "TELEKOM TEKNOLOJİLERİ", "TELEKOM",
        "ENERJİ İMALAT", "ENERJİ", "VARLIK YÖNETİMİ", "TURİZM, OTEL, EĞLENCE", "TARIM",
        "GAYRİMENKUL FAALİYETLERİ", "BANKALAR", "MENKUL DEĞERLER", "FAKTORİNG", "LEASING",
        "SİGORTA", "YATIRIM ORTAKLIKLARI", "GİRİŞİM SERMAYESİ YATIRIM ORTAKLIKLARI",
        "GAYRİMENKUL YATIRIM ORTAKLIKLARI", "YAKIN İZLEME PAZARI", "ALT PAZAR"
    ]

    for page in reader.pages:
        metin = page.extract_text()
        if not metin:
            continue
        for satir in metin.split("\n"):
            s_temiz = satir.strip()
            
            # Sektör başlığı yakalama
            for sek in SEKTOR_LISTESI:
                if sek in s_temiz and len(s_temiz) < 45:
                    aktif_sektor = sek
                    break

            tokens = s_temiz.split()
            # Satır sonunda 4-5 harfli hisse kodu varsa
            if len(tokens) >= 18 and re.match(r"^[A-Z]{4,5}$", tokens[-1]):
                kod = tokens[-1]
                sirket_adi = tokens[0]  # Varsayılan ilk kelime

                # Fiyatı yüzde işaretinden hemen sonraki sayıdan al
                fiyat = 0.0
                for i, tok in enumerate(tokens):
                    if tok.endswith("%") and i + 1 < len(tokens):
                        fiyat = sayi_yap(tokens[i + 1])
                        break

                net_kar = sayi_yap(tokens[-9])
                roa = sayi_yap(tokens[-8])
                roe = sayi_yap(tokens[-7])
                fk = sayi_yap(tokens[-6])
                pddd = sayi_yap(tokens[-5])
                fd_favok = sayi_yap(tokens[-4])
                pd_s = sayi_yap(tokens[-3])
                net_borc_favok = sayi_yap(tokens[-2])

                birlesik_taban[kod] = {
                    "kod": kod,
                    "sirket_adi": sirket_adi,
                    "sektor": aktif_sektor,
                    "fiyat": fiyat,
                    "net_kar_son4c_mn": net_kar,
                    "roe": roe,
                    "roa": roa,
                    "fk": fk,
                    "pddd": pddd,
                    "fd_favok": fd_favok,
                    "pd_s": pd_s,
                    "net_borc_favok": net_borc_favok,
                    "kaynak": "Ziraat Yatırım Resmi"
                }

    with open(CIKTI_JSON, "w", encoding="utf-8") as f:
        json.dump(birlesik_taban, f, ensure_ascii=False, indent=2)

    print(f"✅ Başarılı! Dış dosyaya ihtiyaç olmadan {len(birlesik_taban)} hisse '{CIKTI_JSON}' dosyasına tek başına yazıldı.")
    return birlesik_taban

if __name__ == "__main__":
    bulten_indir()
    ziraat_tek_basina_uret()