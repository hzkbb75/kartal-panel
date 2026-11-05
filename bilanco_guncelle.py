import os
import re
import json
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pandas as pd

# ==============================================================================
# 0. AYARLAR VE ÇIKTI DİZİNİ
# ==============================================================================
ROOT_DIR = "hektor_kutuphane"
CIKTI_YOLU = os.path.join(ROOT_DIR, "bist_mali_tablolar.json")
SOZLUK_YOLU = os.path.join(ROOT_DIR, "bist_sozluk.json") # Yeni Sözlük Yolumuz
os.makedirs(ROOT_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*"
}

PARALEL_ISCI_SAYISI = 6
MAX_RETRY = 3
TIMEOUT_SURESI = 12

def tr_norm(metin: str) -> str:
    if not metin: return ""
    ceviri = str.maketrans("İIŞĞÜÖÇıişğüöç", "iisguociisguoc")
    temiz = metin.translate(ceviri).lower()
    return re.sub(r"\s+", " ", temiz).strip()

# ==============================================================================
# 1. TRADINGVIEW VERİSİ VE BİST SÖZLÜĞÜ İLE EŞLEŞTİRME
# ==============================================================================
def tum_sirket_evrenini_derle() -> list:
    print("[1/3] TradingView temel verileri ve yerel BİST Sözlüğü (Ziraat) birleştiriliyor...")
    
    # 1. Yerel Sözlüğü Yükle
    bist_sozluk = {}
    if os.path.exists(SOZLUK_YOLU):
        with open(SOZLUK_YOLU, "r", encoding="utf-8") as f:
            bist_sozluk = json.load(f)
    else:
        print(f"  [KRİTİK HATA] {SOZLUK_YOLU} bulunamadı! Lütfen dosyayı indirin.")
        return []

    # 2. TradingView'dan Fiyat/Çarpanları Çek
    url = "https://scanner.tradingview.com/turkey/scan"
    payload = {
        "filter": [{"left": "type", "operation": "equal", "right": "stock"}, {"left": "exchange", "operation": "equal", "right": "BIST"}],
        "options": {"lang": "tr"}, "markets": ["turkey"],
        "columns": ["name", "close", "Perf.1M", "market_cap_basic", "price_earnings_ttm", "enterprise_value_ebitda_ttm", "return_on_equity", "float_shares_percent_current"],
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"}, "range": [0, 850]
    }
    
    evren = []
    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            for satir in r.json().get("data", []):
                d = satir.get("d", [])
                if d and d[0]:
                    kod = str(d[0]).strip().upper()
                    
                    # Sadece sözlükte olan gerçek şirketleri al (Endeksleri eler)
                    if kod in bist_sozluk:
                        ha_raw = d[7] if len(d) > 7 else None
                        evren.append({
                            "kod": kod,
                            "sirket_adi": bist_sozluk[kod]["isim"],     # Direkt sözlükten
                            "sektor": bist_sozluk[kod]["sektor"],         # Direkt sözlükten
                            "fiyat": round(float(d[1] or 0.0), 2),
                            "degisim_aylik": round(float(d[2] or 0.0), 1),
                            "piyasa_degeri_milyar": round(float(d[3] or 0.0) / 1e9, 2) if d[3] else 0.0,
                            "fk": round(float(d[4] or 0.0), 1) if d[4] else 0.0,
                            "fd_favok": round(float(d[5] or 0.0), 1) if d[5] else 0.0,
                            "roe_tv": round(float(d[6] or 0.0), 1) if d[6] else 0.0,
                            "halka_aciklik": round(float(ha_raw), 2) if ha_raw is not None else 35.0
                        })
    except Exception as e:
        print(f"  [Uyarı] TradingView Screener bağlantı hatası: {e}")

    print(f"  Toplam {len(evren)} net BIST şirketi listelendi (Ziraat Onaylı).")
    return evren


# ==============================================================================
# 2. UFRS BİLANÇO TABLOLARINI ÇEKME VE HATA İZOLASYONU (RETRY)
# ==============================================================================
def cek_sirket_mali_tablolari(session: requests.Session, kod: str) -> dict:
    donem_gruplari = [
        ("2026", "6", "2025", "6", "2026", "3", "2025", "3"),
        ("2025", "12", "2024", "12", "2025", "9", "2024", "9"),
        ("2025", "9", "2024", "9", "2025", "6", "2024", "6")
    ]
    finansal_gruplar = ["XI_29", "UFRS_K", "UFRS_B"]

    for grup in finansal_gruplar:
        for y1, p1, y2, p2, y3, p3, y4, p4 in donem_gruplari:
            url = (
                f"https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/Common/Data.aspx/MaliTablo"
                f"?companyCode={kod}&exchange=TRY&financialGroup={grup}"
                f"&year1={y1}&period1={p1}&year2={y2}&period2={p2}"
                f"&year3={y3}&period3={p3}&year4={y4}&period4={p4}"
            )
            for deneme in range(MAX_RETRY):
                try:
                    r = session.get(url, headers=HEADERS, timeout=TIMEOUT_SURESI)
                    if r.status_code == 200:
                        veri = r.json().get("value", [])
                        if veri and len(veri) > 5:
                            etiketler = [f"{y1}/{int(p1):02d}", f"{y2}/{int(p2):02d}", f"{y3}/{int(p3):02d}", f"{y4}/{int(p4):02d}"]
                            donemler_sozluk = {d: [] for d in etiketler}
                            satirlar_cari_onceki = []

                            for s in veri:
                                ad = tr_norm(s.get("itemDescTr", ""))
                                v1 = float(s.get("value1") or 0.0) / 1e6
                                v2 = float(s.get("value2") or 0.0) / 1e6
                                v3 = float(s.get("value3") or 0.0) / 1e6
                                v4 = float(s.get("value4") or 0.0) / 1e6

                                vals = [v1, v2, v3, v4]
                                for idx_d, d_etiket in enumerate(etiketler):
                                    donemler_sozluk[d_etiket].append([ad, vals[idx_d]])

                                cari = v1 if v1 != 0.0 else v3
                                onceki = v2 if v1 != 0.0 else v4
                                satirlar_cari_onceki.append([ad, cari, onceki])

                            dolu_donemler = {k: v for k, v in donemler_sozluk.items() if any(abs(val[1]) > 0 for val in v)}
                            if len(dolu_donemler) >= 2:
                                return {
                                    "veri_durumu": "TAM", "finansal_grup": grup, "son_donem": etiketler[0], "onceki_donem": etiketler[1],
                                    "donemler": dolu_donemler, "satirlar_cari_onceki": satirlar_cari_onceki
                                }
                    break
                except Exception:
                    time.sleep(1.0 * (deneme + 1))
                    continue

    return {"veri_durumu": "VERI_YOK", "finansal_grup": "", "son_donem": "", "onceki_donem": "", "donemler": {}, "satirlar_cari_onceki": []}


def isci_gorevi(sirket: dict) -> tuple:
    kod = sirket["kod"]
    mali_veri = cek_sirket_mali_tablolari(requests.Session(), kod)
    
    paket = {**sirket, **mali_veri} # Şirket ana bilgileriyle mali veriyi birleştir
    return kod, paket

# ==============================================================================
# 3. TOPLU İNDİRME VE YEREL VERİ TABANI KAYDI
# ==============================================================================
def main():
    baslangic = time.time()
    print("=" * 65)
    print("KARTAL BİLANÇO GÜNCELLEME VE YEREL VERİ AMBARI MOTORU")
    print("=" * 65)

    evren = tum_sirket_evrenini_derle()
    toplam = len(evren)

    print(f"\n[2/3] {toplam} şirket için UFRS finansal tabloları çekiliyor (İşçi Sayısı: {PARALEL_ISCI_SAYISI})...")
    veritabani_sozluk = {}
    tam_sayisi = eksik_sayisi = 0

    with ThreadPoolExecutor(max_workers=PARALEL_ISCI_SAYISI) as executor:
        gelecek_isler = {executor.submit(isci_gorevi, s): s["kod"] for s in evren}
        for idx, gelecek in enumerate(as_completed(gelecek_isler), 1):
            kod, veri_paketi = gelecek.result()
            veritabani_sozluk[kod] = veri_paketi
            
            if veri_paketi["veri_durumu"] == "TAM": tam_sayisi += 1
            else: eksik_sayisi += 1

            if idx % 75 == 0 or idx == toplam:
                print(f"  İlerleme: {idx}/{toplam} (%{(idx/toplam)*100:.1f}) | Tam: {tam_sayisi} | Bekleyen: {eksik_sayisi}")

    print("\n[3/3] Yerel veri tabanı dosyasına yazılıyor...")
    with open(CIKTI_YOLU, "w", encoding="utf-8") as f:
        json.dump({"guncelleme_tarihi": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "toplam_sirket": toplam, "bilancosu_tam_sirket": tam_sayisi, "bilancosu_olmayan_sirket": eksik_sayisi, "sirketler": veritabani_sozluk}, f, ensure_ascii=False, indent=2)

    print(f"\nTAMAMLANDI! Süre: {round(time.time() - baslangic, 1)} saniye. Çekilen Şirket: {tam_sayisi}")

if __name__ == "__main__":
    main()