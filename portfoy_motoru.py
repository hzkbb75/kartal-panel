import os
import re
import csv
import time
import requests
import pandas as pd
from datetime import datetime

PROJE_DIZINI = os.path.dirname(os.path.abspath(__file__))
PORTFOY_DIR = os.path.join(PROJE_DIZINI, "hektor_kutuphane", "PORTFOY")
PORTFOY_CSV_YOLU = os.path.join(PORTFOY_DIR, "portfoyum.csv")

os.makedirs(PORTFOY_DIR, exist_ok=True)

# 1. İşlemler Sekmesi (gid=1) - Make.com Girdisi
SHEETS_ISLEMLER_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQCQiyLPGlz0tNzCnh7AS3GJsNtPEr96bQvNJ7-JwUl0CFDbbIyiiXUnGx03W4Ac6wBgSBItFREm82d/pub?gid=1&single=true&output=csv"

# 2. Kasa Sekmesi (gid=515795505) - Sizin Nakit/PPF Girdiniz
SHEETS_KASA_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQCQiyLPGlz0tNzCnh7AS3GJsNtPEr96bQvNJ7-JwUl0CFDbbIyiiXUnGx03W4Ac6wBgSBItFREm82d/pub?gid=515795505&single=true&output=csv"

SADECE_ENDEKS_PARA_HARIC = {"NAN", "TOPLAM", "GENEL TOPLAM", "TRY", "USD", "EUR", "OSL(B)", "OSL"}


def tr_sayi(deger) -> float:
    if deger is None:
        return 0.0
    s = str(deger).strip()
    if not s or s == "-":
        return 0.0
    negatif = ("(" in s and ")" in s) or s.startswith("-")
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        return 0.0
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif "." in s:
        parcalar = s.split(".")
        if len(parcalar[-1]) == 3 and len(parcalar) >= 2:
            s = s.replace(".", "")
    try:
        sonuc = float(s)
        return -sonuc if negatif else sonuc
    except ValueError:
        return 0.0


def tefas_ppf_gunluk_getiri(fon_kodu: str = "OSL") -> tuple[float, float]:
    """
    TEFAS API üzerinden ilgili fonun son günlük getiri oranını ve birim fiyatını çeker.
    Dönüş: (gunluk_oran, birim_fiyat) -> Örn: (0.00138, 0.167619)
    """
    bugun_str = datetime.now().strftime("%d.%m.%Y")
    try:
        url = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"
        payload = {
            "fontip": "YAT",
            "fonkod": fon_kodu,
            "bastarih": bugun_str,
            "bittarih": bugun_str
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://www.tefas.gov.tr/TarihselVeriler.aspx"
        }
        r = requests.post(url, data=payload, headers=headers, timeout=8)
        if r.status_code == 200:
            veri = r.json().get("data", [])
            if veri:
                son = veri[-1]
                gunluk_oran = float(son.get("GUNLUKGETIRI", 0.0)) / 100.0
                birim_fiyat = float(son.get("FIYAT", 0.0))
                return gunluk_oran, birim_fiyat
    except Exception as e:
        print(f"  [Uyarı] TEFAS {fon_kodu} verisi çekilemedi: {e}")

    # Emniyet Değeri: Yıllık ~%50 faiz karşılığı günlük net nema oranı
    return 0.00135, 0.167619


def kasa_bakiyesi_cek() -> float:
    """Google Sheets 'Kasa_Hareketleri' (gid=515795505) sekmesinden G sütunundaki tüm hareketleri toplayarak net kasayı hesaplar."""
    try:
        resp = requests.get(SHEETS_KASA_CSV_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        if resp.status_code == 200:
            df = pd.read_csv(pd.io.common.StringIO(resp.text), sep=None, engine="python", dtype=str)
            # G sütununu (Kasa Etkisi) bul veya son sütunu al
            kasa_col = next((c for c in df.columns if "KASA ETKİSİ" in str(c).upper() or "KASA" in str(c).upper()), df.columns[-1])
            toplam_kasa = 0.0
            for val in df[kasa_col]:
                toplam_kasa += tr_sayi(val)
            if toplam_kasa > 0:
                return round(toplam_kasa, 2)
    except Exception as e:
        print(f"  [Uyarı] Kasa sekmesi okunamadı: {e}")
    return 69633.0


def google_sheets_uzerinden_senkronize_et() -> dict:
    """İşlemler ve Kasa sekmelerini birleştirip portfoyum.csv dosyasını günceller."""
    pozisyonlar = {}

    mevcut_notlar = {}
    if os.path.exists(PORTFOY_CSV_YOLU):
        try:
            with open(PORTFOY_CSV_YOLU, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, delimiter=";")
                for r in reader:
                    kod = (r.get("Kod") or "").strip().upper()
                    if kod:
                        mevcut_notlar[kod] = {
                            "tarih": r.get("Alim_Tarihi", datetime.now().strftime("%d.%m.%Y")),
                            "kademe": r.get("Kademe_Durumu", "1/3 Başlangıç"),
                            "not": r.get("Cikis_Kosulu_Notu", "Google Sheets Canlı Senkronizasyon")
                        }
        except Exception:
            pass

    # 1. Hisseleri Çek (Bist_Spot_Islemler)
    try:
        resp = requests.get(SHEETS_ISLEMLER_CSV_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if resp.status_code == 200:
            df = pd.read_csv(pd.io.common.StringIO(resp.text), sep=None, engine="python", dtype=str)
            df.columns = [str(c).strip().upper() for c in df.columns]

            kod_col = next((c for c in df.columns if c in ["HİSSE_KODU", "HISSE_KODU", "HİSSE", "HISSE", "SEMBOL", "KOD"]), None)
            net_adet_col = next((c for c in df.columns if c in ["NET_ADET", "ADET", "LOT"]), None)
            net_tutar_col = next((c for c in df.columns if c in ["NET_TUTAR", "ALIS_TUTAR", "TUTAR"]), None)

            if kod_col and net_adet_col:
                for _, row in df.iterrows():
                    kod = str(row[kod_col]).strip().upper()
                    if not kod or kod in SADECE_ENDEKS_PARA_HARIC:
                        continue

                    adet = tr_sayi(row[net_adet_col])
                    tutar = abs(tr_sayi(row[net_tutar_col])) if net_tutar_col else 0.0

                    if kod not in pozisyonlar:
                        pozisyonlar[kod] = {"adet": 0.0, "toplam_tutar": 0.0}
                    pozisyonlar[kod]["adet"] += adet
                    pozisyonlar[kod]["toplam_tutar"] += tutar

    except Exception as e:
        print(f"  [Uyarı] Bist_Spot_Islemler okunamadı: {e}")

    # 2. Kasayı Çek ve TEFAS Günlük Nema Getirisini Hesapla
    kasa_tutari = kasa_bakiyesi_cek()
    if kasa_tutari <= 0:
        kasa_tutari = 70000.0

    gunluk_nema_orani, osl_fiyat = tefas_ppf_gunluk_getiri("OSL")
    gunluk_nema_tl = round(kasa_tutari * gunluk_nema_orani, 2)
    aylik_tahmini_nema = round(kasa_tutari * gunluk_nema_orani * 30, 2)

    # 3. portfoyum.csv dosyasını tazele
    if pozisyonlar:
        satirlar = ["Kod;Adet;Birim_Maliyet_TL;Alim_Tarihi;Kademe_Durumu;Cikis_Kosulu_Notu"]
        bugun_str = datetime.now().strftime("%d.%m.%Y")

        for kod, veri in sorted(pozisyonlar.items()):
            adet = veri["adet"]
            if adet > 0:
                maliyet = round(veri["toplam_tutar"] / adet, 4) if veri["toplam_tutar"] > 0 else 1.0
                adet_str = str(int(adet) if adet.is_integer() else adet)

                not_bilgi = mevcut_notlar.get(kod, {
                    "tarih": bugun_str,
                    "kademe": "1/3 Başlangıç",
                    "not": "Google Sheets Otomatik Senkronizasyon"
                })

                satirlar.append(f"{kod};{adet_str};{maliyet:.2f};{not_bilgi['tarih']};{not_bilgi['kademe']};{not_bilgi['not']}")

        # Nakit satırına nema ve fon fiyatı detaylarını ekle
        nakit_notu = f"OSL Fiyat: {osl_fiyat:.6f} | Nema: +{gunluk_nema_tl:.1f}TL/gun (~{aylik_tahmini_nema:.0f}TL/ay)"
        satirlar.append(f"NAKIT;1;{round(kasa_tutari):d};{bugun_str};PPF Likidite Kalkanı;{nakit_notu}")

        with open(PORTFOY_CSV_YOLU, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(satirlar))

        print(f"[{datetime.now().strftime('%H:%M:%S')}] portfoyum.csv güncellendi: {len(satirlar)-2} Hisse | Kasa: {kasa_tutari:,.2f} TL (+{gunluk_nema_tl:.2f} TL/gün OSL Nema).")

    return pozisyonlar


def portfoy_ve_canli_fiyatlari_getir(bist_sozluk: dict = None) -> dict:
    """kartal.py'nin aradığı ana köprü: Senkronize eder, CSV'yi okur ve fiyatları tamamlar."""
    google_sheets_uzerinden_senkronize_et()

    pozisyonlar = {}
    if os.path.exists(PORTFOY_CSV_YOLU):
        try:
            with open(PORTFOY_CSV_YOLU, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, delimiter=";")
                for r in reader:
                    k = (r.get("Kod") or "").strip().upper()
                    if not k:
                        continue
                    adet = tr_sayi(r.get("Adet", 0))
                    maliyet = tr_sayi(r.get("Birim_Maliyet_TL", 0))
                    kademe = r.get("Kademe_Durumu", "1/3 Başlangıç")
                    cikis_notu = r.get("Cikis_Kosulu_Notu", "")
                    pozisyonlar[k] = {
                        "adet": int(adet) if adet.is_integer() else adet,
                        "maliyet": maliyet,
                        "canli_fiyat": 0.0,
                        "kademe": kademe,
                        "not": cikis_notu
                    }
        except Exception as e:
            print(f"  [Uyarı] CSV okuma hatası: {e}")

    # Canlı fiyatları ambardan besle
    if bist_sozluk:
        for kod, v in pozisyonlar.items():
            if kod in ["NAKIT", "TRY", "KASA", "PPF"]:
                v["canli_fiyat"] = v["maliyet"]
                continue
            s_veri = bist_sozluk.get(kod, {})
            fiyat = float(s_veri.get("fiyat_tl", 0.0))
            v["canli_fiyat"] = fiyat if fiyat > 0 else v["maliyet"]

    return pozisyonlar


if __name__ == "__main__":
    google_sheets_uzerinden_senkronize_et()