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
os.makedirs(ROOT_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.isyatirim.com.tr/"
}

PARALEL_ISCI_SAYISI = 6
MAX_RETRY = 3
TIMEOUT_SURESI = 12

SADECE_ENDEKS_PARA_HARIC = {
    "BIST", "XU100", "XU030", "TRY", "USD", "EUR", "XU050", "XUMKR", "XUTUM", "XUSIN", "XBANK"
}

BANKA_VE_FINANS_KODLARI = {
    "GARAN", "AKBNK", "ISCTR", "YKBNK", "VAKBN", "HALKB", "TSKB", "SKBNK",
    "ALBRK", "QNBFB", "QNBTR", "KLNMA", "ICBCT", "TURSG", "AGESA", "ANHYT",
    "ANSGR", "AKGRT", "RAYSG", "ISMEN", "INFO", "OSMEN", "GEDIK", "GLCVY", "OYYAT", "DSTKF"
}


def tr_norm(metin: str) -> str:
    if not metin:
        return ""
    ceviri = str.maketrans("İIŞĞÜÖÇıişğüöç", "iisguociisguoc")
    temiz = metin.translate(ceviri).lower()
    return re.sub(r"\s+", " ", temiz).strip()


def gecerli_bist_kodu_mu(kod: str) -> bool:
    if not kod:
        return False
    k = str(kod).strip().upper()
    if k in SADECE_ENDEKS_PARA_HARIC:
        return False
    if k.isdigit() or any(char.isdigit() for char in k):
        return False
    return bool(re.match(r"^[A-Z]{4,5}$", k))


# ==============================================================================
# 1. TRADINGVIEW VE İŞ YATIRIM BİST EVRENİ KEŞFİ
# ==============================================================================
def cek_tradingview_screener() -> dict:
    url = "https://scanner.tradingview.com/turkey/scan"
    payload = {
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "exchange", "operation": "equal", "right": "BIST"}
        ],
        "options": {"lang": "tr"},
        "markets": ["turkey"],
        "columns": [
            "name", "description", "sector", "close", "Perf.1M",
            "market_cap_basic", "price_earnings_ttm", "enterprise_value_ebitda_ttm", "return_on_equity",
            "float_shares_percent_current"
        ],
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, 850]
    }
    sonuc = {}
    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            for satir in r.json().get("data", []):
                d = satir.get("d", [])
                if d and d[0]:
                    kod = str(d[0]).strip().upper()
                    if not gecerli_bist_kodu_mu(kod):
                        continue
                    ha_raw = d[9] if len(d) > 9 else None
                    sonuc[kod] = {
                        "kod": kod,
                        "sirket_adi": str(d[1] or kod),
                        "sektor": str(d[2] or "Sanayi / Hizmet")[:28],
                        "fiyat": round(float(d[3] or 0.0), 2),
                        "degisim_aylik": round(float(d[4] or 0.0), 1),
                        "piyasa_degeri_milyar": round(float(d[5] or 0.0) / 1e9, 2) if d[5] else 0.0,
                        "fk": round(float(d[6] or 0.0), 1) if d[6] else 0.0,
                        "fd_favok": round(float(d[7] or 0.0), 1) if d[7] else 0.0,
                        "roe_tv": round(float(d[8] or 0.0), 1) if d[8] else 0.0,
                        "halka_aciklik": round(float(ha_raw), 2) if ha_raw is not None else 35.0
                    }
    except Exception as e:
        print(f"  [Uyarı] TradingView Screener bağlantı hatası: {e}")
    return sonuc


def tum_sirket_evrenini_derle() -> list:
    print("[1/3] TradingView ve İş Yatırım üzerinden BIST hisse havuzu taranıyor...")
    evren = cek_tradingview_screener()

    url = "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/Temel-Degerler-Ve-Oranlar.aspx"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            tr_bloklari = re.findall(r'<tr[^>]*>(.*?)</tr>', r.text, re.DOTALL)
            for tr in tr_bloklari:
                kod_m = re.search(r'hisse=([A-Z0-9]{3,6})"', tr)
                if kod_m:
                    kod = kod_m.group(1).strip().upper()
                    if not gecerli_bist_kodu_mu(kod):
                        continue
                    td_list = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.DOTALL)
                    temiz_td = [re.sub(r'<[^>]+>', '', td).strip() for td in td_list]
                    sektor = "Sanayi / Hizmet"
                    
                    # --- YENİ EKLENEN SEKTÖR FİLTRESİ ---
                    if len(temiz_td) > 1:
                        potansiyel_sektor = temiz_td[1]
                        # Şirket ünvanlarının (A.Ş., SAN., TİC.) sektör olmasını engelle
                        if len(potansiyel_sektor) > 3 and not any(ch.isdigit() for ch in potansiyel_sektor):
                            kucuk_isim = potansiyel_sektor.lower()
                            if "a.ş." not in kucuk_isim and "san." not in kucuk_isim and "tic." not in kucuk_isim:
                                sektor = potansiyel_sektor[:28]
                    # ------------------------------------

                    if kod not in evren:
                        evren[kod] = {
                            "kod": kod, "sirket_adi": kod, "sektor": sektor,
                            "fiyat": 0.0, "degisim_aylik": 0.0, "piyasa_degeri_milyar": 0.0,
                            "fk": 0.0, "fd_favok": 0.0, "roe_tv": 0.0, "halka_aciklik": 35.0
                        }
                    elif sektor != "Sanayi / Hizmet":
                        evren[kod]["sektor"] = sektor
    except Exception:
        pass

    ozel_tanimlar = [
        ("GARAN", "Bankacılık", 14.0), ("AKBNK", "Bankacılık", 52.0), ("ISCTR", "Bankacılık", 31.4),
        ("YKBNK", "Bankacılık", 38.8), ("VAKBN", "Bankacılık", 6.1), ("HALKB", "Bankacılık", 8.5),
        ("QNBTR", "Bankacılık", 0.12), ("ANSGR", "Sigorta ve Emeklilik", 35.0), ("AGESA", "Sigorta ve Emeklilik", 20.0),
        ("TURSG", "Sigorta ve Emeklilik", 18.9), ("PETKM", "Kimya ve Petrokimya", 47.0), ("EREGL", "Demir Çelik ve Maden", 47.6),
        ("ASELS", "Savunma ve Teknoloji", 25.8), ("BIMAS", "Perakende ve Gıda", 60.1), ("TABGD", "Perakende ve Gıda", 20.0),
        ("MPARK", "Sağlık ve İlaç", 35.0), ("TTKOM", "Telekomünikasyon", 13.2), ("ASTOR", "Elektrik ve Enerji", 31.7)
    ]
    for kod, sek, ha in ozel_tanimlar:
        if kod in evren:
            evren[kod]["sektor"] = sek
            evren[kod]["halka_aciklik"] = ha

    liste = [v for k, v in evren.items() if gecerli_bist_kodu_mu(k)]
    print(f"  Toplam {len(liste)} net BIST şirketi listelendi.")
    return liste


# ==============================================================================
# 2. UFRS BİLANÇO TABLOLARINI ÇEKME VE HATA İZOLASYONU (RETRY)
# ==============================================================================
def cek_sirket_mali_tablolari(session: requests.Session, kod: str) -> dict:
    """
    Şirketin son dönem bilançolarını İş Yatırım UFRS API'sinden 
    kontrollü yeniden deneme (retry) ile çeker. Sahte veri üretmez.
    """
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
                            etiketler = [
                                f"{y1}/{int(p1):02d}", f"{y2}/{int(p2):02d}",
                                f"{y3}/{int(p3):02d}", f"{y4}/{int(p4):02d}"
                            ]
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

                            dolu_donemler = {
                                k: v for k, v in donemler_sozluk.items()
                                if any(abs(val[1]) > 0 for val in v)
                            }
                            if len(dolu_donemler) >= 2:
                                return {
                                    "veri_durumu": "TAM",
                                    "finansal_grup": grup,
                                    "son_donem": etiketler[0],
                                    "onceki_donem": etiketler[1],
                                    "donemler": dolu_donemler,
                                    "satirlar_cari_onceki": satirlar_cari_onceki
                                }
                    break
                except Exception:
                    time.sleep(1.0 * (deneme + 1))
                    continue

    return {
        "veri_durumu": "VERI_YOK",
        "finansal_grup": "",
        "son_donem": "",
        "onceki_donem": "",
        "donemler": {},
        "satirlar_cari_onceki": []
    }


def isci_gorevi(sirket: dict) -> tuple:
    kod = sirket["kod"]
    session = requests.Session()
    mali_veri = cek_sirket_mali_tablolari(session, kod)
    
    paket = {
        "kod": kod,
        "sirket_adi": sirket["sirket_adi"],
        "sektor": sirket["sektor"],
        "halka_aciklik": sirket["halka_aciklik"],
        "piyasa_degeri_milyar": sirket["piyasa_degeri_milyar"],
        "fiyat": sirket["fiyat"],
        "degisim_aylik": sirket["degisim_aylik"],
        "fk": sirket["fk"],
        "fd_favok": sirket["fd_favok"],
        "roe_tv": sirket["roe_tv"],
        "veri_durumu": mali_veri["veri_durumu"],
        "finansal_grup": mali_veri["finansal_grup"],
        "son_donem": mali_veri["son_donem"],
        "onceki_donem": mali_veri["onceki_donem"],
        "donemler": mali_veri["donemler"],
        "satirlar_cari_onceki": mali_veri["satirlar_cari_onceki"]
    }
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
    tamamlanan = 0
    tam_sayisi = 0
    eksik_sayisi = 0

    with ThreadPoolExecutor(max_workers=PARALEL_ISCI_SAYISI) as executor:
        gelecek_isler = {executor.submit(isci_gorevi, s): s["kod"] for s in evren}
        for gelecek in as_completed(gelecek_isler):
            kod, veri_paketi = gelecek.result()
            veritabani_sozluk[kod] = veri_paketi
            tamamlanan += 1

            if veri_paketi["veri_durumu"] == "TAM":
                tam_sayisi += 1
            else:
                eksik_sayisi += 1

            if tamamlanan % 75 == 0 or tamamlanan == toplam:
                yuzde = (tamamlanan / toplam) * 100
                print(f"  İlerleme: {tamamlanan}/{toplam} (%{yuzde:.1f}) | Bilanço Tam: {tam_sayisi} | Bilanço Bekleyen: {eksik_sayisi}")

    print("\n[3/3] Yerel veri tabanı 'hektor_kutuphane/bist_mali_tablolar.json' dosyasına yazılıyor...")
    
    nihai_cikti = {
        "guncelleme_tarihi": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "toplam_sirket": toplam,
        "bilancosu_tam_sirket": tam_sayisi,
        "bilancosu_olmayan_sirket": eksik_sayisi,
        "sirketler": veritabani_sozluk
    }

    with open(CIKTI_YOLU, "w", encoding="utf-8") as f:
        json.dump(nihai_cikti, f, ensure_ascii=False, indent=2)

    sure = round(time.time() - baslangic, 1)
    print("=" * 65)
    print(f"TAMAMLANDI! Süre: {sure} saniye.")
    print(f"Veri Tabanı: {os.path.abspath(CIKTI_YOLU)}")
    print(f"Bilanço Çekilen Net Şirket: {tam_sayisi} adet.")
    print("=" * 65)


if __name__ == "__main__":
    main()