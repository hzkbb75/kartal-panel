import os
import re
import csv
import subprocess
import time
import json
import hashlib
import math
import shutil
import warnings
import requests
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import portfoy_motoru

warnings.filterwarnings("ignore")

# ==============================================================================
# 0. AYARLAR, KLASÖR YAPISI VE YEREL VERİ AMBARLARI
# ==============================================================================
ROOT_DIR = "hektor_kutuphane"
SIRKETLER_DIR = os.path.join(ROOT_DIR, "SIRKETLER")
PORTFOY_DIR = os.path.join(ROOT_DIR, "PORTFOY")
PANO_YOLU = os.path.join(PORTFOY_DIR, "index.html")
GELEN_RAPOR_DIR = os.path.join(PORTFOY_DIR, "gelen_raporlar")
ARSIV_RAPOR_DIR = os.path.join(PORTFOY_DIR, "arsiv_raporlar")

PORTFOY_CSV_YOLU = os.path.join(PORTFOY_DIR, "portfoyum.csv")
DINAMIK_MASA_CSV_YOLU = os.path.join(PORTFOY_DIR, "portfoy_kalkani_dinamik.csv")
BIST_MALI_TABLOLAR_YOLU = os.path.join(ROOT_DIR, "bist_mali_tablolar.json")

for d in [SIRKETLER_DIR, PORTFOY_DIR, GELEN_RAPOR_DIR, ARSIV_RAPOR_DIR]:
    os.makedirs(d, exist_ok=True)

# 1. BIST Mali Tablolarını Yükle
YEREL_MALI_VERITABANI = {}
if os.path.exists(BIST_MALI_TABLOLAR_YOLU):
    try:
        with open(BIST_MALI_TABLOLAR_YOLU, "r", encoding="utf-8") as f:
            ham_vt = json.load(f)
            YEREL_MALI_VERITABANI = ham_vt.get("sirketler", {})
    except Exception as e:
        print(f"  [Uyarı] Yerel mali veri tabanı okunamadı: {e}")
# 1.1 Ziraat Resmi Analiz Tabanını Yükle (Ground Truth)
BIST_ANALIZ_TABANI = {}
ANALIZ_TABANI_YOLU = "bist_analiz_tabani.json"
if os.path.exists(ANALIZ_TABANI_YOLU):
    try:
        with open(ANALIZ_TABANI_YOLU, "r", encoding="utf-8") as f:
            BIST_ANALIZ_TABANI = json.load(f)
            print(f"  [Bilgi] 'bist_analiz_tabani.json' başarıyla yüklendi ({len(BIST_ANALIZ_TABANI)} şirket).")
    except Exception as e:
        print(f"  [Uyarı] bist_analiz_tabani.json okunamadı: {e}")
# 1.2 Kusursuz Sektör Sözlüğünü Yükle
BIST_SOZLUK = {}
SOZLUK_YOLU = "bist_sozluk.json"
if os.path.exists(SOZLUK_YOLU):
    try:
        with open(SOZLUK_YOLU, "r", encoding="utf-8") as f:
            BIST_SOZLUK = json.load(f)
            print(f"  [Bilgi] 'bist_sozluk.json' başarıyla yüklendi ({len(BIST_SOZLUK)} şirket).")
    except Exception as e:
        print(f"  [Uyarı] bist_sozluk.json okunamadı: {e}")

# 1.3 Ziraat Bülteni Verilerinden Gerçek Sektörel Medyan F/K Haritasını Üret
SEKTOR_FK_HARITASI = {}
if BIST_ANALIZ_TABANI:
    _sektor_fk_listeleri = {}
    for _k, _v in BIST_ANALIZ_TABANI.items():
        _fk_val = float(_v.get("fk", 0.0) or 0.0)
        if 1.0 <= _fk_val <= 80.0:
            _sek = (BIST_SOZLUK.get(_k, {}).get("sektor") or _v.get("sektor") or "Sanayi").strip()
            if _sek and _sek not in ("ALT PAZAR", "YAKIN İZLEME PAZARI", "DİĞER"):
                _sektor_fk_listeleri.setdefault(_sek, []).append(_fk_val)
    for _sek, _liste in _sektor_fk_listeleri.items():
        _sirali_fk = sorted(_liste)
        _orta = len(_sirali_fk) // 2
        _medyan = _sirali_fk[_orta] if len(_sirali_fk) % 2 == 1 else (_sirali_fk[_orta - 1] + _sirali_fk[_orta]) / 2.0
        SEKTOR_FK_HARITASI[_sek] = round(_medyan, 2)

# 2. 60 Günlük Teknik Kanal & R:R Veri Ambarını Yükle (Yoksa veya Eksikse Canlı Hesapla)
KAP_VE_TEKNIK_YOLLARI = [
    "kap_ve_teknik.json",
    os.path.join(ROOT_DIR, "kap_ve_teknik.json"),
    os.path.join(PORTFOY_DIR, "kap_ve_teknik.json")
]
KAP_VE_TEKNIK_AMBARI = {}
for yol in KAP_VE_TEKNIK_YOLLARI:
    if os.path.exists(yol):
        try:
            with open(yol, "r", encoding="utf-8") as f:
                KAP_VE_TEKNIK_AMBARI = json.load(f)
                if KAP_VE_TEKNIK_AMBARI.get("teknik_veriler"):
                    print(f"  [Bilgi] 'kap_ve_teknik.json' başarıyla hafızaya alındı ({yol}).")
                    break
        except Exception as e:
            print(f"  [Uyarı] kap_ve_teknik.json okunamadı: {e}")


def guncelle_canli_teknik_kanal_ambari() -> dict:
    """
    TradingView BIST Scanner API üzerinden tek seferde tüm BIST hisselerinin
    60 işlem günü (3 Ay) Destek, Direnç, Kanal Konumu (%), R:R, 20G/30G Hacim Çarpanı
    ve BIST 100 Göreceli Güç (1 Ay) verilerini hesaplar ve kap_ve_teknik.json'a yazar.
    """
    global KAP_VE_TEKNIK_AMBARI
    mevcut_tv = KAP_VE_TEKNIK_AMBARI.get("teknik_veriler", {})
    guncel_tarih_str = datetime.now().strftime("%Y-%m-%d")
    if len(mevcut_tv) >= 400 and str(KAP_VE_TEKNIK_AMBARI.get("guncelleme", "")).startswith(guncel_tarih_str):
        return KAP_VE_TEKNIK_AMBARI

    url = "https://scanner.tradingview.com/turkey/scan"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*"
    }
    payload = {
        "filter": [{"left": "exchange", "operation": "equal", "right": "BIST"}],
        "options": {"lang": "tr"},
        "markets": ["turkey"],
        "columns": [
            "name", "close", "High.3M", "Low.3M", "High.1M", "Low.1M",
            "Perf.1M", "volume", "average_volume_30d_calc"
        ],
        "sort": {"sortBy": "name", "sortOrder": "asc"},
        "range": [0, 950]
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code == 200:
            satirlar = r.json().get("data", [])
            ham_liste = []
            xu100_perf = None
            perf_listesi = []

            for s in satirlar:
                d = s.get("d", [])
                if not d or not d[0]:
                    continue
                kod = str(d[0]).strip().upper()
                fiyat = float(d[1] or 0.0)
                if fiyat <= 0:
                    continue
                p1m = float(d[6] or 0.0)
                if kod == "XU100":
                    xu100_perf = p1m
                perf_listesi.append(p1m)
                ham_liste.append((kod, fiyat, d))

            if xu100_perf is None and perf_listesi:
                sirali_p = sorted(perf_listesi)
                xu100_perf = sirali_p[len(sirali_p) // 2]
            xu100_perf = float(xu100_perf or 0.0)

            yeni_teknik = {}
            for kod, fiyat, d in ham_liste:
                h3m = float(d[2] or d[4] or (fiyat * 1.15))
                l3m = float(d[3] or d[5] or (fiyat * 0.88))
                direnc_60g = round(max(h3m, fiyat * 1.01), 2)
                destek_60g = round(min(l3m, fiyat * 0.99), 2)
                if destek_60g <= 0:
                    destek_60g = round(fiyat * 0.88, 2)

                kanal_boyu = max(direnc_60g - destek_60g, fiyat * 0.01, 0.01)
                kanal_konumu = round(max(0.0, min(100.0, ((fiyat - destek_60g) / kanal_boyu) * 100.0)), 1)

                risk_yuzde = round(max(0.5, ((fiyat - destek_60g) / fiyat) * 100.0), 1)
                odul_yuzde = round(max(0.5, ((direnc_60g - fiyat) / fiyat) * 100.0), 1)
                rr_orani = round(odul_yuzde / max(risk_yuzde, 0.5), 2)

                vol = float(d[7] or 0.0)
                avg_vol = float(d[8] or 0.0)
                hacim_kati = round(vol / avg_vol, 2) if avg_vol > 0 else 1.0

                p1m = float(d[6] or 0.0)
                rs_bist_fark = round(p1m - xu100_perf, 1)

                if kanal_konumu <= 25.0:
                    durum = "🟢 60G DESTEK BÖLGESİ (ALIM FIRSATI)"
                    renk = "#10b981"
                elif kanal_konumu <= 65.0:
                    durum = "🔵 KANAL ORTA BANT (DENGELİ)"
                    renk = "#38bdf8"
                elif kanal_konumu <= 85.0:
                    durum = "🟡 DİRENCE YAKIN (DİKKATLİ İZLE)"
                    renk = "#f59e0b"
                else:
                    durum = "🔴 60G DİRENÇ TAVANI (PRİMLİ)"
                    renk = "#ef4444"

                yeni_teknik[kod] = {
                    "fiyat": round(fiyat, 2),
                    "destek_60g": destek_60g,
                    "direnc_60g": direnc_60g,
                    "kanal_konumu": kanal_konumu,
                    "risk_yuzde": risk_yuzde,
                    "odul_yuzde": odul_yuzde,
                    "rr_orani": rr_orani,
                    "hacim_kati": hacim_kati,
                    "rs_bist_fark": rs_bist_fark,
                    "durum": durum,
                    "renk": renk
                }

            if yeni_teknik:
                KAP_VE_TEKNIK_AMBARI["guncelleme"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                KAP_VE_TEKNIK_AMBARI["teknik_veriler"] = yeni_teknik
                with open("kap_ve_teknik.json", "w", encoding="utf-8") as f:
                    json.dump(KAP_VE_TEKNIK_AMBARI, f, ensure_ascii=False, indent=2)
                print(f"  [Bilgi] 60 Günlük Teknik Kanal & R:R Ambarı canlı güncellendi ({len(yeni_teknik)} hisse).")
    except Exception as e:
        print(f"  [Uyarı] Canlı teknik kanal verisi çekilemedi: {e}")
    return KAP_VE_TEKNIK_AMBARI

guncelle_canli_teknik_kanal_ambari()

# 3. Yerel KAP Haberleri Ambarını Yükle (Tüm BİST)
YEREL_KAP_DOSYASI = os.path.join(ROOT_DIR, "tum_kap_haberleri.json")
TUM_KAP_HABERLERI = {}
if os.path.exists(YEREL_KAP_DOSYASI):
    try:
        with open(YEREL_KAP_DOSYASI, "r", encoding="utf-8") as f:
            TUM_KAP_HABERLERI = json.load(f).get("kap_haberleri", {})
            print(f"  [Bilgi] 'tum_kap_haberleri.json' başarıyla yüklendi ({len(TUM_KAP_HABERLERI)} şirket).")
    except Exception as e:
        print(f"  [Uyarı] tum_kap_haberleri.json okunamadı: {e}")

BANKA_VE_FINANS_KODLARI = {
    "GARAN", "AKBNK", "ISCTR", "YKBNK", "VAKBN", "HALKB", "TSKB", "SKBNK",
    "ALBRK", "QNBFB", "QNBTR", "KLNMA", "ICBCT", "TURSG", "AGESA", "ANHYT",
    "ANSGR", "AKGRT", "RAYSG", "DSTKF"
}
SADECE_ENDEKS_PARA_HARIC = {"BIST", "XU100", "XU030", "TRY", "USD", "EUR", "XU050", "XUMKR", "XUTUM", "XUSIN", "XBANK", "NAKIT", "KASA", "PPF"}


def tr_norm(metin: str) -> str:
    if not metin:
        return ""
    ceviri = str.maketrans("İIŞĞÜÖÇıişğüöç", "iisguociisguoc")
    return re.sub(r"\s+", " ", metin.translate(ceviri).lower()).strip()


def satir_bul(satirlar: list, aranan_kelimeler: list, mod: str = "ilk") -> tuple:
    norm_arananlar = [tr_norm(k) for k in aranan_kelimeler]

    def _yasakli_satir_mi(ad: str, aranan: str) -> bool:
        # "donem kari vergi yukumlulugu" satırının "donem kari" aramasında net kâr sanılmasını engelle
        if any(k in aranan for k in ["donem kar", "donem net kar", "ana ortaklik paylari"]):
            if any(y in ad for y in ["vergi", "yukumluluk", "karsilik", "dagilimi", "onceki"]):
                return True
        # "finansman gideri oncesi faaliyet kari" satırının esas faaliyet kârı sanılmasını engelle
        if aranan in ("esas faaliyet kari", "faaliyet kari") and "finansman gideri oncesi" in ad:
            return True
        return False

    if mod == "ilk":
        # 1. Öncelik: Aranan kelimelerin sırasıyla TAM EŞLEŞME (ad == aranan) kontrolü
        for aranan in norm_arananlar:
            for satir in satirlar:
                ad = satir[0]
                if _yasakli_satir_mi(ad, aranan):
                    continue
                v1 = float(satir[1] or 0.0)
                v2 = float(satir[2] or 0.0)
                if ad == aranan and (v1 != 0.0 or v2 != 0.0):
                    return v1, v2
        # 2. Öncelik: Aranan kelimelerin sırasıyla ALT METİN (aranan in ad) kontrolü
        for aranan in norm_arananlar:
            for satir in satirlar:
                ad = satir[0]
                if _yasakli_satir_mi(ad, aranan):
                    continue
                v1 = float(satir[1] or 0.0)
                v2 = float(satir[2] or 0.0)
                if aranan in ad and (v1 != 0.0 or v2 != 0.0):
                    return v1, v2
        return 0.0, 0.0

    toplam_c, toplam_o = 0.0, 0.0
    bulundu = False
    for satir in satirlar:
        ad = satir[0]
        v1 = float(satir[1] or 0.0)
        v2 = float(satir[2] or 0.0)
        for aranan in norm_arananlar:
            if aranan in ad or ad == aranan:
                if v1 != 0.0 or v2 != 0.0:
                    toplam_c += v1
                    toplam_o += v2
                    bulundu = True
                    break
    if bulundu:
        return toplam_c, toplam_o
    return 0.0, 0.0


# ==============================================================================
# 1. BİRİKİMLİ DİPNOT VE RASYOLAR MOTORU
# ==============================================================================
def rasyonel_degerleme_hesapla(fiyat, hbk, dd, roe, sektor_fk=10.0, sermaye_maliyeti=0.45):
    try:
        fiyat = float(fiyat)
        hbk = float(hbk)
        dd = float(dd)
        roe = float(roe) / 100.0
        
        v_graham = 0
        if hbk > 0 and dd > 0:
            v_graham = math.sqrt(22.5 * hbk * dd)
            
        v_roe = 0
        if dd > 0 and roe > 0:
            hak_edilen_pddd = roe / sermaye_maliyeti
            v_roe = dd * hak_edilen_pddd
            
        v_sektor = 0
        if hbk > 0:
            v_sektor = hbk * sektor_fk
            
        gecerli_degerler = [v for v in [v_graham, v_roe, v_sektor] if v > 0]
        if not gecerli_degerler:
            return 0.0, 0.0, "Hesaplanamadı"
            
        adil_deger = sum(gecerli_degerler) / len(gecerli_degerler)
        iskonto_orani = ((fiyat - adil_deger) / adil_deger) * 100
        
        if iskonto_orani <= -25:
            durum = "KELEPİR"
        elif iskonto_orani <= 15:
            durum = "ADİL DEĞER"
        else:
            durum = "PRİMLİ"
            
        return round(adil_deger, 2), round(iskonto_orani, 1), durum
    except (ValueError, TypeError, ZeroDivisionError):
        return 0.0, 0.0, "Hata"


def _donem_gun_ve_yillik_carpan(donem_adi: str) -> tuple:
    """
    UFRS gelir tablosu kümülatif dönemine göre (03, 06, 09, 12)
    gerçek gün sayısını ve yıllıklandırma çarpanını döndürür.
    """
    if not donem_adi or "/" not in str(donem_adi):
        return 365.0, 1.0
    ay_str = str(donem_adi).split("/")[-1].strip()
    if ay_str in ("03", "3"):
        return 91.25, 4.0
    elif ay_str in ("06", "6"):
        return 182.5, 2.0
    elif ay_str in ("09", "9"):
        return 273.75, (12.0 / 9.0)
    return 365.0, 1.0


def _kisa_tutar_metni(deger_mn: float) -> str:
    if abs(deger_mn) >= 1000.0:
        return f"{deger_mn / 1000.0:,.2f} Mr ₺"
    return f"{deger_mn:,.0f} Mn ₺"


def hesapla_donem_dipnot_metrikleri(satirlar_cari: list, satirlar_onceki: list = None, donem_adi: str = "2026/06", onceki_donem_adi: str = None) -> dict:
    onceki = satirlar_onceki if satirlar_onceki else satirlar_cari
    donem_gun, yillik_carpan = _donem_gun_ve_yillik_carpan(donem_adi)
    _, onceki_yillik_carpan = _donem_gun_ve_yillik_carpan(onceki_donem_adi or donem_adi)

    satis_c = satir_bul(satirlar_cari, ["hasilat", "satis gelirleri", "faiz, ucret, prim, komisyon ve diger gelirler", "faiz gelirleri"])[0]
    satis_o = satir_bul(onceki, ["hasilat", "satis gelirleri", "faiz, ucret, prim, komisyon ve diger gelirler", "faiz gelirleri"])[0]
    efk_c = satir_bul(satirlar_cari, ["esas faaliyet kari", "faaliyet kari"])[0]
    amort_c = abs(satir_bul(satirlar_cari, ["amortisman giderleri", "amortisman & itfa paylari"])[0])
    net_c = satir_bul(satirlar_cari, ["donem net kar/zarari", "donem net kar", "ana ortaklik paylari", "donem kari"])[0]
    cfo_c = satir_bul(satirlar_cari, ["isletme faaliyetlerinden nakit akislari", "isletme faaliyetlerinden", "faaliyetlerden nakit", "kaynaklanan net nakit"])[0]

    _donen_d = satir_bul(satirlar_cari, ["donen varliklar", "toplam donen varliklar"])[0]
    _duran_d = satir_bul(satirlar_cari, ["duran varliklar", "toplam duran varliklar"])[0]
    _ozk_d = satir_bul(satirlar_cari, ["ana ortakliga ait ozkaynaklar", "toplam ozkaynaklar", "ozkaynaklar"])[0]
    _kv_d = satir_bul(satirlar_cari, ["kisa vadeli yukumlulukler", "toplam kisa vadeli yukumlulukler"])[0]
    _uv_d = satir_bul(satirlar_cari, ["uzun vadeli yukumlulukler", "toplam uzun vadeli yukumlulukler"])[0]
    varlik_c = max(satir_bul(satirlar_cari, ["toplam varliklar", "toplam aktifler"])[0], _donen_d + _duran_d, _ozk_d + _kv_d + _uv_d, 1.0)
    ozk_c = max(satir_bul(satirlar_cari, ["ana ortakliga ait ozkaynaklar", "toplam ozkaynaklar", "ozkaynaklar"])[0], 1.0)
    nakit_c = satir_bul(satirlar_cari, ["nakit ve nakit benzerleri", "nakit degerler"])[0]
    fin_borc_c = satir_bul(satirlar_cari, ["finansal borclar"], mod="topla")[0]
    fin_borc_o = satir_bul(onceki, ["finansal borclar"], mod="topla")[0]
    fin_gider_c = abs(satir_bul(satirlar_cari, ["finansman giderleri", "(esas faaliyet disi) finansal giderler", "esas faaliyet disi finansal giderler"])[0])
    fin_gelir_c = abs(satir_bul(satirlar_cari, ["(esas faaliyet disi) finansal gelirler", "esas faaliyet disi finansal gelirler"])[0])

    alacak_c = satir_bul(satirlar_cari, ["ticari alacaklar"])[0]
    alacak_o = satir_bul(onceki, ["ticari alacaklar"])[0]
    iliskili_alacak_c = satir_bul(satirlar_cari, ["iliskili taraflardan diğer alacaklar", "iliskili taraflardan ticari olmayan alacaklar"], mod="topla")[0]
    modv_c = satir_bul(satirlar_cari, ["maddi olmayan duran varliklar"])[0]
    modv_o = satir_bul(onceki, ["maddi olmayan duran varliklar"])[0]
    serefiye_c = satir_bul(satirlar_cari, ["serefiye"])[0]
    kagit_kar = max(0.0, satir_bul(satirlar_cari, ["yatirim faaliyetlerinden gelirler"])[0]) + max(0.0, satir_bul(satirlar_cari, ["ertelenmis vergi geliri"])[0])
    karsiliklar_c = satir_bul(satirlar_cari, ["borc karsiliklari", "kisa vadeli karsiliklar", "uzun vadeli karsiliklar", "calisanlara saglanan"], mod="topla")[0]

    # Net Finansman Gideri ve Operasyonel FAVÖK (EFK + Amortisman) bazlı gerçekçi ICR & Yıllıklandırılmış Borç Maliyeti
    net_fin_gider_c = max(fin_gider_c - fin_gelir_c, fin_gider_c * 0.35) if fin_gider_c > 0 else 0.0
    ort_borc = max((fin_borc_c + fin_borc_o) / 2.0, 1.0)
    efm_yuzde = round(((net_fin_gider_c * yillik_carpan) / ort_borc) * 100.0, 1) if fin_borc_c > 0 else 0.0
    operasyonel_nakit_kar = max(efk_c + amort_c, efk_c)
    icr_kati = round(operasyonel_nakit_kar / max(net_fin_gider_c, 1.0), 2) if net_fin_gider_c > 0 else 99.0

    tahakkuk_orani = round(((net_c - cfo_c) / varlik_c) * 100.0, 2)
    aga_orani = round((max(0.0, modv_c - modv_o) / max(efk_c, 1.0)) * 100.0, 1) if efk_c > 0 else 0.0
    
    if efk_c > 0:
        ykp_orani = min(100.0, round((kagit_kar / max(efk_c + kagit_kar, 1.0)) * 100.0, 1))
    else:
        ykp_orani = min(100.0, round((kagit_kar / max(varlik_c, 1.0)) * 100.0, 1))

    # Kümülatif dönem gün sayısına (91.25 / 182.5 / 273.75 / 365) göre gerçek DSO (Alacak Tahsil Süresi)
    dso_gun = round(donem_gun * (alacak_c / max(satis_c, 1.0)), 1) if satis_c > 0 else 0.0
    satis_c_yillik = satis_c * yillik_carpan
    satis_o_yillik = satis_o * onceki_yillik_carpan
    alacak_buyume = ((alacak_c / max(alacak_o, 1.0)) - 1.0) * 100.0 if alacak_o > 0 else 0.0
    satis_buyume = ((satis_c_yillik / max(satis_o_yillik, 1.0)) - 1.0) * 100.0 if satis_o_yillik > 0 else 0.0
    acm_makasi = round(alacak_buyume - satis_buyume, 1)

    serefiye_ozk_orani = round((serefiye_c / ozk_c) * 100.0, 1)
    karsilik_ozk_orani = round((karsiliklar_c / ozk_c) * 100.0, 1)
    iliskili_orani = round((iliskili_alacak_c / varlik_c) * 100.0, 2)

    # 4 Çeyreklik Kurumsal Denetim Matrisi İçin Otomatik UFRS Dipnot Röntgeni Üretimi
    mdv_c_val = satir_bul(satirlar_cari, ["maddi duran varliklar"])[0]
    capex_c_val = abs(satir_bul(satirlar_cari, ["sabit sermaye yatirimlari"])[0])
    ydisi_satis_c = satir_bul(satirlar_cari, ["yurtdisi satislar"])[0]
    avans_c_val = satir_bul(satirlar_cari, ["musteri soz. dogan yuk.", "musteri soz.dogan yuk.", "ertelenmis gelirler", "alinan siparis avanslari"], mod="topla")[0]
    ypp_hedge_c = satir_bul(satirlar_cari, ["net ypp (hedge dahil)", "net yabanci para pozisyonu"])[0]
    ypp_ham_c = satir_bul(satirlar_cari, ["net yabanci para pozisyonu"])[0]

    capex_amort_kati = round(capex_c_val / amort_c, 2) if amort_c > 0 else 0.0
    yatirim_durum = "Agresif Kapasite Artışı" if capex_amort_kati >= 1.5 else ("Kapasite Korunumu / İdame" if capex_amort_kati >= 0.7 else "Düşük CAPEX / Hasat Dönemi")
    not_yatirim = f"<strong>CAPEX:</strong> {_kisa_tutar_metni(capex_c_val)} ({capex_amort_kati:.1f}x Amortisman)<br><strong>MDV:</strong> {_kisa_tutar_metni(mdv_c_val)}<br><span style='color:#34d399; font-size:10.5px;'>● {yatirim_durum}</span>"

    ihracat_payi = round((ydisi_satis_c / max(satis_c, 1.0)) * 100.0, 1) if satis_c > 0 and ydisi_satis_c > 0 else 0.0
    avans_ciro_orani = round((avans_c_val / max(satis_c_yillik, 1.0)) * 100.0, 1) if satis_c_yillik > 0 else 0.0
    not_siparis = (
        f"<strong>Sipariş Avansı:</strong> {_kisa_tutar_metni(avans_c_val)} (Yıllık Cironun %{avans_ciro_orani:.1f}'i)<br>"
        f"<strong>İhracat Payı:</strong> %{ihracat_payi:.1f} ({_kisa_tutar_metni(ydisi_satis_c)})"
    )

    ypp_ozk_orani = round((ypp_hedge_c / ozk_c) * 100.0, 1)
    ypp_renk = "#34d399" if ypp_ozk_orani >= -10.0 else "#f87171"
    ypp_etiket = "Kur Korumalı / Fazla" if ypp_hedge_c >= 0 else ("Kontrollü Açık" if ypp_ozk_orani >= -15.0 else "Yüksek Kur Riski")
    not_doviz = (
        f"<strong>Net YPP (Hedge Dahil):</strong> {_kisa_tutar_metni(ypp_hedge_c)} (ÖZK %{ypp_ozk_orani:+.1f})<br>"
        f"<strong>Ham YPP:</strong> {_kisa_tutar_metni(ypp_ham_c)} | <span style='color:{ypp_renk}; font-weight:bold;'>{ypp_etiket}</span>"
    )

    kv_ozk_kati = round(_kv_d / ozk_c, 2)
    tri_etiket = "Temiz / Düşük Karşılık" if karsilik_ozk_orani <= 10.0 else "Yüksek Karşılık Yükü"
    not_tri = (
        f"<strong>Karşılık / ÖZK:</strong> %{karsilik_ozk_orani:.1f} ({_kisa_tutar_metni(karsiliklar_c)})<br>"
        f"<strong>KV Yükümlülük / ÖZK:</strong> {kv_ozk_kati:.2f}x | <span style='color:#93c5fd;'>{tri_etiket}</span>"
    )

    otomatik_notlar = {
        "yatirim_ve_kapasite": not_yatirim,
        "siparis_ve_backlog": not_siparis,
        "doviz_ve_turev": not_doviz,
        "tri_rehin_ipotek": not_tri
    }

    ceza = 0
    uyarilar = []
    if iliskili_orani > 3.0: ceza += 20; uyarilar.append(f"🔴 İlişkili taraflardan ticari olmayan alacaklar aktifin %{iliskili_orani}'ine ulaştı.")
    if tahakkuk_orani > 5.0: ceza += 20; uyarilar.append(f"🔴 Tahakkuk anomalisi (%{tahakkuk_orani}): Kâr nakde dönüşmüyor.")
    if ykp_orani > 35.0: ceza += 15; uyarilar.append(f"🟡 Kağıt üzeri kâr payı yüksek (%{ykp_orani}).")
    if acm_makasi > 15.0 and dso_gun > 45.0: ceza += 15; uyarilar.append(f"🟡 Alacak-Ciro makası +%{acm_makasi} açıldı (DSO: {dso_gun} gün).")
    if icr_kati < 1.5 and net_fin_gider_c > 0 and fin_borc_c > nakit_c: ceza += 15; uyarilar.append(f"🔴 Faiz karşılama oranı {icr_kati}x (ICR zayıf).")

    if not uyarilar: uyarilar.append("🟢 Muhasebe makyajı veya anomali tespit edilmedi.")

    return {
        "efm_yuzde": efm_yuzde, "icr_kati": icr_kati, "tahakkuk_orani": tahakkuk_orani,
        "aga_orani": aga_orani, "ykp_orani": ykp_orani, "dso_gun": dso_gun, "acm_makasi": acm_makasi,
        "serefiye_ozk_orani": serefiye_ozk_orani, "karsilik_ozk_orani": karsilik_ozk_orani,
        "iliskili_orani": iliskili_orani, "dts_skor": min(100, ceza), "uyarilar": uyarilar,
        "pdf_dipnot_notlari": otomatik_notlar
    }


def guncelle_birikimli_dipnot_arsivi(kod: str) -> dict:
    arsiv_yolu = os.path.join(SIRKETLER_DIR, kod, "04_birikimli_dipnot_arsivi.json")
    mevcut_arsiv = {"kod": kod, "son_guncelleme": "", "donemler": {}}
    if os.path.exists(arsiv_yolu):
        try:
            with open(arsiv_yolu, "r", encoding="utf-8") as f:
                mevcut_arsiv = json.load(f)
        except Exception:
            pass

    sirket_kaydi = YEREL_MALI_VERITABANI.get(kod, {})
    cok_donem = sirket_kaydi.get("donemler", {})
    sirali_anahtarlar = sorted(cok_donem.keys())

    for idx, d_adi in enumerate(sirali_anahtarlar):
        # Elma-Elma Dönem Eşleşmesi: Önce aynı ay kodlu geçen yıl dönemini (örn. 2026/06 -> 2025/06) ara
        ay_kodu = d_adi.split("/")[-1] if "/" in d_adi else ""
        es_donem_adi = d_adi
        for k_onceki in reversed(sirali_anahtarlar[:idx]):
            if k_onceki.endswith("/" + ay_kodu):
                es_donem_adi = k_onceki
                break
        if es_donem_adi == d_adi and idx > 0:
            es_donem_adi = sirali_anahtarlar[idx - 1]

        onceki_satirlar = cok_donem[es_donem_adi]
        cari_satirlar = [(s[0], s[1], s[1]) for s in cok_donem[d_adi]]
        onceki_s = [(s[0], s[1], s[1]) for s in onceki_satirlar]

        hesaplanan = hesapla_donem_dipnot_metrikleri(cari_satirlar, onceki_s, donem_adi=d_adi, onceki_donem_adi=es_donem_adi)

        if d_adi in mevcut_arsiv.get("donemler", {}):
            eski = mevcut_arsiv["donemler"][d_adi]
            eski_notlar = eski.get("pdf_dipnot_notlari") or {}
            # Eğer PDF madencisinden özel metin geldiyse onu koru, boş kalan alanlarda UFRS dipnot röntgenini kullan
            for k_alan, v_otomatik in hesaplanan["pdf_dipnot_notlari"].items():
                eski_val = str(eski_notlar.get(k_alan, "")).strip()
                if eski_val and len(eski_val) > 5 and eski_val != "-" and "CAPEX:" not in eski_val and "Sipariş Avansı:" not in eski_val and "Net YPP" not in eski_val and "Karşılık / ÖZK:" not in eski_val:
                    hesaplanan["pdf_dipnot_notlari"][k_alan] = eski_val
            if eski.get("tutarlilik_raporu"):
                hesaplanan["tutarlilik_raporu"] = eski["tutarlilik_raporu"]

        mevcut_arsiv.setdefault("donemler", {})[d_adi] = hesaplanan

    tum_adlar = sorted(mevcut_arsiv["donemler"].keys())
    for i, d_adi in enumerate(tum_adlar):
        curr = mevcut_arsiv["donemler"][d_adi]
        if i > 0:
            prev = mevcut_arsiv["donemler"][tum_adlar[i - 1]]
            curr["dts_delta"] = curr["dts_skor"] - prev["dts_skor"]
            curr["dso_delta"] = round(curr["dso_gun"] - prev["dso_gun"], 1)
        else:
            curr["dts_delta"], curr["dso_delta"] = 0, 0.0

    # Son çeyrek için otomatik Yönetim Vaatleri & Tutarlılık Karnesi özeti
    if tum_adlar:
        son_d = mevcut_arsiv["donemler"][tum_adlar[-1]]
        if not son_d.get("tutarlilik_raporu"):
            son_d["tutarlilik_raporu"] = list(son_d.get("uyarilar", []))

    mevcut_arsiv["son_guncelleme"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(arsiv_yolu), exist_ok=True)
    with open(arsiv_yolu, "w", encoding="utf-8") as f:
        json.dump(mevcut_arsiv, f, ensure_ascii=False, indent=2)
    return mevcut_arsiv


def degerleme_termometresi_uret(hisse_kodu, mevcut_fiyat, adil_deger, iskonto_orani, durum):
    if adil_deger == 0.0:
        return ""
        
    if iskonto_orani <= -25:
        bar_color = "#10b981"
        mesaj = f"✅ <strong>Kelepir Fırsat:</strong> Hisse, adil değerinin <strong>%{abs(iskonto_orani):.1f} altında</strong> (iskontolu) fiyatlanıyor. Yüksek güvenlik marjı."
        bg_alert = "rgba(16, 185, 129, 0.1)"
        border_alert = "#10b981"
        text_alert = "#34d399"
    elif iskonto_orani <= 15:
        bar_color = "#f59e0b" 
        mesaj = f"⚖️ <strong>Dengeli:</strong> Mevcut fiyat adil değer sınırları içinde. (Kısa sapma: %{iskonto_orani:+.1f})"
        bg_alert = "rgba(245, 158, 11, 0.1)"
        border_alert = "#f59e0b"
        text_alert = "#fbbf24"
    else:
        bar_color = "#ef4444" 
        mesaj = f"⚠️ <strong>Pahalılık Uyarısı:</strong> Mevcut fiyat adil değerin <strong>%{iskonto_orani:.1f} üzerinde</strong> işlem görüyor. Alım için güvenlik marjı (%25 iskonto) oluşması beklenmeli."
        bg_alert = "rgba(239, 68, 68, 0.1)"
        border_alert = "#ef4444"
        text_alert = "#fca5a5"

    if iskonto_orani <= -25.0:
        # Kelepir Bölge (0% - 35% arası): -75% -> 5%, -25% -> 35%
        ibre_pozisyonu = 35.0 + ((iskonto_orani + 25.0) / 50.0) * 30.0
    elif iskonto_orani <= 15.0:
        # Adil Değer Bölgesi (35% - 70% arası): -25% -> 35%, +15% -> 70%
        ibre_pozisyonu = 35.0 + ((iskonto_orani + 25.0) / 40.0) * 35.0
    else:
        # Primli Bölge (70% - 95% arası): +15% -> 70%, +65% -> 95%
        ibre_pozisyonu = 70.0 + ((iskonto_orani - 15.0) / 50.0) * 25.0
    ibre_pozisyonu = round(max(5.0, min(95.0, ibre_pozisyonu)), 1)

    kelepir_sinir = round(adil_deger * 0.75, 2)
    primli_sinir = round(adil_deger * 1.15, 2)

    return f'''
    <div style="background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 20px; color: #fff; width: 100%; margin-top: 15px; font-family: sans-serif; box-sizing: border-box;">
      <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px;">
        <div>
          <span style="font-size: 18px; font-weight: 700; color: #38bdf8;">{hisse_kodu} Değerleme Karnesi</span>
          <div style="font-size: 13px; color: #9ca3af; margin-top: 2px;">Rasyonel Konsensüs Modeli (Graham + ROE + Çarpan)</div>
        </div>
        <div style="text-align: right;">
          <div style="font-size: 13px; color: #9ca3af;">Adil Değer</div>
          <div style="font-size: 18px; font-weight: 700; color: {bar_color};">{adil_deger} TL</div>
        </div>
      </div>

      <div style="position: relative; margin: 25px 0 15px 0;">
        <div style="height: 12px; border-radius: 6px; background: linear-gradient(to right, #10b981 0%, #10b981 35%, #f59e0b 35%, #f59e0b 70%, #ef4444 70%, #ef4444 100%); width: 100%;"></div>
        
        <div style="position: absolute; left: {ibre_pozisyonu}%; top: -8px; transform: translateX(-50%); display: flex; flex-direction: column; align-items: center;">
          <div style="background: #ffffff; color: #000; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px; box-shadow: 0 2px 5px rgba(0,0,0,0.5); white-space: nowrap;">
            {mevcut_fiyat} TL (Mevcut)
          </div>
          <div style="width: 2px; height: 16px; background: #ffffff; margin-top: 2px;"></div>
        </div>
      </div>

      <div style="display: flex; justify-content: space-between; font-size: 11px; color: #6b7280; margin-bottom: 15px;">
        <span style="color: #10b981;">● Kelepir (< {kelepir_sinir} TL)</span>
        <span style="color: #f59e0b;">● Adil Aralık ({kelepir_sinir} - {primli_sinir} TL)</span>
        <span style="color: #ef4444;">● Primli (> {primli_sinir} TL)</span>
      </div>

      <div style="background: {bg_alert}; border-left: 4px solid {border_alert}; padding: 10px 12px; border-radius: 4px; font-size: 12px; color: {text_alert};">
        {mesaj}
      </div>
    </div>
    '''


# ==============================================================================
# 2. ŞİRKET ELEME, 6 KÜMELİ SEKTÖREL ROE VE 4 KATMANLI HİBRİT PUAN MOTORU
# ==============================================================================
def sektor_roe_normu_getir(sektor_adi: str) -> tuple:
    """
    6 Kümeli DuPont Sektörel ROE Matrisi (Python & JS Tam Senkronize).
    Döndürür: (min_norm, ust_norm, kume_adi)
    """
    sek_l = tr_norm(str(sektor_adi or ""))
    if any(x in sek_l for x in ["perakende", "magaza", "gida", "icecek"]):
        return 35.0, 60.0, "Organize Perakende & Tüketim"
    elif any(x in sek_l for x in ["yazilim", "bilisim", "teknoloji", "savunma", "telekom"]):
        return 30.0, 50.0, "Hafif Varlık & Teknoloji"
    elif any(x in sek_l for x in ["banka", "sigorta", "faktoring", "finans", "menkul", "emeklilik"]):
        return 22.0, 38.0, "Banka & Finans"
    elif any(x in sek_l for x in ["demir", "celik", "petrol", "kimya", "cimento", "otomotiv", "maden"]):
        return 15.0, 30.0, "Ağır Sanayi & Döngüsel"
    elif any(x in sek_l for x in ["gyo", "gayrimenkul", "insaat", "turizm", "enerji", "otel", "tarim"]):
        return 8.0, 20.0, "Varlık Yoğun & Düşük Devir"
    elif any(x in sek_l for x in ["holding"]):
        return 18.0, 32.0, "Holding & İştirak Grubu"
    return 15.0, 30.0, "Ağır Sanayi & Üretim"


def hesapla_hibrit_kartal_puani(s: dict, teknik_veri: dict = None) -> float:
    """
    Serkan Ünal 4 Katmanlı Hibrit Puan Motoru (0 - 100 Puan):
      1. Bilanço & Nakit Kalitesi (F-Skor + Borçsuzluk): Maks 20 Puan
      2. Sektör Duyarlı DuPont ROE & Hendek Sınıfı: Maks 25 Puan
      3. Rasyonel Değerleme Güvenlik Marjı (Bölüm 4) & Dipnot Temizliği (Bölüm 3): Maks 20 Puan
      4. 60G Teknik Kanal Konumu & BIST Göreceli Güç (RS): Maks 35 Puan
    """
    # KATMAN 1: F-Skor (Maks 9p) + Nakit Dönüşüm Kalitesi CFO>NetKâr (Maks 5p) + Net Borç/ÖZK Zırhı (Maks 6p) = 20 Puan
    f_skor = int(s.get("piotroski_skor", 5))
    katman1 = float(f_skor)

    if s.get("cfo_buyuk_net_kar", False):
        katman1 += 5.0
    elif float(s.get("cfo_milyon_tl", 0.0)) > 0:
        katman1 += 2.0

    nb_ozk = float(s.get("net_borc_ozkaynak", 1.0))
    if nb_ozk <= 0.10: katman1 += 6.0
    elif nb_ozk <= 0.50: katman1 += 4.5
    elif nb_ozk <= 1.00: katman1 += 2.0

    # KATMAN 2: 6 Kümeli Sektörel ROE (Maks 17p) + Hendek Sınıfı (Maks 8p) = 25 Puan
    roe = float(s.get("roe", 0.0))
    min_roe, ust_roe, _ = sektor_roe_normu_getir(s.get("sektor", ""))
    katman2 = 0.0
    if roe < 5.0:
        katman2 -= 10.0  # Reel sermaye erimesi cezası
    elif roe > ust_roe:
        katman2 += 17.0  # Sektör normunun üzerinde üstün sermaye verimi
    elif roe >= min_roe:
        katman2 += 13.0  # Sektör normuyla tam uyumlu
    else:
        katman2 += 5.0   # Sektör normunun altında pozitif ROE

    hendek = s.get("kategori", "Hendeksiz")
    if hendek == "Korunaklı": katman2 += 8.0
    elif hendek == "Kırılgan Hendekli": katman2 += 4.0

    # KATMAN 3: Değerleme Güvenlik Marjı (Maks 15p) + Dipnot Kalitesi (Maks 5p / Ceza -15p) = 20 Puan
    katman3 = 0.0
    adil_deger = float(s.get("adil_deger_tl", 0.0))
    iskonto = float(s.get("iskonto_orani", 0.0))
    fk = float(s.get("fk", 0.0))

    if adil_deger > 0:
        if iskonto <= -25.0: katman3 += 15.0     # Kelepir (Yüksek güvenlik marjı)
        elif iskonto <= 0.0: katman3 += 11.0     # Adil değerin altında iskontolu
        elif iskonto <= 15.0: katman3 += 7.0     # Adil değer bandında
        elif iskonto <= 35.0: katman3 += 2.0     # Hafif primli
        else: katman3 -= 5.0                     # Aşırı primli / güvenlik marjı yok
    elif 0 < fk <= 7.0:
        katman3 += 10.0
    elif 0 < fk <= 12.0:
        katman3 += 6.0

    dts_skor = int(s.get("dts_skor", 0))
    if dts_skor == 0: katman3 += 5.0
    elif dts_skor <= 15: katman3 += 2.0
    elif dts_skor >= 50: katman3 -= 15.0
    elif dts_skor >= 35: katman3 -= 8.0

    # KATMAN 4: 60G Teknik Kanal Desteği (Maks 30p) + BIST 100 Göreceli Güç (Maks 5p) = 35 Puan
    katman4 = 17.5  # Teknik veri yoksa nötr
    if teknik_veri:
        konum = float(teknik_veri.get("kanal_konumu", 50.0))
        if konum <= 20.0: katman4 = 30.0
        elif konum <= 35.0: katman4 = 24.0
        elif konum <= 50.0: katman4 = 18.0
        elif konum <= 70.0: katman4 = 10.0
        else: katman4 = 0.0

        rs_fark = float(teknik_veri.get("rs_bist_fark", 0.0))
        if rs_fark > 0 and konum <= 55.0:
            katman4 += 5.0
        elif rs_fark >= -3.0 and konum <= 40.0:
            katman4 += 2.5

    toplam_puan = max(0.0, min(100.0, round(katman1 + katman2 + katman3 + katman4, 1)))
    return toplam_puan

def analiz_et_sirket(kod: str) -> dict:
    sirket_kaydi = YEREL_MALI_VERITABANI.get(kod)

    # 1. DÜZELTME: Sektor değişkenini en başta tanımlıyoruz!
    sektor = sirket_kaydi.get("sektor", "Sanayi") if sirket_kaydi else "BIST"

    if not sirket_kaydi:
        return {"kod": kod, "bolum1_gecti_mi": False, "cfo_buyuk_net_kar": False, "piotroski_skor": 0}

    halka_aciklik = float(sirket_kaydi.get("halka_aciklik", 38.0))
    banka_mi = (kod in BANKA_VE_FINANS_KODLARI) or ("banka" in tr_norm(sirket_kaydi.get("sektor", ""))) or ("sigorta" in tr_norm(sirket_kaydi.get("sektor", "")))
    satirlar = sirket_kaydi.get("satirlar_cari_onceki", [])

    fiyat = float(sirket_kaydi.get("fiyat", 0.0))
    fk = float(sirket_kaydi.get("fk", 0.0))
    fd_favok = float(sirket_kaydi.get("fd_favok", 0.0))
    pd_milyar = float(sirket_kaydi.get("piyasa_degeri_milyar", 0.0))

    if not satirlar or sirket_kaydi.get("veri_durumu") == "VERI_YOK":
        return {
            "kod": kod, "sirket_adi": sirket_kaydi.get("sirket_adi", kod), "sektor": sirket_kaydi.get("sektor", "BIST"),
            "kategori": "Hendeksiz", "bolum1_gecti_mi": False, "cfo_buyuk_net_kar": False, "piotroski_skor": 0,
            "fiyat_tl": fiyat, "halka_aciklik": halka_aciklik, "net_borc_ozkaynak": 0.0
        }

    satis_c, satis_o = satir_bul(satirlar, ["hasilat", "satis gelirleri", "faiz, ucret, prim, komisyon ve diger gelirler", "faaliyet gelirleri", "faiz gelirleri"])
    brut_c, brut_o = satir_bul(satirlar, ["ticari faaliyetlerden brut kar", "brut kar", "faiz, ucret, prim, komisyon kar", "net faiz geliri"])
    efk_c, efk_o = satir_bul(satirlar, ["esas faaliyet kari", "faaliyet kari"])
    net_c, net_o = satir_bul(satirlar, ["donem net kar/zarari", "donem net kar", "ana ortaklik paylari", "surdurulen faaliyetler donem kari/zarari", "donem kari"])
    cfo_c, cfo_o = satir_bul(satirlar, ["isletme faaliyetlerinden nakit akislari", "isletme faaliyetlerinden", "faaliyetlerden nakit", "kaynaklanan net nakit"])

    # Eğer şirketin UFRS tablosunda satış ve CFO verisi tam ise (örn. OYYAT, ISMEN vb. aracı kurumlar),
    # standart UFRS tablo ve Piotroski motorunu kullan; yalnızca gerçek Banka/Sigorta (satis_c == 0 ve cfo_c == 0) için banka_mi uygula
    if satis_c > 0 and cfo_c != 0:
        banka_mi = False

    varlik_c, varlik_o = satir_bul(satirlar, ["toplam varliklar", "toplam aktifler"])
    ozk_c, ozk_o = satir_bul(satirlar, ["ana ortakliga ait ozkaynaklar", "toplam ozkaynaklar", "ozkaynaklar"])
    nakit_c, nakit_o = satir_bul(satirlar, ["nakit ve nakit benzerleri", "nakit degerler"])
    fin_borc_c, fin_borc_o = satir_bul(satirlar, ["finansal borclar"], mod="topla")
    alacak_c, alacak_o = satir_bul(satirlar, ["ticari alacaklar"], mod="ilk")
    iliskili_alacak_c, _ = satir_bul(satirlar, ["iliskili taraflardan diğer alacaklar", "iliskili taraflardan ticari olmayan alacaklar"], mod="topla")
    stok_c, stok_o = satir_bul(satirlar, ["stoklar"], mod="ilk")
    mdv_c, mdv_o = satir_bul(satirlar, ["maddi duran varliklar"], mod="ilk")
    ertelenmis_c, ertelenmis_o = satir_bul(satirlar, ["musteri soz. dogan yuk.", "musteri soz.dogan yuk.", "ertelenmis gelirler", "alinan siparis avanslari"], mod="topla")
    amort_c, amort_o = satir_bul(satirlar, ["amortisman giderleri", "amortisman & itfa paylari"], mod="ilk")
    donen_c, donen_o = satir_bul(satirlar, ["donen varliklar", "toplam donen varliklar"], mod="ilk")
    duran_c, duran_o = satir_bul(satirlar, ["duran varliklar", "toplam duran varliklar"], mod="ilk")
    kv_c, kv_o = satir_bul(satirlar, ["kisa vadeli yukumlulukler", "toplam kisa vadeli yukumlulukler"], mod="ilk")
    uv_c, uv_o = satir_bul(satirlar, ["uzun vadeli yukumlulukler", "toplam uzun vadeli yukumlulukler"], mod="ilk")

    # Muhasebe Denklemi Güvencesi: Toplam Varlıklar = max(Toplam Varlıklar, Dönen + Duran, Özkaynak + KV + UV)
    varlik_c = max(varlik_c, donen_c + duran_c, ozk_c + kv_c + uv_c)
    varlik_o = max(varlik_o, donen_o + duran_o, ozk_o + kv_o + uv_o)

    iliskili_oran = round((iliskili_alacak_c / max(varlik_c, 1.0)) * 100, 2)
    net_borc_c = max(fin_borc_c - nakit_c, 0.0)
    net_borc_o = max(fin_borc_o - nakit_o, 0.0)
    net_borc_ozkaynak = round(net_borc_c / max(ozk_c, 1.0), 2)
    kv_borc_oran = round((kv_c / max(kv_c + uv_c, 1.0)) * 100, 1)

    kirmizi_cizgi = (halka_aciklik > 70.0) or (iliskili_oran > 5.0) or (not banka_mi and (ozk_c <= 0 or (net_borc_ozkaynak > 2.0 and efk_c < 0) or (cfo_c < 0 and cfo_o < 0)))
    bolum1_gecti_mi = not kirmizi_cizgi

    efk_marj_c = (efk_c / max(satis_c, 1.0))
    favok_marj_c = ((efk_c + abs(amort_c)) / max(satis_c, 1.0)) if satis_c > 0 else 0.0
    yatirilan_sermaye = max(ozk_c + net_borc_c, 1.0)
    roic_tahmini = efk_c / yatirilan_sermaye

    # === EVRENSEL ÇOK DÖNEMLİ HENDEK MOTORU ===
    cok_donem = sirket_kaydi.get("donemler", {})
    gecmis_efk_marjlari = []
    for d_adi, d_satirlar in cok_donem.items():
        s_gelir = satir_bul([(r[0], r[1], r[1]) for r in d_satirlar], ["hasilat", "satis gelirleri", "faaliyet gelirleri", "faiz gelirleri"])[0]
        e_kar = satir_bul([(r[0], r[1], r[1]) for r in d_satirlar], ["esas faaliyet kari", "faaliyet kari"])[0]
        if s_gelir > 0:
            gecmis_efk_marjlari.append(e_kar / s_gelir)
            
    medyan_efk_marj = (sorted(gecmis_efk_marjlari)[len(gecmis_efk_marjlari)//2]) if gecmis_efk_marjlari else efk_marj_c

    # === KURUMSAL ALTYAPI & BİLANÇO BÜYÜKLÜĞÜ HENDEĞİ ===
    # Özkaynakları 10 Milyar TL üzerinde olan, güvenli borçlulukta (Net Borç/ÖZK <= 0.85) ve güçlü nakit üreten devler,
    # ağır yatırım ve döngüsel marj diplerinde dahi en az Kırılgan Hendekli (Döngüsel Hendek) kabul edilir.
    kurumsal_altyapi_hendegi = (ozk_c >= 10000.0 and net_borc_ozkaynak <= 0.85 and cfo_c > 0) or (favok_marj_c >= 0.10 and net_borc_ozkaynak <= 0.80 and cfo_c > 0)

    # Serkan Ünal 60 Hisse Portföy Tavanları: Korunaklı (%3.5), Kırılgan Hendekli (%2.2), Hendeksiz (%1.2)
    if (medyan_efk_marj >= 0.14 or (efk_marj_c >= 0.12 and roic_tahmini >= 0.15)) and cfo_c > 0:
        kategori, tavan = "Korunaklı", 3.5
    elif (medyan_efk_marj >= 0.07 or efk_marj_c >= 0.07 or kurumsal_altyapi_hendegi) and cfo_c > 0:
        kategori, tavan = "Kırılgan Hendekli", 2.2
    else:
        kategori, tavan = "Hendeksiz", 1.2

    if banka_mi:
        cfo_kaliteli = net_c > 0
        f_skor = 7 if (net_c > net_o and net_c > 0) else 5
    else:
        cari_c = round(donen_c / max(kv_c, 1.0), 2)
        cari_o = round(donen_o / max(kv_o, 1.0), 2)
        cfo_kaliteli = bool(cfo_c > net_c and cfo_c > 0)
        piotroski_puanlar = [
            int(net_c > 0),
            int(cfo_c > 0),
            int((net_c / max(varlik_c, 1.0)) > (net_o / max(varlik_o, 1.0))),
            int(cfo_c > net_c),
            int((uv_c / max(varlik_c, 1.0)) <= (uv_o / max(varlik_o, 1.0))),
            int(cari_c > cari_o),
            1,
            int((brut_c / max(satis_c, 1.0)) > (brut_o / max(satis_o, 1.0))),
            int((satis_c / max(varlik_c, 1.0)) > (satis_o / max(varlik_o, 1.0)))
        ]
        f_skor = sum(piotroski_puanlar)

    roe_hesap = round((net_c / max(ozk_c, 1.0)) * 100, 1) if ozk_c > 0 else float(sirket_kaydi.get("roe_tv", 0.0))

    # ZİRAAT RESMİ VERİLERİNİ DEVREYE AL
    # (UFRS dönemlik net_c ve net_o oran bütünlüğü için korunur; Ziraat 4Ç TTM kârı ayrı saklanır)
    net_kar_ttm = net_c
    ziraat = BIST_ANALIZ_TABANI.get(kod, {})
    if ziraat:
        if ziraat.get("fiyat", 0) > 0: fiyat = float(ziraat["fiyat"])
        if ziraat.get("fk", 0) > 0: fk = float(ziraat["fk"])
        if ziraat.get("fd_favok", 0) > 0: fd_favok = float(ziraat["fd_favok"])
        if ziraat.get("roe", 0) != 0: roe_hesap = float(ziraat["roe"])
        if ziraat.get("net_kar_son4c_mn", 0) != 0: net_kar_ttm = float(ziraat["net_kar_son4c_mn"])

    # Gerçek Piyasa Değeri Türetimi (TV verisi yoksa veya 0 ise Ziraat PD/DD veya F/K üzerinden gerçek PD)
    pddd_z = float(ziraat.get("pddd", 0.0) or 0.0) if ziraat else 0.0
    if pd_milyar <= 0.0:
        if pddd_z > 0 and ozk_c > 0:
            pd_milyar = round((ozk_c * pddd_z) / 1000.0, 2)
        elif fk > 0 and net_kar_ttm > 0:
            pd_milyar = round((net_kar_ttm * fk) / 1000.0, 2)

    # KUSURSUZ SEKTÖR ATAMASI (ALT PAZAR / YAKIN İZLEME PAZARI olan hisselerin unvandan gerçek Ziraat sektörünü bul)
    pazar_etiketleri = {"ALT PAZAR", "YAKIN İZLEME PAZARI", "ANA PAZAR", "YILDIZ PAZAR", "DİĞER", "BIST", "Sanayi", ""}
    sozluk_verisi = BIST_SOZLUK.get(kod, {})
    sozluk_sektor = (sozluk_verisi.get("sektor") or "").strip() if sozluk_verisi else ""
    yerel_sektor = (sirket_kaydi.get("sektor") or "").strip() if sirket_kaydi else ""
    tam_isim = tr_norm((sozluk_verisi.get("isim") if sozluk_verisi else "") or sirket_kaydi.get("sirket_adi", ""))

    if sozluk_sektor and sozluk_sektor not in pazar_etiketleri:
        sektor = sozluk_sektor
    elif yerel_sektor and yerel_sektor not in pazar_etiketleri:
        sektor = yerel_sektor
    else:
        if any(k in tam_isim for k in ["elektrik", "muhendislik", "kablo", "pano"]):
            sektor = "ELEKTRİK MALZEMELERİ"
        elif any(k in tam_isim for k in ["enerji", "elektrik uretim", "yenilenebilir"]):
            sektor = "ENERJİ"
        elif any(k in tam_isim for k in ["gida", "un ", "sut", "tarim", "yem"]):
            sektor = "GIDA"
        elif any(k in tam_isim for k in ["tekstil", "giyim", "dokuma", "iplik"]):
            sektor = "TEKSTİL & TEKSTİL YAN SANAYİ"
        elif any(k in tam_isim for k in ["gayrimenkul", "gyo"]):
            sektor = "GAYRİMENKUL YATIRIM ORTAKLIKLARI"
        elif any(k in tam_isim for k in ["kimya", "boya", "plastik", "polimer"]):
            sektor = "KİMYASAL MADDELER"
        elif any(k in tam_isim for k in ["makina", "makine", "celik", "dokum", "metal"]):
            sektor = "MAKİNE"
        elif any(k in tam_isim for k in ["yazilim", "bilgisayar", "teknoloji", "bilisim"]):
            sektor = "BİLGİSAYAR & YAZILIM"
        elif any(k in tam_isim for k in ["kagit", "ambalaj", "basim", "matbaa", "orman"]):
            sektor = "ORMAN, KAĞIT & KAĞIT ÜRÜNLERİ"
        elif any(k in tam_isim for k in ["inşaat", "insaat", "yapi", "cimento", "beton"]):
            sektor = "YAPI MALZEMELERİ"
        elif any(k in tam_isim for k in ["turizm", "otel", "konaklama"]):
            sektor = "TURİZM, OTEL, EĞLENCE"
        elif any(k in tam_isim for k in ["holding", "yatirim"]):
            sektor = "FİNANS-DIŞI HOLDİNG"
        else:
            sektor = "DİĞER İMALAT"

    # BÖLÜM 3 (DİPNOT RÖNTGENİ) & BÖLÜM 4 (ZİRAAT SEKTÖR F/K DESTEKLİ RASYONEL DEĞERLEME)
    cari_s_dipnot = [(r[0], r[1], r[1]) for r in satirlar]
    onceki_s_dipnot = [(r[0], r[2], r[2]) for r in satirlar]
    son_d_etiket = sirket_kaydi.get("son_donem") or "2026/06"
    onceki_d_etiket = sirket_kaydi.get("onceki_donem") or "2025/06"
    dipnot_ozet = hesapla_donem_dipnot_metrikleri(cari_s_dipnot, onceki_s_dipnot, donem_adi=son_d_etiket, onceki_donem_adi=onceki_d_etiket)
    dts_skor = int(dipnot_ozet.get("dts_skor", 0))
    icr_kati = float(dipnot_ozet.get("icr_kati", 99.0))

    sektor_fk = SEKTOR_FK_HARITASI.get(sektor, 10.0)
    sirket_hbk = round(fiyat / fk, 2) if fk > 0 else 0.0
    sirket_dd = round(fiyat / pddd_z, 2) if pddd_z > 0 else (ozk_c / max((pd_milyar * 1000) / max(fiyat, 1.0), 1.0) if pd_milyar > 0 else 0.0)
    adil_deger_tl, iskonto_orani, degerleme_durumu = rasyonel_degerleme_hesapla(
        fiyat=fiyat, hbk=sirket_hbk, dd=sirket_dd, roe=roe_hesap, sektor_fk=sektor_fk
    )

    return {
        "kod": kod, "sirket_adi": sirket_kaydi.get("sirket_adi", kod), "sektor": sektor,
        "kategori": kategori, "tavan_agirlik": tavan, "halka_aciklik": halka_aciklik, "net_borc_ozkaynak": net_borc_ozkaynak,
        "bolum1_gecti_mi": bolum1_gecti_mi, "fiyat_tl": fiyat, "cfo_buyuk_net_kar": cfo_kaliteli, "piotroski_skor": f_skor,
        "iliskili_oran": iliskili_oran, "kv_borc_oran": kv_borc_oran, "cfo_milyon_tl": round(cfo_c, 1),
        "net_kar_milyon_tl": round(net_c, 1), "net_kar_ttm_milyon_tl": round(net_kar_ttm, 1),
        "net_kar_onceki_milyon_tl": round(net_o, 1), "satis_milyon_tl": round(satis_c, 1), "satis_onceki_milyon_tl": round(satis_o, 1),
        "efk_milyon_tl": round(efk_c, 1), "efk_onceki_milyon_tl": round(efk_o, 1), "ozk_c": round(ozk_c, 1), "ozk_o": round(ozk_o, 1),
        "varlik_c": round(varlik_c, 1), "varlik_o": round(varlik_o, 1), "mdv_c": round(mdv_c, 1), "mdv_o": round(mdv_o, 1),
        "ertelenmis_c": round(ertelenmis_c, 1), "ertelenmis_o": round(ertelenmis_o, 1), "stok_c": round(stok_c, 1), "stok_o": round(stok_o, 1),
        "alacak_c": round(alacak_c, 1), "alacak_o": round(alacak_o, 1), "net_borc_c": round(net_borc_c, 1), "net_borc_o": round(net_borc_o, 1),
        "fin_borc_c": round(fin_borc_c, 1), "nakit_c": round(nakit_c, 1),
        "piyasa_degeri_milyar": pd_milyar, "fk": fk, "fd_favok": fd_favok, "roe": roe_hesap,
        "dts_skor": dts_skor, "icr_kati": icr_kati,
        "adil_deger_tl": adil_deger_tl, "iskonto_orani": iskonto_orani, "degerleme_durumu": degerleme_durumu
    }


# ==============================================================================
# 3. SERKAN ÜNAL TEFTİŞ VE DNA KARNELERİ
# ==============================================================================
def format_milyar_milyon(deger_mn: float) -> str:
    if abs(deger_mn) >= 1000.0:
        return f"{deger_mn / 1000.0:,.2f} milyar TL"
    return f"{deger_mn:,.0f} milyon TL"


def uret_dna_ve_tercume_katmani(m: dict) -> dict:
    kod = m.get("kod", "")
    sektor = m.get("sektor", "")
    kategori = m.get("kategori", "Hendeksiz")
    f_skor = int(m.get("f_skor", 6))
    efk_marji = float(m.get("efk_marji", 0.0))
    marj_degisim_qoq = float(m.get("marj_degisim_qoq", 0.0))
    ciro_buyume_yoy = float(m.get("ciro_buyume_yoy", 0.0))
    ina_nk_orani = float(m.get("ina_net_kar_orani", 0.0))
    net_kar_milyon = float(m.get("net_kar_milyon_tl", 0.0))
    ozkaynak_orani = float(m.get("ozkaynak_orani", 50.0))
    kv_borc_orani = float(m.get("kv_borc_orani", 50.0))
    net_borc_ozk = float(m.get("net_borc_ozk", 0.0))
    icr = float(m.get("icr", 3.0))
    dts_skor = int(m.get("dts_skor", 0))
    dso_gun = float(m.get("dso_gun", 0.0))
    alacak_makasi = float(m.get("alacak_ciro_makasi", 0.0))
    ozk_buyume = float(m.get("ozk_buyume", 0.0))
    mdv_buyume = float(m.get("mdv_buyume", 0.0))
    avans_buyume = float(m.get("avans_buyume", 0.0))
    roe = float(m.get("roe", 0.0))
    fk = float(m.get("fk", 0.0))
    fd_favok = float(m.get("fd_favok", 0.0))
    pd_milyar = float(m.get("pd_milyar", 0.0))
    halka_aciklik = float(m.get("halka_aciklik", 35.0))
    tarihsel_bant_yuzde = float(m.get("tarihsel_bant_konumu", 50.0))
    pfk = float(m.get("pfk", 0.0))

    banka_mi = bool(m.get("banka_mi", False))
    min_roe_norm, _, _ = sektor_roe_normu_getir(sektor)



    if banka_mi:
        kar_cumlesi = (
            f"Finans/Sigorta bilançosu: Özkaynaklar son dönemde %{ozk_buyume:+.1f} büyüdü, "
            f"özkaynak kârlılığı (ROE) %{roe:.1f} seviyesinde."
        )
        nakit_cumlesi = (
            f"Son dönemde {format_milyar_milyon(net_kar_milyon)} net dönem kârı üretildi; "
            f"Piotroski F-Skoru {f_skor}/9 ile bilanço ivmesi korunuyor."
        )
        borc_cumlesi = (
            f"Finansal kuruluş yapısı (Halka açıklık %{halka_aciklik:.1f}); "
            f"ticari borç rasyosu yerine sermaye yeterliliği ve ROE izlenir."
        )
    else:
        if efk_marji > 0:
            kar_cumlesi = (
                f"Her 100 TL'lik satıştan kasaya {efk_marji:.1f} TL esas faaliyet kârı kalıyor "
                f"(Önceki döneme göre marj {marj_degisim_qoq:+.1f} puan değişti)."
            )
        else:
            kar_cumlesi = (
                f"Şirket ana faaliyetinden zarar yazıyor; her 100 TL satışta {abs(efk_marji):.1f} TL "
                f"operasyonel erime var (Marj değişimi: {marj_degisim_qoq:+.1f} puan)."
            )

        if ina_nk_orani >= 1.0:
            nakit_cumlesi = (
                f"Kağıt üzerindeki her 1 TL net kâra karşılık kasaya {ina_nk_orani:.2f} TL "
                f"gerçek işletme nakdi (CFO) giriyor (Tahakkuk kalitesi yüksek)."
            )
        elif 0 < ina_nk_orani < 1.0:
            nakit_cumlesi = (
                f"Yazılan her 1 TL net kârın sadece {ina_nk_orani:.2f} TL'si nakde dönüyor; "
                f"kârın bir kısmı ticari alacak ({dso_gun:.0f} gün vade) ve stokta bekliyor."
            )
        else:
            nakit_cumlesi = (
                "Bilançoda muhasebesel kâr görünse de işletme faaliyetleri kasadan nakit tüketiyor "
                "(İNA negatif veya nakit dönüşümü zayıf)."
            )

        if net_borc_ozk <= 0.05:
            borc_cumlesi = (
                f"Varlıkların %{ozkaynak_orani:.1f}'i ortakların öz kaynağı; şirket net nakit/sıfır borç "
                f"konumunda (Yükümlülüklerin %{kv_borc_orani:.0f}'i kısa vadeli)."
            )
        else:
            icr_metin = f"faiz giderlerini {icr:.1f} kez karşılıyor" if icr > 0 else "faiz giderlerini karşılamakta zorlanıyor"
            borc_cumlesi = (
                f"Varlıkların %{ozkaynak_orani:.1f}'i özkaynak, Net Borç/ÖZK {net_borc_ozk:.2f}x; "
                f"üretilen faaliyet kârı {icr_metin} (Borcun %{kv_borc_orani:.0f}'i kısa vadeli)."
            )

    if fk > 0:
        favok_ek = f", FD/FAVÖK {fd_favok:.1f}x" if fd_favok > 0 else ""
        degerleme_cumlesi = (
            f"Mevcut kâr temposuyla piyasa değerini {fk:.1f} yılda amorti ediyor{favok_ek}; "
            f"tarihsel değerleme bandının %{tarihsel_bant_yuzde:.0f} diliminde fiyatlanıyor."
        )
    else:
        favok_ek = f"FD/FAVÖK {fd_favok:.1f}x çarpanında; " if fd_favok > 0 else ""
        degerleme_cumlesi = (
            f"{favok_ek}Net kâr bazlı F/K hesaplanamıyor; "
            f"tarihsel değerleme bandının %{tarihsel_bant_yuzde:.0f} diliminde izleniyor."
        )

    oz_turkce_seridi = [
        {"ikon": "⚙️", "etiket": "Operasyonel Güç", "metin": kar_cumlesi},
        {"ikon": "💵", "etiket": "Para Var mı?", "metin": nakit_cumlesi},
        {"ikon": "🛡️", "etiket": "Bilanço Zırhı", "metin": borc_cumlesi},
        {"ikon": "⚖️", "etiket": "Fiyatlama", "metin": degerleme_cumlesi},
    ]

    if banka_mi:
        if 0 < fk <= 6.0 and roe >= min_roe_norm:
            en_guclu = {
                "baslik": "Güçlü ROE & Çarpan İskontosu",
                "detay": f"%{roe:.1f} özkaynak kârlılığı (Sektör normu >=%{min_roe_norm:.0f}) ve {fk:.1f}x F/K çarpanıyla iskonto bölgesinde."
            }
        elif 0 < fk <= 6.0:
            en_guclu = {
                "baslik": f"Çarpan İskontosu ({fk:.1f}x F/K) & {f_skor}/9 F-Skor",
                "detay": f"{fk:.1f}x F/K çarpanı ve %{ozk_buyume:+.1f} özkaynak büyümesiyle güvenlik marjı sunuyor."
            }
        else:
            en_guclu = {
                "baslik": f"Özkaynak Büyümesi & {f_skor}/9 F-Skor",
                "detay": f"Özkaynaklarını %{ozk_buyume:+.1f} büyütürken {f_skor}/9 Piotroski skoruyla sağlamlığını koruyor."
            }
    elif ina_nk_orani >= 1.5 and efk_marji >= 15.0:
        en_guclu = {
            "baslik": "Yüksek Nakit Üretim Makinesi",
            "detay": f"%{efk_marji:.1f} faaliyet marjı ve 1 TL kâra {ina_nk_orani:.2f} TL nakit dönüşümüyle kârı doğrudan kasaya indiriyor."
        }
    elif net_borc_ozk <= 0.10 and ozkaynak_orani >= 50.0:
        en_guclu = {
            "baslik": "Net Nakit & Çelik Bilanço Zırhı",
            "detay": f"%{ozkaynak_orani:.1f} özkaynak oranı ve {net_borc_ozk:.2f}x Net Borç/ÖZK ile yüksek faiz ortamına tam bağışıklı."
        }
    elif 0 < fk <= 7.0 and f_skor >= 7:
        en_guclu = {
            "baslik": f"Güvenlik Marjı ({fk:.1f}x F/K) & F-Skor ({f_skor}/9)",
            "detay": f"Tarihsel çarpan bandının %{tarihsel_bant_yuzde:.0f} diliminde iskontolu ve {f_skor}/9 finansal kalite puanına sahip."
        }
    elif ina_nk_orani >= 1.2:
        en_guclu = {
            "baslik": f"Güçlü Nakit Dönüşümü ({ina_nk_orani:.2f}x CFO/Net Kâr)",
            "detay": f"1 TL net kâra karşılık kasaya {ina_nk_orani:.2f} TL gerçek işletme nakdi indirerek operasyonlarını finanse ediyor."
        }
    else:
        baslik_hendek = f"{kategori} Yapı & Nakit Akışı ({f_skor}/9)" if kategori != "Hendeksiz" else f"Pozitif İşletme Nakit Üretimi ({f_skor}/9)"
        en_guclu = {
            "baslik": baslik_hendek,
            "detay": f"1 TL net kâra karşılık {ina_nk_orani:.2f} TL işletme nakdi üreterek faaliyet döngüsünü finanse ediyor."
        }

    if marj_degisim_qoq >= 1.5 and ciro_buyume_yoy >= 10.0:
        ivme = {
            "baslik": "Çift Motorlu İvme (Ciro + Marj Açılması)",
            "detay": f"Satışlar %{ciro_buyume_yoy:+.1f} büyürken esas faaliyet marjı geçen döneme göre {marj_degisim_qoq:+.1f} puan genişledi."
        }
    elif ciro_buyume_yoy >= 20.0:
        ivme = {
            "baslik": "Güçlü Satış & Pazar Payı İvmesi",
            "detay": f"Üst satır satış gelirleri %{ciro_buyume_yoy:+.1f} artışla sektörel talep daralmasını aşıyor."
        }
    elif marj_degisim_qoq >= 0.5:
        ivme = {
            "baslik": "Maliyet Kontrolü & Marj İyileşmesi",
            "detay": f"Maliyet baskısına rağmen esas faaliyet kâr marjı önceki döneme göre {marj_degisim_qoq:+.1f} puan yükseldi."
        }
    elif avans_buyume >= 40.0 or mdv_buyume >= 35.0:
        ivme = {
            "baslik": "Sipariş Avansları & Kapasite Yatırımı",
            "detay": f"Maddi duran varlıklar %{mdv_buyume:+.1f}, alınan sipariş avansları %{avans_buyume:+.1f} büyüyerek gelecek ciroyu destekliyor."
        }
    else:
        ivme = {
            "baslik": "Nakit Kalkanı & Bilanço Dengelenmesi",
            "detay": f"Özkaynaklar %{ozk_buyume:+.1f} büyüdü; işletme sermayesi disipliniyle marj dengelenmesi izleniyor."
        }

    if 0 < fk <= 12.0 and pfk > fk * 1.5:
        kritik_risk = {
            "kod": "DEGER_TUZAGI",
            "baslik": "Döngü Zirvesi & Değer Tuzağı Riski",
            "detay": f"Cari F/K {fk:.1f}x ucuz görünse de, marj/ciro daralmasıyla P-F/K beklentisi {pfk:.1f}x seviyesine fırlıyor.",
            "iptal_tetikleyicisi": (
                "ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: İleriye dönük P-F/K beklentisi Cari F/K'yı %50'den fazla aşmaya "
                "devam ederse döngü zirvesi teyit edilir, değer tuzağına düşmemek için çıkılır."
            )
        }
    elif not banka_mi and net_borc_ozk > 0.25 and ((kv_borc_orani >= 75.0 and net_borc_ozk >= 0.50) or (0 < icr < 1.5) or net_borc_ozk >= 1.20):
        esik_icr = round(min(1.25, max(0.50, icr * 0.80)), 2) if icr > 0 else 1.00
        icr_kosul_metni = (
            f"Faiz karşılama oranı (ICR: {icr:.2f}x) gelecek çeyrekte 1.25x üzerine toparlanamaz ve {esik_icr:.2f}x altına sarkar"
            if icr < 1.25 else f"Faiz karşılama oranı (ICR) 1.25x'in altına sarkar"
        )
        kritik_risk = {
            "kod": "KISA_VADELI_BORC_VE_FAIZ_BASKISI",
            "baslik": "Kısa Vadeli Borç Çevirme & Finansman Yükü",
            "detay": f"Borcun %{kv_borc_orani:.0f}'i kısa vadeli; Net Borç/ÖZK {net_borc_ozk:.2f}x ve faiz karşılama (ICR) {icr:.2f}x düzeyinde.",
            "iptal_tetikleyicisi": (
                f"ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: {icr_kosul_metni} veya "
                f"Net Borç/ÖZK rasyosu {max(1.50, net_borc_ozk + 0.35):.2f}x eşiğini aşarsa temel tez iptal olur ve pozisyondan çıkılır."
            )
        }
    elif not banka_mi and (alacak_makasi >= 25.0 or dso_gun >= 180.0 or dts_skor >= 35):
        kritik_risk = {
            "kod": "ALACAK_VADE_VE_TAHAKKUK_RISKI",
            "baslik": "Ticari Alacak Vadesi (DSO) & Tahakkuk Baskısı",
            "detay": f"Alacak tahsil süresi {dso_gun:.0f} gün (DTS: {dts_skor}/100); alacak artışı ciro hızını {alacak_makasi:+.1f} puan aşıyor.",
            "iptal_tetikleyicisi": (
                f"ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: Gelecek bilançoda DSO ({dso_gun:.0f} gün) düşüşe geçmez ve "
                f"İNA/Net Kâr oranı 1.00x'in altına inerse alacak kalitesi bozulmuş sayılır, pozisyon kapatılır."
            )
        }
    elif marj_degisim_qoq <= -2.0 or efk_marji < 7.0:
        kritik_risk = {
            "kod": "OPERASYONEL_MARJ_EROZYONU",
            "baslik": "Döngüsel Marj Daralması & Emtia / Maliyet Baskısı",
            "detay": f"Esas faaliyet marjı %{efk_marji:.1f} seviyesinde (Önceki döneme göre marj değişimi: {marj_degisim_qoq:+.1f} puan).",
            "iptal_tetikleyicisi": f"ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: Esas faaliyet kâr marjı gelecek çeyrekte de toparlanamaz ve %{max(2.0, efk_marji - 1.5):.1f} eşiğinin altında kalmaya devam ederse pozisyon azaltılır."
        }
    elif tarihsel_bant_yuzde >= 80.0 or fk >= 18.0:
        kritik_risk = {
            "kod": "CARPAN_DOYGUNLUGU",
            "baslik": "Tarihsel Çarpan Doygunluğu (Yüksek Fiyatlama)",
            "detay": f"F/K {fk:.1f}x ile tarihsel değerleme bandının %{tarihsel_bant_yuzde:.0f} diliminde; güvenlik marjı daralmış durumda.",
            "iptal_tetikleyicisi": (
                "ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: Yüksek çarpanı destekleyen satış/FAVÖK büyümesi gelecek çeyrekte "
                "yavaşlar veya stok devir hızı düşerse pahalı çarpan taşınmaz, kâr realizasyonu yapılır."
            )
        }
    else:
        kritik_risk = {
            "kod": "ISLETME_SERMAYESI_VE_KV_BORC_TAKIBI",
            "baslik": "Kısa Vadeli Borç Payı & Stok Devir Disiplini",
            "detay": f"Bilançoda kırmızı bayrak yok; ancak borcun %{kv_borc_orani:.0f}'lik kısa vadeli kısmı ve marj korunumu izleniyor.",
            "iptal_tetikleyicisi": (
                "ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: Kısa vadeli borç çevirme maliyetleri operasyonel kâr artışını geçer "
                "veya stok/alacak devir hızı üst üste 2 çeyrek bozulursa pozisyon ağırlığı azaltılır."
            )
        }

    return {
        "dna_ozeti": {
            "en_guclu_yani": en_guclu,
            "iyiye_giden_taraf": ivme,
            "kritik_risk": kritik_risk
        },
        "oz_turkce_seridi": oz_turkce_seridi
    }


def uret_serkan_unal_karnesi(s: dict, pozisyon: dict = None) -> dict:
    kod = s["kod"]
    sirket_adi = s.get("sirket_adi", kod)
    sektor = s.get("sektor", "Sanayi")
    # Sektör "DİĞER" ise yerel kayıttan gerçek sektörü al
    
    kategori = s.get("kategori", "Kırılgan Hendekli")
    halka_aciklik = float(s.get("halka_aciklik", 35.0))
    net_borc_ozk = float(s.get("net_borc_ozkaynak", 0.0))
    cfo_ok = bool(s.get("cfo_buyuk_net_kar", True))
    f_skor = int(s.get("piotroski_skor", 6))
    fiyat = float(s.get("fiyat_tl", 0.0))
    tavan = float(s.get("tavan_agirlik", 5.0))
    iliskili_oran = float(s.get("iliskili_oran", 0.2))
    kv_borc_oran = float(s.get("kv_borc_oran", 45.0))

    satis_c = float(s.get("satis_milyon_tl", 1000.0))
    satis_o = float(s.get("satis_onceki_milyon_tl", 800.0))
    efk_c = float(s.get("efk_milyon_tl", 180.0))
    efk_o = float(s.get("efk_onceki_milyon_tl", 130.0))
    net_c = float(s.get("net_kar_milyon_tl", 150.0))
    net_o = float(s.get("net_kar_onceki_milyon_tl", 110.0))
    cfo_c = float(s.get("cfo_milyon_tl", 210.0))
    ozk_c = float(s.get("ozk_c", 4000.0))
    ozk_o = float(s.get("ozk_o", 3100.0))
    varlik_c = float(s.get("varlik_c", 7500.0))
    varlik_o = float(s.get("varlik_o", 6000.0))
    mdv_c = float(s.get("mdv_c", 2200.0))
    mdv_o = float(s.get("mdv_o", 1850.0))
    ertelenmis_c = float(s.get("ertelenmis_c", 320.0))
    ertelenmis_o = float(s.get("ertelenmis_o", 240.0))
    stok_c = float(s.get("stok_c", 950.0))
    stok_o = float(s.get("stok_o", 780.0))
    alacak_c = float(s.get("alacak_c", 1100.0))
    alacak_o = float(s.get("alacak_o", 890.0))
    fin_borc_c = float(s.get("fin_borc_c", 0.0))
    nakit_c = float(s.get("nakit_c", 0.0))

    pd_milyar = float(s.get("piyasa_degeri_milyar", 0.0))
    fk = float(s.get("fk", 0.0))
    fd_favok = float(s.get("fd_favok", 0.0))
    net_borc_c = float(s.get("net_borc_c", 0.0))
    net_kar_ttm = float(s.get("net_kar_ttm_milyon_tl", net_c))
    fd_satis = round((pd_milyar * 1000 + net_borc_c) / max(satis_c, 1.0), 2) if satis_c > 0 else 0.0
    roe = float(s.get("roe", round((net_c / max(ozk_c, 1.0)) * 100, 1)))

    satis_buyume = round(((satis_c - satis_o) / max(abs(satis_o), 1.0)) * 100, 1) if satis_o != 0 else 0.0
    if net_o != 0:
        net_kar_buyume = round(((net_c - net_o) / max(abs(net_o), 1.0)) * 100, 1)
    else:
        net_kar_buyume = 100.0 if net_c > 0 else 0.0
    efk_marj_c = round((efk_c / max(satis_c, 1.0)) * 100, 1) if satis_c > 0 else 0.0
    efk_marj_o = round((efk_o / max(satis_o, 1.0)) * 100, 1) if satis_o > 0 else 0.0
    marj_fark_puan = round(efk_marj_c - efk_marj_o, 1)
    nakit_donusum_kati = round(cfo_c / net_c, 2) if net_c > 0 else (1.5 if cfo_c > 0 else 0.0)

    # === DİNAMİK KARAKTER ETİKETLERİ (TAGS) ===
    karakter_etiketleri = []
    if net_borc_ozk <= 0.05:
        karakter_etiketleri.append('<span style="background:#064e3b; color:#34d399; padding:3px 6px; border-radius:4px; font-size:10.5px; margin-right:6px; font-weight:bold; box-shadow:0 0 4px #064e3b;">[Net Nakit]</span>')
    elif net_borc_ozk > 1.0:
        karakter_etiketleri.append('<span style="background:#7f1d1d; color:#fca5a5; padding:3px 6px; border-radius:4px; font-size:10.5px; margin-right:6px; font-weight:bold;">[Yüksek Borç]</span>')
        
    if nakit_donusum_kati >= 1.2:
        karakter_etiketleri.append('<span style="background:#1e3a8a; color:#93c5fd; padding:3px 6px; border-radius:4px; font-size:10.5px; margin-right:6px; font-weight:bold; box-shadow:0 0 4px #1e3a8a;">[Nakit Makinesi]</span>')
    elif nakit_donusum_kati < 0.5:
        karakter_etiketleri.append('<span style="background:#78350f; color:#fcd34d; padding:3px 6px; border-radius:4px; font-size:10.5px; margin-right:6px; font-weight:bold;">[Zayıf Tahakkuk]</span>')
        
    if marj_fark_puan >= 2.0:
        karakter_etiketleri.append('<span style="background:#065f46; color:#6ee7b7; padding:3px 6px; border-radius:4px; font-size:10.5px; margin-right:6px; font-weight:bold;">[Marj Genişliyor]</span>')
    elif marj_fark_puan <= -2.0:
        karakter_etiketleri.append('<span style="background:#450a0a; color:#f87171; padding:3px 6px; border-radius:4px; font-size:10.5px; margin-right:6px; font-weight:bold;">[Marj Baskılı]</span>')

    if roe >= 35.0:
        karakter_etiketleri.append('<span style="background:#4c1d95; color:#c4b5fd; padding:3px 6px; border-radius:4px; font-size:10.5px; margin-right:6px; font-weight:bold; box-shadow:0 0 4px #4c1d95;">[Yüksek ROE]</span>')

    etiketler_html = "".join(karakter_etiketleri) if karakter_etiketleri else '<span style="color:#64748b; font-size:10.5px;">[Dengeli Profil]</span>'

    # === BÜYÜME İVMESİ ROZETLERİ ===
    nk_renk = "#34d399" if net_kar_buyume >= 0 else "#f87171"
    nk_ok = "▲" if net_kar_buyume >= 0 else "▼"
    ciro_renk = "#34d399" if satis_buyume >= 0 else "#f87171"
    ciro_ok = "▲" if satis_buyume >= 0 else "▼"
    
    buyume_rozetleri_html = f'''
    <div style="display:flex; gap:10px; margin-top:10px; margin-bottom:12px;">
        <div style="background:#111827; border:1px solid #334155; padding:6px 12px; border-radius:6px; font-size:12px;">
            <span style="color:#94a3b8;">Yıllık Net Kâr:</span> <strong style="color:{nk_renk};">{nk_ok} %{abs(net_kar_buyume):.1f}</strong>
        </div>
        <div style="background:#111827; border:1px solid #334155; padding:6px 12px; border-radius:6px; font-size:12px;">
            <span style="color:#94a3b8;">Yıllık Ciro:</span> <strong style="color:{ciro_renk};">{ciro_ok} %{abs(satis_buyume):.1f}</strong>
        </div>
    </div>
    '''

    # SENTETİK P-F/K MOTORU (Kâr İvmesi + Ciro İvmesi + Marj Genişlemesi + Maddi Sipariş Avansı)
    avans_buyume = round(((ertelenmis_c / max(abs(ertelenmis_o), 1.0)) - 1) * 100, 1) if ertelenmis_o > 0 else 0.0
    avans_maddi_mi = max(ertelenmis_c, ertelenmis_o) >= (satis_c * 0.05) and satis_c > 0
    avans_ivmesi = max(-0.20, min(0.35, (avans_buyume / 100.0) * 0.30)) if avans_maddi_mi else 0.0

    satis_ivmesi = max(-0.40, min(0.80, satis_buyume / 100.0))
    kar_ivmesi = max(-0.40, min(0.80, net_kar_buyume / 100.0))
    marj_ivmesi = max(-0.30, min(0.30, marj_fark_puan / 100.0))

    # Eğer net kâr ve faaliyet marjı birlikte büyüyorsa, brüt işlem hacmi (ciro) daralmasının etkisini sınırla
    if net_kar_buyume > 0 and marj_fark_puan > 0 and satis_ivmesi < 0:
        toplam_ivme = max(-0.20, min(1.20, (0.55 * kar_ivmesi) + (0.20 * satis_ivmesi) + marj_ivmesi + avans_ivmesi))
    else:
        toplam_ivme = max(-0.60, min(1.20, (0.45 * kar_ivmesi) + (0.35 * satis_ivmesi) + marj_ivmesi + avans_ivmesi))

    if fk > 0 and (1.0 + toplam_ivme) > 0.20:
        pfk = round(fk / (1.0 + toplam_ivme), 1)
    else:
        pfk = 0.0

    # 12 ÇEYREKLİK (3 YILLIK) ROE TRENDİ VE SEKTÖREL VERİLER
    sirket_kaydi = YEREL_MALI_VERITABANI.get(kod, {})
    cok_donem = sirket_kaydi.get("donemler", {})
    # Son 12 çeyreği (3 yıl) çek
    sirali_donemler = sorted(cok_donem.keys())[-12:]

    roe_donemler = []
    roe_degerleri = []
    for d_adi in sirali_donemler:
        d_satirlar = [(row[0], row[1], row[1]) for row in cok_donem[d_adi]]
        d_net = satir_bul(d_satirlar, ["donem net kar", "donem kari", "ana ortaklik paylari"])[0]
        d_ozk = max(satir_bul(d_satirlar, ["ana ortakliga ait ozkaynaklar", "toplam ozkaynaklar", "ozkaynaklar"])[0], 1.0)
        d_roe = round((d_net / d_ozk) * 100.0, 1)
        roe_donemler.append(d_adi)
        roe_degerleri.append(d_roe)

    if not roe_degerleri:
        roe_donemler = ["Son Dönem"]
        roe_degerleri = [roe]
    # Son çeyrek ROE'sini Ziraat resmi değeriyle güncelle
    if roe_degerleri:
        roe_degerleri[-1] = roe
    roe_serisi = {
        "donemler": roe_donemler,
        "roe_degerleri": roe_degerleri
    }

    # HİBRİT DEĞERLEME MODÜLÜ (ZİRAAT GÜVENCELİ - UFRS net_c korunur, 4Ç TTM kârı net_kar_ttm'de tutulur)
    ziraat = BIST_ANALIZ_TABANI.get(kod, {})
    if ziraat:
        if ziraat.get("fk", 0) > 0: fk = float(ziraat["fk"])
        if ziraat.get("fd_favok", 0) > 0: fd_favok = float(ziraat["fd_favok"])
        if ziraat.get("roe", 0) != 0: roe = float(ziraat["roe"])
        if ziraat.get("net_kar_son4c_mn", 0) != 0: net_kar_ttm = float(ziraat["net_kar_son4c_mn"])
        if ziraat.get("pd_s", 0) > 0: fd_satis = float(ziraat["pd_s"])

    sektor_medyan_fk = SEKTOR_FK_HARITASI.get(sektor, 10.0)
    alt_fk = round(max(3.0, sektor_medyan_fk * 0.65), 1)
    medyan_fk = round(sektor_medyan_fk, 1)
    ust_fk = round(sektor_medyan_fk * 1.35, 1)

    konum_yuzde = int(min(95, max(5, (fk / max(ust_fk * 1.25, 1.0)) * 100))) if fk > 0 else 5
    marj_durum = "GÜÇLÜ GÜVENLİK MARJI (İSKONTOLU)" if (0 < fk < alt_fk) else ("DENGELİ / MAKUL DEĞERLEME" if (0 < fk <= ust_fk) else "DOYUM NOKTASI / PAHALI")
    marj_renk = "badge-ok" if (0 < fk < alt_fk) else ("badge-warn" if (0 < fk <= ust_fk) else "badge-fail")

    degerleme_bandi = {
        "fk": fk, "pfk": pfk, "alt_fk": alt_fk, "medyan_fk": medyan_fk, "ust_fk": ust_fk,
        "marj_durum": marj_durum, "marj_renk": marj_renk, "konum_yuzde": konum_yuzde
    }

    # Hatasız Hisse Başına Kâr (HBK) ve Defter Değeri (DD) Türetimi
    sirket_hbk = round(fiyat / fk, 2) if fk > 0 else 0.0
    pddd = ziraat.get("pddd", 0.0) if ziraat else 0.0
    sirket_dd = round(fiyat / pddd, 2) if pddd > 0 else (ozk_c / max((pd_milyar * 1000) / max(fiyat, 1.0), 1.0) if pd_milyar > 0 else 0.0)

    adil_deger_tl, iskonto_yuzde, degerleme_durumu = rasyonel_degerleme_hesapla(
        fiyat=fiyat, 
        hbk=sirket_hbk, 
        dd=sirket_dd, 
        roe=roe,
        sektor_fk=sektor_medyan_fk
    )
    
    termometre_html_kodu = degerleme_termometresi_uret(
        hisse_kodu=kod, 
        mevcut_fiyat=fiyat, 
        adil_deger=adil_deger_tl, 
        iskonto_orani=iskonto_yuzde, 
        durum=degerleme_durumu
    )

    birikimli_dipnot_arsivi = guncelle_birikimli_dipnot_arsivi(kod)
    mevcut_agirlik = round(float(pozisyon.get("agirlik_yuzde", 0.0)), 2) if pozisyon else 0.0
    kademe_durumu = pozisyon.get("kademe", "1/3 Başlangıç (Radar)") if pozisyon else "1/3 Başlangıç (Yeni Aday)"

    kar_degisim_etiket = f"Zarardan Kâra ({format_milyar_milyon(net_o)} → {format_milyar_milyon(net_c)})" if (net_o < 0 and net_c > 0) else f"%{net_kar_buyume:+.1f} ({format_milyar_milyon(net_o)} → {format_milyar_milyon(net_c)})"
    sirketi_tani = {
        "sirket_adi": sirket_adi,
        "ne_is_yapar": f"{sirket_adi}, {sektor} sektöründe faaliyet gösteren, Bölüm 1 süzgeçlerini geçen BIST şirketidir.",
        "urun_ve_pazar": f"{sektor} ana ürün ve hizmetleri | Yurt içi ve ihracat pazarları",
        "piyasa_degeri": format_milyar_milyon(pd_milyar * 1000.0) if pd_milyar > 0 else format_milyar_milyon(ozk_c),
        "net_kar_12ay": format_milyar_milyon(net_kar_ttm),
        "net_kar_yorum": f"Son 4 çeyrek (TTM) net kâr: {format_milyar_milyon(net_kar_ttm)} | UFRS dönem değişimi: {kar_degisim_etiket}.",
        "roe_deger": f"%{roe:.1f}",
        "roe_yorum": f"Özkaynak kârlılığı (ROE) %{roe:.1f} düzeyindedir.",
        "carpan_ozet": f"F/K: {fk if fk > 0 else 'N/A'}x | FD/FAVÖK: {fd_favok if fd_favok > 0 else 'N/A'}x | PD/Satış: {fd_satis}x"
    }

    gercek_banka_sigorta_mi = (satis_c <= 0.0 and cfo_c == 0.0) or any(x in tr_norm(sektor) for x in ["banka", "sigorta", "emeklilik"])
    ozk_buyume_tab2 = round(((ozk_c / max(abs(ozk_o), 1.0)) - 1.0) * 100.0, 1) if ozk_o != 0 else 0.0
    ozk_aktif_tab2 = round((ozk_c / max(varlik_c, 1.0)) * 100.0, 1)
    min_roe_tab2, _, _ = sektor_roe_normu_getir(sektor)

    if gercek_banka_sigorta_mi and satis_c <= 0.0:
        dna_ve_gidisat = [
            {"baslik": "Özkaynaklar Büyüyor mu?", "deger": f"%{ozk_buyume_tab2:+.1f}", "durum": "GÜÇLÜ" if ozk_buyume_tab2 >= 15.0 else "DENGELİ", "yorum": f"Ana ortaklık özkaynakları {format_milyar_milyon(ozk_o)}'den {format_milyar_milyon(ozk_c)}'ye ulaştı."},
            {"baslik": "Kâr Artıyor mu?", "deger": f"%{net_kar_buyume:+.1f}", "durum": "ARTIŞ VAR" if net_kar_buyume > 0 else "DÜŞÜŞ / ZARAR", "yorum": f"Dönem net kârı {format_milyar_milyon(net_o)}'den {format_milyar_milyon(net_c)}'ye değişti."},
            {"baslik": "Sermaye Getirisi (ROE) Yeterli mi?", "deger": f"ROE %{roe:.1f}", "durum": "İYİLEŞİYOR" if roe >= min_roe_tab2 else "DARALMA", "yorum": f"Sektör asgari ROE normu %{min_roe_tab2:.0f} iken şirketin cari ROE'si %{roe:.1f} düzeyindedir."},
            {"baslik": "Sermaye Tamponu (Özkaynak / Aktif)", "deger": f"%{ozk_aktif_tab2:.1f}", "durum": "GÜVENLİ" if ozk_aktif_tab2 >= 10.0 else "İZLEMEDE", "yorum": f"Toplam {format_milyar_milyon(varlik_c)} aktif içinde özkaynak payı %{ozk_aktif_tab2:.1f} seviyesindedir."},
            {"baslik": "Bilanço İvmesi & Kâr Kalitesi", "deger": f"{f_skor}/9 F-Skor", "durum": "SAĞLAM" if cfo_ok else "İZLEMEDE", "yorum": f"Son 4 çeyrek (TTM) net kâr {format_milyar_milyon(net_kar_ttm)} ile finansal kârlılık korunuyor."}
        ]
    else:
        satis_durum = "GÜÇLÜ" if satis_buyume >= 20.0 else ("DARALMA" if satis_buyume < 0 else "DENGELİ")
        dna_ve_gidisat = [
            {"baslik": "Satışlar Hızlanıyor mu?", "deger": f"%{satis_buyume:+.1f}", "durum": satis_durum, "yorum": f"Yıllık satış gelirleri {format_milyar_milyon(satis_o)}'den {format_milyar_milyon(satis_c)}'ye geldi."},
            {"baslik": "Kâr Artıyor mu?", "deger": f"%{net_kar_buyume:+.1f}", "durum": "ARTIŞ VAR" if net_kar_buyume > 0 else "DÜŞÜŞ / ZARAR", "yorum": f"Net kâr {format_milyar_milyon(net_o)}'den {format_milyar_milyon(net_c)}'ye değişti."},
            {"baslik": "Marjlar Güçleniyor mu?", "deger": f"EFK %{efk_marj_c:.1f}", "durum": "İYİLEŞİYOR" if marj_fark_puan > 0 else "DARALMA", "yorum": f"Operasyonel kâr marjı önceki döneme göre {marj_fark_puan:+.1f} puan değişti."},
            {"baslik": "Borç Azalıyor mu? (Sağlamlık)", "deger": f"{net_borc_ozk:.2f}x", "durum": "GÜVENLİ" if net_borc_ozk <= 1.0 else "RİSKLİ", "yorum": f"Net Borç / Özkaynak oranı {net_borc_ozk:.2f}x. Kısa vadeli borç payı %{kv_borc_oran:.0f}."},
            {"baslik": "Parası Var mı? (Nakit Üretimi)", "deger": f"{nakit_donusum_kati:.2f} TL Nakit", "durum": "SAĞLAM" if cfo_ok else "İZLEMEDE", "yorum": f"Her 1 TL net kâra karşılık {nakit_donusum_kati:.2f} TL işletme nakdi üretildi."}
        ]

    def degisim_karti(kalem_adi, c_val, o_val, varlik_onceki, ozel_not):
        fark = c_val - o_val
        yuzde = round(((c_val / max(abs(o_val), 1.0)) - 1) * 100, 1) if o_val != 0 else 0.0
        aktif_etkisi = round((abs(fark) / max(varlik_onceki, 1.0)) * 100, 1)
        yon = "YÜKSELDİ" if fark >= 0 else "AZALDI"
        
        return {
            "kalem": f"{kalem_adi} %{abs(yuzde):.1f} {yon}",
            "guncel_tutar": format_milyar_milyon(c_val),
            "ne_ifade_ediyor": f"{format_milyar_milyon(o_val)} → {format_milyar_milyon(c_val)}. Değişim aktifin %{aktif_etkisi:.1f}'i. {ozel_not}"
        }

    ozk_not = "Özkaynak tamponunun net kârla büyümesi içsel değeri artırır." if ozk_c >= ozk_o else "Özkaynak tabanındaki daralma veya temettü çıkışı izlenmelidir."
    mdv_not = "Maddi duran varlık artışı kapasite ve tesis yatırımlarının sürdüğünü gösterir." if mdv_c >= mdv_o else "Sabit kıymetlerde reel artış yok; mevcut kapasiteyle üretim yapılıyor."
    avans_not = "Alınan peşin sipariş avanslarındaki artış gelecek çeyreklerin hazır hasılatıdır." if ertelenmis_c >= ertelenmis_o else "Sipariş avanslarının azalması geçmiş siparişlerin teslim edilip faturaya dönüştüğünü gösterir."
    stok_deg = ((stok_c / max(stok_o, 1.0)) - 1.0) * 100.0 if stok_o > 0 else 0.0
    stok_not = "Stok artışı ciro büyümesinin üzerinde; stok devir hızı yakından izlenmelidir." if (stok_deg > satis_buyume + 15.0) else "Stoklar operasyonel satış hacmiyle dengeli seyrediyor."
    alacak_deg = ((alacak_c / max(alacak_o, 1.0)) - 1.0) * 100.0 if alacak_o > 0 else 0.0
    alacak_not = "Alacaklar satıştan hızlı büyüdü; tahsilat vadesinde (DSO) açılma var." if (alacak_deg > satis_buyume + 15.0) else "Ticari alacaklar satış temposuyla uyumlu ve tahsilat disiplini korunuyor."

    bilanco_degisimleri = [
        degisim_karti("ANA ORTAKLIK ÖZKAYNAĞI", ozk_c, ozk_o, varlik_o, ozk_not),
        degisim_karti("MADDİ DURAN VARLIKLAR", mdv_c, mdv_o, varlik_o, mdv_not),
        degisim_karti("SİPARİŞ AVANSLARI (ERTELENMİŞ GELİR)", ertelenmis_c, ertelenmis_o, varlik_o, avans_not),
        degisim_karti("STOKLAR", stok_c, stok_o, varlik_o, stok_not),
        degisim_karti("TİCARİ ALACAKLAR", alacak_c, alacak_o, varlik_o, alacak_not)
    ]

    donemler_dict = birikimli_dipnot_arsivi.get("donemler", {})
    son_donem_adi = sorted(donemler_dict.keys())[-1] if donemler_dict else None
    son_dipnot = donemler_dict.get(son_donem_adi, {}) if son_donem_adi else {}

    ozk_orani_hesap = round((ozk_c / max(varlik_c, 1.0)) * 100.0, 1)
    ozk_buyume_yuzde = round(((ozk_c / max(abs(ozk_o), 1.0)) - 1.0) * 100.0, 1) if ozk_o != 0 else 0.0
    mdv_buyume_yuzde = round(((mdv_c / max(abs(mdv_o), 1.0)) - 1.0) * 100.0, 1) if mdv_o != 0 else 0.0
    avans_buyume_yuzde = round(((ertelenmis_c / max(abs(ertelenmis_o), 1.0)) - 1.0) * 100.0, 1) if ertelenmis_o != 0 else 0.0
    alacak_buyume_yuzde = round(((alacak_c / max(abs(alacak_o), 1.0)) - 1.0) * 100.0, 1) if alacak_o != 0 else 0.0

    dna_katmani = uret_dna_ve_tercume_katmani({
        "kod": kod, "sektor": sektor, "kategori": kategori, "f_skor": f_skor,
        "banka_mi": (gercek_banka_sigorta_mi and satis_c <= 0.0),
        "efk_marji": efk_marj_c, "marj_degisim_qoq": marj_fark_puan,
        "ciro_buyume_yoy": satis_buyume, "ina_net_kar_orani": nakit_donusum_kati,
        "net_kar_milyon_tl": net_c, "ozkaynak_orani": ozk_orani_hesap,
        "kv_borc_orani": kv_borc_oran, "net_borc_ozk": net_borc_ozk,
        "icr": float(son_dipnot.get("icr_kati", 3.0)),
        "dts_skor": int(son_dipnot.get("dts_skor", 0)),
        "dso_gun": float(son_dipnot.get("dso_gun", 0.0)),
        "alacak_ciro_makasi": round(alacak_buyume_yuzde - satis_buyume, 1),
        "ozk_buyume": ozk_buyume_yuzde, "mdv_buyume": mdv_buyume_yuzde,
        "avans_buyume": avans_buyume_yuzde, "roe": roe, "fk": fk, "pfk": pfk,
        "fd_favok": fd_favok, "pd_milyar": pd_milyar if pd_milyar > 0 else round((ozk_c * 1.3) / 1000.0, 2),
        "halka_aciklik": halka_aciklik, "tarihsel_bant_konumu": konum_yuzde
    })

    # DİKKATİMİ ÇEKENLER & SEVMEDİKLERİM (Özet ve Sadeleştirilmiş)
    dikkat_cekenler = []
    if cfo_ok:
        dikkat_cekenler.append(f"Güçlü Nakit Akışı: Her 1 TL net kâra {nakit_donusum_kati:.2f} TL işletme nakdi üretildi.")
    if net_borc_ozk <= 0.25:
        dikkat_cekenler.append(f"Çelik Bilanço: Net Borç/ÖZK {net_borc_ozk:.2f}x ile borçsuz veya net nakitte.")
    if efk_marj_c > 14.0 and marj_fark_puan >= 0:
        dikkat_cekenler.append(f"Marj Gücü: Faaliyet marjı %{efk_marj_c:.1f} ({marj_fark_puan:+.1f} puan iyileşme).")
    if not dikkat_cekenler:
        dikkat_cekenler.append("Bilanço dengesi stabil; faaliyet rutininde devam ediyor.")

    sevmediklerim = []
    if net_borc_ozk > 1.2: 
        sevmediklerim.append(f"Yüksek Borç: Net Borç / Özkaynak {net_borc_ozk:.2f}x sınırını aşıyor.")
    if nakit_c > 0 and fin_borc_c > (nakit_c * 2):
        sevmediklerim.append(f"Nakit Baskısı: Borç ({format_milyar_milyon(fin_borc_c)}), nakdin {round(fin_borc_c/nakit_c, 1)} katı.")
    if net_kar_buyume < 0:
        sevmediklerim.append(f"Kâr Gerilemesi: Yıllık net kâr %{abs(net_kar_buyume):.1f} geriledi.")
    if marj_fark_puan < -1.5:
        sevmediklerim.append(f"Marj Erozyonu: Operasyonel marj {abs(marj_fark_puan):.1f} puan daraldı.")
    if f_skor < 7: 
        sevmediklerim.append(f"Piotroski {f_skor}/9 ile 7 eşiğinin altında kaldı.")
    if not sevmediklerim: 
        sevmediklerim.append("Temel tabloda kırmızı bayrak veya ciddi operasyonel zaaf tespit edilmedi.")

    iptal_kosulu_metni = dna_katmani["dna_ozeti"]["kritik_risk"]["iptal_tetikleyicisi"]
    borsaci_gozuyle_ozet = (
        f"{sirket_adi} ({kod}), {sektor} sektöründe '{kategori}' hendek yapısıyla faaliyet gösteriyor. "
        f"Son dönemde {format_milyar_milyon(satis_c)} ciro ve {format_milyar_milyon(net_c)} net kâr üretildi. "
        f"Kârın nakde dönüşüm oranı {nakit_donusum_kati:.2f}x ve Net Borç/ÖZK rasyosu {net_borc_ozk:.2f}x seviyesindedir. "
        f"Şirketin en güçlü kalkanı '{dna_katmani['dna_ozeti']['en_guclu_yani']['baslik']}' iken, "
        f"yakından izlenen kritik zaafı '{dna_katmani['dna_ozeti']['kritik_risk']['baslik']}' tarafıdır."
    )

    # KAP_VE_TEKNIK_AMBARI'ndan Gerçek Verileri Bağla
    # YENİ HALİ:
    kap_verileri = TUM_KAP_HABERLERI.get(kod) or KAP_VE_TEKNIK_AMBARI.get("kap_haberleri", {}).get(kod, [])
    teknik_veri = KAP_VE_TEKNIK_AMBARI.get("teknik_veriler", {}).get(kod, None)

    return {
        "etiketler_html": etiketler_html,
        "buyume_rozetleri_html": buyume_rozetleri_html,
        "kod": kod, "sirket_adi": sirket_adi, "sektor": sektor, "kategori": kategori, "fiyat_tl": fiyat,
        "f_skor": f_skor, "tavan_agirlik": tavan, "mevcut_agirlik": mevcut_agirlik, "kademe_durumu": kademe_durumu,
        "degerleme_bandi": degerleme_bandi, "termometre_html": termometre_html_kodu, "birikimli_dipnot": birikimli_dipnot_arsivi,
        "sirketi_tani": sirketi_tani, "dna_ve_gidisat": dna_ve_gidisat, "bilanco_degisimleri": bilanco_degisimleri,
        "kap_haberleri": kap_verileri,
        "teknik_veri": teknik_veri,
        "dna_ozeti": dna_katmani["dna_ozeti"],
        "oz_turkce_seridi": dna_katmani["oz_turkce_seridi"],
        "roe_serisi": roe_serisi,
        "son_soz": {
            "dikkat_cekenler": dikkat_cekenler,
            "sevmediklerim": sevmediklerim,
            "borsaci_gozuyle": borsaci_gozuyle_ozet,
            "iptal_kosulu": iptal_kosulu_metni
        },
        "bolum1_elemeler": [
            {"kriter": "Halka Açıklık", "deger": f"%{halka_aciklik:.1f}", "durum": "GEÇTİ" if halka_aciklik <= 70.0 else "ELENDİ", "not": "Tavan %70 kontrolü"},
            {"kriter": "İlişkili Taraflara Kaynak Çıkışı", "deger": f"%{iliskili_oran:.2f}", "durum": "TEMİZ" if iliskili_oran < 5.0 else "UYARI", "not": "Kaynak sızıntısı yok"},
            {"kriter": "Borçluluk ve Faiz Yapısı", "deger": f"{net_borc_ozk:.2f}x", "durum": "GÜVENLİ" if net_borc_ozk <= 1.5 else "DENGELİ", "not": "Özkaynak / borç uyumu"}
        ],
        "bolum5_karar_ve_cikis": {
            "hendek_sinifi": kategori,
            "portfoy_tavani": f"Maksimum %{tavan:.1f}",
            "cikis_tetikleyicileri": [
                "Yönetim veya hâkim hissedar değişimi ve kurumsal nitelik kaybı",
                "Hâkim hissedarın borsada doğrudan agresif pay satışı başlatması",
                "İlişkili taraflara şüpheli kaynak veya fon aktarımı",
                "Şirketin ana iş modelinde ve pazarında yapısal kırılma",
                "İlk analiz tezinde kritik bir hesaplama veya temel hata tespiti"
            ]
        }
    }


# ==============================================================================
# 4. TEFTİŞ MASASI VE HTML MOTORU (PORTFÖY KALKANI & 6 SEKME)
# ==============================================================================
def uret_sektor_paneli_html(mevcut_sektorler: dict, nakit_orani: float, nakit_tl: float, gercek_kasa_mi: bool) -> str:
    renk_paleti = [
        "#38bdf8", "#818cf8", "#fb923c", "#34d399", "#f472b6", 
        "#a78bfa", "#2dd4bf", "#e879f9", "#f97316", "#06b6d4"
    ]
    
    sirali = sorted(mevcut_sektorler.items(), key=lambda x: x[1], reverse=True)
    nakit_durum_metin = "🟢 FIRSAT YEDEĞİ GÜÇLÜ" if nakit_orani >= 10.0 else ("🟡 TAMPON DÜŞÜK / İZLEMEDE" if nakit_orani >= 5.0 else "🔴 NAKİT TÜKENDİ / ALIM FRENİ")
    nakit_durum_renk = "#34d399" if nakit_orani >= 10.0 else ("#fbbf24" if nakit_orani >= 5.0 else "#f87171")
    nakit_etiket = "Gerçek Kasa (PPF/Nakit)" if gercek_kasa_mi else "Hedef Nakit Tamponu (%10)"
    
    segmentler = f'<div style="width:{min(100.0, max(2.0, nakit_orani))}%; background:#facc15; height:100%;" title="{nakit_etiket}: %{nakit_orani:.1f} ({nakit_tl:,.0f} TL)"></div>'
    rozetler = f'''
        <div style="background:#1e293b; border:1px solid #facc15; border-radius:6px; padding:7px 10px; display:flex; justify-content:space-between; align-items:center;">
            <div style="display:flex; align-items:center; gap:6px;">
                <span style="width:10px; height:10px; border-radius:50%; background:#facc15; display:inline-block;"></span>
                <span style="font-size:11.5px; font-weight:bold; color:#f8fafc;">{nakit_etiket}</span>
            </div>
            <div style="text-align:right;">
                <strong style="color:#facc15; font-size:12px;">%{nakit_orani:.1f}</strong>
                <span style="font-size:9.5px; color:{nakit_durum_renk}; font-weight:bold; display:block;">{nakit_durum_metin}</span>
            </div>
        </div>
    '''
    
    for idx, (sek, pay) in enumerate(sirali):
        if pay <= 0.05: continue
        renk = renk_paleti[idx % len(renk_paleti)]
        tavan_asildi = pay > 20.0
        durum_renk = "#f87171" if tavan_asildi else "#34d399"
        durum_metin = "⚠️ %20 AŞIMI" if tavan_asildi else "🟢 GÜVENLİ"
        border_c = "#7f1d1d" if tavan_asildi else "#334155"
        
        segmentler += f'<div style="width:{pay:.1f}%; background:{renk}; height:100%; border-left:1px solid #0b0e14;" title="{sek}: %{pay:.1f}"></div>'
        rozetler += f'''
            <div style="background:#1e293b; border:1px solid {border_c}; border-radius:6px; padding:7px 10px; display:flex; justify-content:space-between; align-items:center;">
                <div style="display:flex; align-items:center; gap:6px; overflow:hidden;">
                    <span style="width:10px; height:10px; border-radius:50%; background:{renk}; display:inline-block; flex-shrink:0;"></span>
                    <span style="font-size:11.5px; color:#f8fafc; white-space:nowrap; text-overflow:ellipsis; overflow:hidden;">{sek}</span>
                </div>
                <div style="text-align:right; flex-shrink:0; margin-left:8px;">
                    <strong style="color:#f8fafc; font-size:12px;">%{pay:.1f}</strong>
                    <span style="font-size:9.5px; color:{durum_renk}; font-weight:bold; display:block;">{durum_metin}</span>
                </div>
            </div>
        '''

    return f'''
    <div style="background:#111827; border:1px solid #1f2937; border-radius:8px; padding:14px 16px; margin-bottom:24px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; flex-wrap:wrap; gap:8px;">
            <div>
                <span style="font-size:12px; font-weight:bold; color:#38bdf8; text-transform:uppercase; letter-spacing:0.5px;">
                    🛡️ SEKTÖREL ÇEŞİTLENDİRME KALKANI & VARLIK DAĞILIMI
                </span>
                <span style="font-size:11px; color:#64748b; margin-left:8px;">(Serkan Ünal 11. Bölüm: Tek Bir Sektöre Maksimum %20 Üst Sınır)</span>
            </div>
            <div style="font-size:11px; color:#94a3b8;">
                Mevcut Kasa / Nakit: <strong style="color:#facc15;">%{nakit_orani:.1f} ({nakit_tl:,.0f} TL)</strong> &nbsp;|&nbsp; Sektör Tavanı: <strong style="color:#38bdf8;">Maks %20.0</strong>
            </div>
        </div>
        
        <div style="display:flex; height:18px; border-radius:6px; overflow:hidden; background:#0f172a; margin-bottom:12px; box-shadow: inset 0 2px 4px rgba(0,0,0,0.5);">
            {segmentler}
        </div>

        <div style="grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); display:grid; gap:8px;">
            {rozetler}
        </div>
    </div>
    '''


def uret_portfoy_kalkani_html(tablo_verileri, karneler, toplam_portfoy, hisse_portfoy_tl, nakit_tl, hisse_sayisi, sektor_paneli_html):
    # --- FAZ 9: HTML VİTRİN ROZETLERİ İÇİN HAFIZA OKUMA ---
    import os, json
    onceki_kadro = set()
    ONCEKI_TARAMA_YOLU = os.path.join("hektor_kutuphane", "PORTFOY", "onceki_tarama.json")
    if os.path.exists(ONCEKI_TARAMA_YOLU):
        try:
            with open(ONCEKI_TARAMA_YOLU, "r", encoding="utf-8") as f:
                onceki_kadro = set(json.load(f).get("hedef_60_kadrosu", []))
        except Exception: pass

    # 4'LÜ DİNAMİK BUTON SAYAÇLARI
    sayi_hedef60 = sum(1 for r in tablo_verileri if r.get('kadroda', False))
    sayi_mevcut = sum(1 for r in tablo_verileri if r['Mevcut_Lot'] > 0)
    sayi_radar = sum(1 for r in tablo_verileri if r.get('is_radar', False))
    sayi_tum = len(tablo_verileri)

    satirlar_html = ""
    for r in tablo_verileri:
        kod = r["Kod"]
        
        # YENİ GİRİŞ ROZETİ KONTROLÜ
        yeni_giris_rozet = ""
        if r.get('kadroda', False) and onceki_kadro and kod not in onceki_kadro:
            yeni_giris_rozet = ' <span style="background:#10b981; color:#022c22; font-size:9px; padding:2px 5px; border-radius:4px; font-weight:bold; box-shadow: 0 0 5px #10b981; position:relative; top:-2px; margin-left:4px; animation: pulse 2s infinite;">YENİ</span>'

        kz_renk = "#34d399" if r["KZ_Yuzde"] >= 0 else "#f87171"
        fark_metin = f"+{r['Fark_Lot']:,}" if r['Fark_Lot'] > 0 else f"{r['Fark_Lot']:,}"

        # 4'LÜ SINIFLANDIRMA VE CSS ETİKETLERİ
        classes = ["hisse-satiri"]
        if r.get('kadroda', False):
            classes.append("satir-hedef60")
        if r['Mevcut_Lot'] > 0:
            classes.append("satir-mevcut")
        if r.get('is_radar', False):
            classes.append("satir-radar")
        if not r.get('kadroda', False) and r['Mevcut_Lot'] == 0:
            classes.append("satir-diger")

        satir_class = " ".join(classes)

        aksiyon = r["Aksiyon"]
        if r['Mevcut_Lot'] > 0:
            kz_gosterim = f" (<span style='color:{kz_renk}; font-weight:bold;'>%{r['KZ_Yuzde']}</span>)"
        else:
            kz_gosterim = ""

        if not r.get('kadroda', False) and r['Mevcut_Lot'] == 0 and r.get('is_radar', False):
            aksiyon = "RADAR / YEDEK"
            fark_metin = "-"
            renk = "badge-warn"
        elif not r.get('kadroda', False) and r['Mevcut_Lot'] == 0 and not r.get('is_radar', False):
            aksiyon = "ELENDİ"
            fark_metin = "-"
            renk = "badge-fail"
        else:
            renk = "badge-ok" if "AL" in aksiyon else ("badge-fail" if "SAT" in aksiyon else "badge-warn")

        # Mevcut % ve Hedef % için görsel barlar
        mevcut_bar_w = min(100, int(r['Mevcut_Pay'] * 5))
        hedef_bar_w = min(100, int(r['Hedef_Pay'] * 5))

        bar_mevcut_html = f"""
        <div style="font-size:11px; margin-bottom:2px;">%{r['Mevcut_Pay']}</div>
        <div style="background:#1e293b; height:5px; border-radius:3px; width:70px; overflow:hidden;">
            <div style="background:#38bdf8; width:{mevcut_bar_w}%; height:100%;"></div>
        </div>"""

        bar_hedef_html = f"""
        <div style="font-size:11px; font-weight:bold; color:#facc15; margin-bottom:2px;">%{r['Hedef_Pay']}</div>
        <div style="background:#1e293b; height:5px; border-radius:3px; width:70px; overflow:hidden;">
            <div style="background:#facc15; width:{hedef_bar_w}%; height:100%;"></div>
        </div>"""

        satirlar_html += f"""
        <tr class="{satir_class}" onclick="acKarneModal('{kod}')" style="cursor:pointer;">
            <td><strong style="color:#38bdf8;">{kod} 🔍</strong>{yeni_giris_rozet}</td>
            <td>{r['Sektör']}</td>
            <td>{r['Hendek']}</td>
            <td><strong>{r['F_Skor']}/9</strong></td>
	    <td>{r.get('ROE_Rozet', '-')}</td>
            <td>{r['Mevcut_Lot']:,}</td>
            <td>{r['Fiyat']:.2f} TL{kz_gosterim}</td>
            <td><strong>{r['Mevcut_Tutar']:,.0f} TL</strong></td>
            <td>{bar_mevcut_html}</td>
            <td>{bar_hedef_html}</td>
            <td>{r['Hedef_Tutar']:,.0f} TL</td>
            <td>{r['Hedef_Lot']:,}</td>
            <td><strong>{fark_metin}</strong></td>
            <td><span class="{renk}">{aksiyon}</span></td>
        </tr>"""

    karneler_json_str = json.dumps(karneler, ensure_ascii=False)

    html_icerik = f"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <title>KARTAL — Dinamik BIST Radar</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🦅</text></svg>">
    <style>
        body {{ background-color: #0b0e14; color: #e2e8f0; font-family: monospace, system-ui; margin: 0; padding: 24px; }}
        .header {{ border-bottom: 1px solid #1e293b; padding-bottom: 16px; margin-bottom: 24px; }}
        .strip {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }}
        .strip-card {{ background: #111827; border: 1px solid #1f2937; padding: 14px; border-radius: 8px; }}
        .strip-card .label {{ font-size: 11px; color: #64748b; text-transform: uppercase; }}
        .strip-card .val {{ font-size: 18px; font-weight: bold; margin-top: 6px; color: #f8fafc; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 12px; background: #111827; border-radius: 8px; }}
        th, td {{ text-align: left; padding: 9px 8px; border-bottom: 1px solid #1e293b; }}
        th {{ color: #94a3b8; font-size: 11px; text-transform: uppercase; }}
        tr:hover {{ background-color: #1e293b; }}
	.satir-mevcut td {{ background-color: rgba(56, 189, 248, 0.05); border-top: 1px solid #0369a1; border-bottom: 1px solid #0369a1; }}
        .satir-mevcut td:first-child {{ border-left: 4px solid #38bdf8; }}
        .satir-hedef60 td {{ border-bottom: 1px solid #1e293b; }}
        
        .btn-filtre {{ padding: 10px 20px; font-weight: bold; cursor: pointer; background: #1e293b; color:#94a3b8; border:1px solid #334155; border-radius:6px; transition: all 0.2s; }}
        .btn-filtre.aktif {{ background: #eab308; color: #0f172a; border-color: #facc15; }}
        .badge-ok {{ background: #064e3b; color: #34d399; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
        .badge-warn {{ background: #78350f; color: #fbbf24; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
        .badge-fail {{ background: #7f1d1d; color: #f87171; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }}

        .modal-overlay {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.85); display: none; z-index: 1000; }}
        .drawer {{ position: fixed; right: -100vw; top: 2.5%; width: 95vw; height: 95vh; background: #0f172a; border-radius: 12px 0 0 12px; border-left: 2px solid #38bdf8; padding: 22px; box-sizing: border-box; overflow-y: auto; transition: right 0.3s ease-in-out; z-index: 1001; box-shadow: -5px 0 25px rgba(0,0,0,0.5); }}
        .drawer.active {{ right: 0; }}
        .close-btn {{ background: #1e293b; color: #cbd5e1; border: 1px solid #334155; padding: 6px 12px; border-radius: 6px; cursor: pointer; float: right; font-weight: bold; }}

        .tabs-bar {{ display: flex; gap: 8px; margin: 14px 0 18px 0; border-bottom: 1px solid #334155; padding-bottom: 10px; flex-wrap: wrap; }}
        .tab-btn {{ background: #1e293b; color: #94a3b8; border: 1px solid #334155; padding: 8px 12px; border-radius: 6px; cursor: pointer; font-size: 11.5px; font-weight: bold; }}
        .tab-btn.active {{ background: #eab308; color: #0f172a; border-color: #facc15; }}
        .tab-pane {{ display: none; }}
        .tab-pane.active {{ display: block; }}

        .section-title {{ color: #38bdf8; font-size: 13px; border-bottom: 1px solid #334155; padding-bottom: 6px; margin: 14px 0 10px 0; font-weight: bold; text-transform: uppercase; }}
        .info-card {{ background: #111827; border: 1px solid #1f2937; border-radius: 8px; padding: 12px 14px; margin-bottom: 10px; }}
        .info-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px; }}
        .big-val {{ font-size: 17px; font-weight: bold; color: #34d399; margin: 4px 0; }}
        .meal-text {{ font-size: 11.5px; color: #cbd5e1; line-height: 1.45; margin-top: 4px; }}
        .cikis-kural {{ background: #1c1917; border-left: 3px solid #ef4444; padding: 6px 10px; margin-bottom: 5px; border-radius: 4px; font-size: 11px; color: #fecaca; }}
        .good-box {{ background: #064e3b33; border-left: 3px solid #10b981; padding: 8px 10px; margin-bottom: 6px; border-radius: 4px; font-size: 11.5px; color: #d1fae5; }}
        .warn-box {{ background: #78350f44; border-left: 3px solid #f59e0b; padding: 8px 10px; margin-bottom: 6px; border-radius: 4px; font-size: 11.5px; color: #fef3c7; }}

        .matris-table {{ width: 100%; border-collapse: collapse; font-size: 11.5px; margin: 12px 0 18px 0; background: #0b1120; border: 1px solid #1e293b; border-radius: 6px; }}
        .matris-table th {{ background: #1e293b; color: #38bdf8; padding: 8px 10px; text-align: left; border-bottom: 2px solid #334155; }}
        .matris-table td {{ padding: 10px 8px; vertical-align: top; border-bottom: 1px solid #1e293b; border-right: 1px solid #1e293b; line-height: 1.4; }}
        .matris-table tr:hover {{ background: #111827; }}

        .dna-ribbon-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 12px; }}
        .dna-card {{ background: rgba(15, 23, 42, 0.85); border-radius: 8px; padding: 10px 12px; border-left: 4px solid #64748b; }}
        .dna-card.strength {{ border-left-color: #10b981; background: linear-gradient(135deg, rgba(16,185,129,0.12), rgba(15,23,42,0.9)); }}
        .dna-card.momentum {{ border-left-color: #38bdf8; background: linear-gradient(135deg, rgba(56,189,248,0.12), rgba(15,23,42,0.9)); }}
        .dna-card.risk     {{ border-left-color: #f43f5e; background: linear-gradient(135deg, rgba(244,63,94,0.14), rgba(15,23,42,0.9)); }}
        .dna-badge-label {{ font-size: 10px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }}
        .dna-card.strength .dna-badge-label {{ color: #34d399; }}
        .dna-card.momentum .dna-badge-label {{ color: #38bdf8; }}
        .dna-card.risk     .dna-badge-label {{ color: #fb7185; }}
        .dna-title {{ font-size: 12px; font-weight: bold; color: #f8fafc; margin-bottom: 3px; }}
        .dna-desc {{ font-size: 11px; color: #cbd5e1; line-height: 1.35; }}

        .oz-turkce-strip {{ display: grid; grid-template-columns: 1fr 1fr; gap: 7px 12px; background: #111827; border: 1px solid #334155; border-radius: 8px; padding: 10px 12px; margin-bottom: 14px; }}
        .oz-turkce-item {{ font-size: 11.5px; color: #e2e8f0; line-height: 1.42; }}
        .oz-turkce-item strong {{ color: #facc15; }}

        .invalidation-box {{ background: linear-gradient(135deg, rgba(127,29,29,0.35), rgba(15,23,42,0.95)); border: 1px solid #ef4444; border-left: 4px solid #ef4444; border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; font-size: 12px; color: #fecaca; line-height: 1.5; }}
        @keyframes pulse {{ 0% {{ box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }} 70% {{ box-shadow: 0 0 0 5px rgba(16, 185, 129, 0); }} 100% {{ box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }} }}
    </style>
</head>
<body>
    <div class="header">
        <h1 style="color:#38bdf8; margin:0 0 6px 0;">KARTAL — Dinamik BIST Portföy Kalkanı & Serkan Ünal Teftiş Masası</h1>
        <p style="color:#94a3b8; margin:0; font-size:12px;">Serkan Ünal 11. Bölüm Karar Sistemi: Dinamik Kasa Tamponu, %20 Sektör Üst Sınırı ve 4 Çeyreklik Kurumsal Denetim Matrisi.</p>
    </div>
    
    <div class="strip">
        <div class="strip-card"><div class="label">Toplam Portföy Büyüklüğü</div><div class="val">{toplam_portfoy:,.0f} TL</div></div>
        <div class="strip-card"><div class="label">Hisse Yatırımları Değeri</div><div class="val" style="color:#38bdf8;">{hisse_portfoy_tl:,.0f} TL</div></div>
        <div class="strip-card"><div class="label">Kasa / Nakit Tamponu</div><div class="val" style="color:#facc15;">{nakit_tl:,.0f} TL</div></div>
        <div class="strip-card"><div class="label">Sektör Kalkanı & Havuz</div><div class="val" style="color:#34d399;">Maks %20 | {hisse_sayisi} Hisse</div></div>
    </div>

    {sektor_paneli_html}

    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 10px;">
        <div style="display: flex; gap: 8px; flex-wrap: wrap;">
            <button onclick="hisseFiltrele('hedef60')" id="btn-hedef60" class="btn-filtre aktif">🎯 Hedef 60 Portföy ({sayi_hedef60})</button>
            <button onclick="hisseFiltrele('mevcut')" id="btn-mevcut" class="btn-filtre">🛡️ Mevcut Lotlar ({sayi_mevcut})</button>
            <button onclick="hisseFiltrele('radar')" id="btn-radar" class="btn-filtre">🔭 Geniş Radar ({sayi_radar})</button>
            <button onclick="hisseFiltrele('tum')" id="btn-tum" class="btn-filtre">🌐 Tümü ({sayi_tum})</button>
        </div>
        <div>
            <input type="text" id="hisseArama" onkeyup="tablodaAra()" placeholder="Hisse Kodu veya Adı Ara..." 
            style="padding: 10px 15px; width: 250px; border-radius: 6px; border: 1px solid #334155; background-color: #1e293b; color: #f8fafc; font-weight:bold; font-family: monospace;">
        </div>
    </div>

    <table id="portfoy-tablosu">
        <thead>
            <tr>
                <th>Kod</th><th>Sektör</th><th>Hendek</th><th>F-Skor</th><th>ROE</th><th>Mevcut Lot</th><th>Güncel Fiyat</th><th>Mevcut Tutar</th><th>Mevcut %</th><th>Hedef %</th><th>Hedef Tutar</th><th>Hedef Lot</th><th>Pazartesi Fark Lot</th><th>Aksiyon</th>
            </tr>
        </thead>
        <tbody>
            {satirlar_html}
        </tbody>
    </table>

    <div class="modal-overlay" id="modalOverlay" onclick="kapatKarneModal()"></div>
    <div class="drawer" id="karneDrawer">
        <button class="close-btn" onclick="kapatKarneModal()">✕ Kapat</button>
        <h2 id="modalHisseBaslik" style="margin-top:0; color:#38bdf8; font-size:20px;"></h2>
        <div id="modalUstBant"></div>

        <div class="tabs-bar">
            <button class="tab-btn active" onclick="sekmeDegistir('tab1', this)">1. ŞİRKETİ TANI & DEĞERLEME</button>
            <button class="tab-btn" onclick="sekmeDegistir('tab2', this)">2. ŞİRKETİN DNA'SI & GİDİŞAT</button>
            <button class="tab-btn" onclick="sekmeDegistir('tab3', this)">3. BİLANÇO MEALİ & KAP</button>
            <button class="tab-btn" onclick="sekmeDegistir('tab4', this)" style="border-color:#38bdf8; color:#38bdf8;">4. DESTEK / DİRENÇ (60G)</button>
            <button class="tab-btn" onclick="sekmeDegistir('tab5', this)">5. SON SÖZ & ÇIKIŞ RADARI</button>
            <button class="tab-btn" onclick="sekmeDegistir('tab6', this)">6. DİPNOT RÖNTGENİ & MATRİS</button>
        </div>

        <div id="tab1" class="tab-pane active"></div>
        <div id="tab2" class="tab-pane"></div>
        <div id="tab3" class="tab-pane"></div>
        <div id="tab4" class="tab-pane"></div>
        <div id="tab5" class="tab-pane"></div>
        <div id="tab6" class="tab-pane"></div>
    </div>

    <script>
        
    const KARNELER = {karneler_json_str};

        // --- ARAMA KUTUSU (SEARCH) MOTORU ---
        function tablodaAra() {{
            const input = document.getElementById("hisseArama").value.toUpperCase();
            const table = document.getElementById("portfoy-tablosu");
            const tr = table.getElementsByTagName("tr");
            
            if (input.length > 0) {{
                document.querySelectorAll('.btn-filtre').forEach(b => b.classList.remove('aktif'));
            }} else {{
                hisseFiltrele('hedef60');
                return;
            }}

            for (let i = 1; i < tr.length; i++) {{
                const tdKod = tr[i].getElementsByTagName("td")[0];
                const tdAd = tr[i].getElementsByTagName("td")[1];
                if (tdKod || tdAd) {{
                    const txtKod = tdKod.textContent || tdKod.innerText;
                    const txtAd = tdAd.textContent || tdAd.innerText;
                    if (txtKod.toUpperCase().indexOf(input) > -1 || txtAd.toUpperCase().indexOf(input) > -1) {{
                        tr[i].style.display = "";
                    }} else {{
                        tr[i].style.display = "none";
                    }}
                }}
            }}
        }}

        window.onload = function() {{
            hisseFiltrele('hedef60'); // Sayfa ilk açılışta doğrudan Hedef 60'ı gösterir
        }};
        // ------------------------------------------

        // --- 4'LÜ DİNAMİK FİLTRE MOTORU ---
        function hisseFiltrele(tur) {{
            const satirlar = document.querySelectorAll('.hisse-satiri');
            satirlar.forEach(satir => {{
                if (tur === 'tum') {{
                    satir.style.display = '';
                }} else if (tur === 'hedef60') {{
                    satir.style.display = satir.classList.contains('satir-hedef60') ? '' : 'none';
                }} else if (tur === 'mevcut') {{
                    satir.style.display = satir.classList.contains('satir-mevcut') ? '' : 'none';
                }} else if (tur === 'radar') {{
                    satir.style.display = satir.classList.contains('satir-radar') ? '' : 'none';
                }}
            }});

            document.querySelectorAll('.btn-filtre').forEach(b => b.classList.remove('aktif'));
            const secilenBtn = document.getElementById('btn-' + tur);
            if (secilenBtn) secilenBtn.classList.add('aktif');
        }}

        // ------------------------------------

        function sekmeDegistir(tabId, btn) {{
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            btn.classList.add('active');
        }}

        function uretDinamikDnaVeTercume(k) {{
            if (k.dna_ozeti && k.oz_turkce_seridi) {{
                return {{ dna_ozeti: k.dna_ozeti, oz_turkce_seridi: k.oz_turkce_seridi }};
            }}
            const kod = k.kod || '';
            const sektor = k.sektor || '';
            const kategori = k.kategori || 'Hendeksiz';
            const f_skor = k.f_skor || 6;
            const dg = k.dna_ve_gidisat || [];
            const st = k.sirketi_tani || {{}};
            const db = k.degerleme_bandi || {{ fk: 0, pfk: 0, konum_yuzde: 50 }};
            const b1 = k.bolum1_elemeler || [];

            const num = (str, rgx, defVal=0) => {{
                if (!str) return defVal;
                const m = String(str).replace(',', '.').match(rgx);
                return m ? parseFloat(m[1]) : defVal;
            }};

            const ciroBuyume = num(dg[0]?.deger, /([+-]?[0-9.]+)/, 0);
            const efkMarji = num(dg[2]?.deger, /([+-]?[0-9.]+)/, 0);
            const marjDegisim = num(dg[2]?.yorum, /([+-]?[0-9.]+)\\s*puan/, 0);
            const inaNk = num(dg[4]?.deger, /([+-]?[0-9.]+)\\s*TL/, 0);
            const kvBorc = num(dg[3]?.yorum, /%([0-9.]+)/, 50);
            const nbOzk = num(dg[3]?.yorum, /rasyosu\\s*([+-]?[0-9.]+)x/, 0);
            const roe = num(st.roe_deger, /([+-]?[0-9.]+)/, 0);
            const fdFavok = num(st.carpan_ozet, /FD\\/FAVÖK:\\s*([0-9.]+)x/, 0);
            const fk = db.fk || 0;
            const bantYuzde = db.konum_yuzde || 50;
            const halkaAciklik = num(b1[0]?.deger, /([0-9.]+)/, 35);

            const pdRaw = num(st.piyasa_degeri, /([0-9.]+)/, 0);
            const pdMilyar = String(st.piyasa_degeri || '').includes('milyar') ? pdRaw : (pdRaw / 1000.0);

            const bdDonemler = k.birikimli_dipnot?.donemler || {{}};
            const dKeys = Object.keys(bdDonemler).sort();
            const sonDipnot = dKeys.length ? bdDonemler[dKeys[dKeys.length - 1]] : {{}};
            const icr = sonDipnot.icr_kati ?? 3.0;
            const dts = sonDipnot.dts_skor ?? 0;
            const dso = sonDipnot.dso_gun ?? 0;

            const bankaMi = /banka|sigorta|emeklilik|faktoring/i.test(sektor) || ['AKBNK','GARAN','ISCTR','YKBNK','HALKB','SKBNK','QNBTR','KLNMA','ICBCT','AGESA','ANHYT','ANSGR','TURSG','QUICK','OYYAT','DSTKF'].includes(kod);
            const ozkOrani = Math.max(20, Math.min(88, Math.round(100 / (1 + Math.max(0.15, nbOzk * 0.75 + 0.35)))));

            const tahtaDerinligi = (pdMilyar >= 15 && halkaAciklik >= 10 && halkaAciklik <= 70)
                ? {{ sinif: 'Kurumsal / Likit 250 Tahta', rozet_renk: 'badge-ok' }}
                : (pdMilyar >= 2.5 ? {{ sinif: 'Orta Ölçek / Standart Tahta', rozet_renk: 'badge-ok' }} : {{ sinif: 'Sığ Tahta / Likidite Uyarısı', rozet_renk: 'badge-warn' }});

            let karCumlesi, nakitCumlesi, borcCumlesi;
            if (bankaMi) {{
                karCumlesi = `Finans/Sigorta bilançosu: Yıllıklandırılmış özkaynak kârlılığı (ROE) %${{roe.toFixed(1)}} düzeyinde.`;
                nakitCumlesi = `Son dönem net kârı ${{st.net_kar_12ay || '-'}}; Piotroski F-Skoru ${{f_skor}}/9 ile bilanço kalitesi korunuyor.`;
                borcCumlesi = `Finansal kuruluş yapısı (Halka açıklık %${{halkaAciklik.toFixed(1)}}); ticari borç yerine sermaye yeterliliği ve ROE izlenir.`;
            }} else {{
                karCumlesi = efkMarji > 0
                    ? `Her 100 TL'lik satıştan kasaya ${{efkMarji.toFixed(1)}} TL esas faaliyet kârı kalıyor (Marj değişimi: ${{marjDegisim >= 0 ? '+' : ''}}${{marjDegisim.toFixed(1)}} puan).`
                    : `Şirket ana faaliyetinden zarar yazıyor; her 100 TL satışta ${{Math.abs(efkMarji).toFixed(1)}} TL operasyonel erime var.`;
                nakitCumlesi = inaNk >= 1.0
                    ? `Kağıt üzerindeki her 1 TL net kâra karşılık kasaya ${{inaNk.toFixed(2)}} TL gerçek işletme nakdi (CFO) giriyor.`
                    : `Yazılan her 1 TL net kârın ${{inaNk.toFixed(2)}} TL'si nakde dönüyor; alacak tahsil süresi (DSO) ${{dso.toFixed(0)}} gün.`;
                borcCumlesi = nbOzk <= 0.05
                    ? `Varlıkların ~%${{ozkOrani}}'i özkaynak; şirket net nakit konumunda (Yükümlülüklerin %${{kvBorc.toFixed(0)}}'i kısa vadeli).`
                    : `Varlıkların ~%${{ozkOrani}}'i özkaynak, Net Borç/ÖZK ${{nbOzk.toFixed(2)}}x; faaliyet kârı faizi ${{icr.toFixed(2)}}x karşılıyor (%${{kvBorc.toFixed(0)}} kısa vade).`;
            }}
            const degerlemeCumlesi = fk > 0
                ? `Mevcut kâr temposuyla piyasa değerini ${{fk.toFixed(1)}} yılda amorti ediyor${{fdFavok > 0 ? ', FD/FAVÖK ' + fdFavok.toFixed(1) + 'x' : ''}}; tarihsel bandın %${{bantYuzde}} diliminde.`
                : `${{fdFavok > 0 ? 'FD/FAVÖK ' + fdFavok.toFixed(1) + 'x çarpanında; ' : ''}}Net kâr bazlı F/K hesaplanamıyor; tarihsel bandın %${{bantYuzde}} diliminde.`;

            const enGuclu = bankaMi
                ? {{ baslik: `Finansal Güç & ROE (%${{roe.toFixed(1)}})`, detay: `${{fk > 0 ? fk.toFixed(1) + 'x F/K çarpanı ve ' : ''}}${{f_skor}}/9 Piotroski skoruyla sektör içinde dengeli konumda.` }}
                : (inaNk >= 1.5 && efkMarji >= 15)
                    ? {{ baslik: 'Yüksek Nakit Üretim Makinesi', detay: `%${{efkMarji.toFixed(1)}} faaliyet marjı ve 1 TL kâra ${{inaNk.toFixed(2)}} TL nakit dönüşümüyle kârı kasaya indiriyor.` }}
                    : (nbOzk <= 0.10)
                        ? {{ baslik: 'Net Nakit & Çelik Bilanço Zırhı', detay: `${{nbOzk.toFixed(2)}}x Net Borç/ÖZK rasyosuyla finansman gideri baskısına karşı korunaklı.` }}
                        : {{ baslik: `${{kategori}} Hendek & Nakit Akışı (${{f_skor}}/9)`, detay: `1 TL net kâra karşılık ${{inaNk.toFixed(2)}} TL işletme nakdi üreterek operasyonlarını finanse ediyor.` }};

            const ivme = (marjDegisim >= 1.0 && ciroBuyume >= 10)
                ? {{ baslik: 'Çift Motorlu İvme (Ciro + Marj)', detay: `Satışlar %${{ciroBuyume.toFixed(1)}} büyürken esas faaliyet marjı +${{marjDegisim.toFixed(1)}} puan genişledi.` }}
                : (ciroBuyume >= 20)
                    ? {{ baslik: 'Güçlü Satış & Pazar Payı İvmesi', detay: `Satış gelirleri %${{ciroBuyume.toFixed(1)}} artışla üst satır büyümesini sürüklemeye devam ediyor.` }}
                    : (marjDegisim >= 0.5)
                        ? {{ baslik: 'Maliyet Kontrolü & Marj İyileşmesi', detay: `Esas faaliyet kâr marjı geçen döneme göre +${{marjDegisim.toFixed(1)}} puan iyileşti.` }}
                        : {{ baslik: 'Nakit Kalkanı & Dengelenme', detay: `Operasyonel marj dengelenme sürecinde; nakit akışı ve işletme sermayesi disiplini korunuyor.` }};

            let kritikRisk;
            if (!bankaMi && ((kvBorc >= 75 && nbOzk >= 0.5) || (icr > 0 && icr < 1.5) || nbOzk >= 1.2)) {{
                kritikRisk = {{
                    baslik: 'Kısa Vadeli Borç Çevirme & Finansman Yükü',
                    detay: `Borcun %${{kvBorc.toFixed(0)}}'i kısa vadeli; Net Borç/ÖZK ${{nbOzk.toFixed(2)}}x ve faiz karşılama (ICR) ${{icr.toFixed(2)}}x sınırında.`,
                    iptal_tetikleyicisi: `ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: Faiz karşılama oranı (ICR) 1.25x'in altına sarkar veya Net Borç/ÖZK rasyosu ${{Math.max(1.5, nbOzk + 0.35).toFixed(2)}}x eşiğini aşarsa temel tez iptal olur ve pozisyondan çıkılır.`
                }};
            }} else if (!bankaMi && (dso >= 180 || dts >= 35)) {{
                kritikRisk = {{
                    baslik: 'Ticari Alacak Vadesi (DSO) & Tahakkuk Baskısı',
                    detay: `Alacak tahsil süresi ${{dso.toFixed(0)}} gün (Dipnot Tehlike Skoru: ${{dts}}/100); işletme sermayesi alacakta bekliyor.`,
                    iptal_tetikleyicisi: `ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: Gelecek bilançoda DSO (${{dso.toFixed(0)}} gün) düşüşe geçmez ve İNA/Net Kâr oranı 1.00x'in altına inerse alacak kalitesi bozulmuş sayılır, pozisyon kapatılır.`
                }};
            }} else if (marjDegisim <= -2.0 || efkMarji < 0) {{
                kritikRisk = {{
                    baslik: 'Operasyonel Marj Daralması & Maliyet Baskısı',
                    detay: `Esas faaliyet marjı %${{efkMarji.toFixed(1)}} seviyesinde (Önceki döneme göre ${{marjDegisim.toFixed(1)}} puan değişim).`,
                    iptal_tetikleyicisi: `ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: Esas faaliyet kâr marjı gelecek çeyrekte toparlanamaz ve %${{Math.max(3.0, efkMarji - 1.5).toFixed(1)}} eşiğinin altında kalırsa büyüme hikayesi bozulmuş sayılır ve çıkılır.`
                }};
            }} else if (bantYuzde >= 80 || fk >= 18) {{
                kritikRisk = {{
                    baslik: 'Tarihsel Çarpan Doygunluğu (Yüksek Fiyatlama)',
                    detay: `F/K ${{fk.toFixed(1)}}x ile tarihsel değerleme bandının %${{bantYuzde}} diliminde; güvenlik marjı daralmış durumda.`,
                    iptal_tetikleyicisi: `ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: Yüksek çarpanı destekleyen satış/FAVÖK büyümesi gelecek çeyrekte yavaşlar veya stok devir hızı düşerse pahalı çarpan taşınmaz, kâr realizasyonu yapılır.`
                }};
            }} else {{
                kritikRisk = {{
                    baslik: 'Kısa Vadeli Borç Payı & Stok Devir Disiplini',
                    detay: `Bilançoda majör kırmızı bayrak yok; ancak yükümlülüklerin %${{kvBorc.toFixed(0)}}'lik kısa vadeli kısmı izleniyor.`,
                    iptal_tetikleyicisi: `ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: Kısa vadeli borç çevirme maliyetleri operasyonel kâr artışını geçer veya stok/alacak devir hızı üst üste 2 çeyrek bozulursa pozisyon ağırlığı azaltılır.`
                }};
            }}

            return {{
                dna_ozeti: {{ en_guclu_yani: enGuclu, iyiye_giden_taraf: ivme, kritik_risk: kritikRisk }},
                oz_turkce_seridi: [
                    {{ ikon: '⚙️', etiket: 'Operasyonel Güç', metin: karCumlesi }},
                    {{ ikon: '💵', etiket: 'Para Var mı?', metin: nakitCumlesi }},
                    {{ ikon: '🛡️', etiket: 'Bilanço Zırhı', metin: borcCumlesi }},
                    {{ ikon: '⚖️', etiket: 'Fiyatlama', metin: degerlemeCumlesi }}
                ],
                tahta_derinligi: tahtaDerinligi
            }};
        }}

        // 6 KÜMELİ DİNAMİK SEKTÖR ROE MOTORU
            // --- 12 ÇEYREKLİK ROE SPARKLINE SVG ÜRETİCİ ---
        function uretRoeSparkline(donemler, degerler) {{
            if (!degerler || degerler.length < 2) return '';
            const min = Math.min(...degerler);
            const max = Math.max(...degerler);
            const range = max - min || 1;
            const w = 260, h = 40;
            
            const points = degerler.map((v, i) => {{
                const x = (i / (degerler.length - 1)) * w;
                const y = h - ((v - min) / range) * (h - 10) - 5;
                return x.toFixed(1) + ',' + y.toFixed(1);
            }}).join(' ');

            const ilk = degerler[0];
            const son = degerler[degerler.length - 1];
            const renk = son >= ilk ? '#34d399' : '#f87171';
            const ok = son >= ilk ? '▲' : '▼';

            return '<div style="margin: 10px 0 6px 0; background: #0f172a; padding: 8px 12px; border-radius: 6px; border: 1px solid #1e293b;">' +
                   '<div style="display:flex; justify-content:space-between; font-size:10.5px; color:#94a3b8; margin-bottom:4px;">' +
                   '<span>📈 Çeyreklik ROE Trendi (Son ' + degerler.length + ' Çeyrek)</span>' +
                   '<span style="color:' + renk + '; font-weight:bold;">' + ok + ' %' + ilk + ' → %' + son + '</span>' +
                   '</div>' +
                   '<svg width="100%" height="40" viewBox="0 0 ' + w + ' ' + h + '" style="overflow:visible;">' +
                   '<polyline fill="none" stroke="' + renk + '" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" points="' + points + '" />' +
                   '</svg>' +
                   '</div>';
        }}

	    function uretRoeAnalizYorumu(roeDeger, sektorAdi) {{
            const roe = parseFloat(roeDeger) || 0;
            const s = (sektorAdi || '')
                .replace(/İ/g, 'i').replace(/I/g, 'i').replace(/ı/g, 'i')
                .replace(/Ş/g, 's').replace(/ş/g, 's')
                .replace(/Ğ/g, 'g').replace(/ğ/g, 'g')
                .replace(/Ü/g, 'u').replace(/ü/g, 'u')
                .replace(/Ö/g, 'o').replace(/ö/g, 'o')
                .replace(/Ç/g, 'c').replace(/ç/g, 'c')
                .toLowerCase();
            
            let kume = 'Ağır Sanayi & Üretim';
            let minNorm = 15;
            let ustNorm = 30;
            let motor = 'Kapasite Kullanımı & Emtia/Spread Dengesi';

            if (/perakende|magaza|gida|icecek/i.test(s)) {{
                kume = 'Organize Perakende & Tüketim'; minNorm = 35; ustNorm = 60;
                motor = 'Yüksek Aktif Devir Hızı (Stok Sirkülasyonu)';
            }} else if (/yazilim|bilisim|teknoloji|savunma|telekom/i.test(s)) {{
                kume = 'Hafif Varlık & Teknoloji'; minNorm = 30; ustNorm = 50;
                motor = 'Yüksek Net Faaliyet Marjı';
            }} else if (/banka|sigorta|faktoring|finans|menkul/i.test(s)) {{
                kume = 'Banka & Finans'; minNorm = 22; ustNorm = 38;
                motor = 'Finansal Kaldıraç & Net Faiz Marjı';
            }} else if (/demir|celik|petrol|kimya|cimento|otomotiv|maden/i.test(s)) {{
                kume = 'Ağır Sanayi & Döngüsel'; minNorm = 15; ustNorm = 30;
                motor = 'Emtia Döngüsü & Operasyonel Verimlilik';
            }} else if (/gyo|gayrimenkul|insaat|turizm|enerji|otel|tarim/i.test(s)) {{
                kume = 'Varlık Yoğun & Düşük Devir'; minNorm = 8; ustNorm = 20;
                motor = 'Varlık Değeri & Nakit Akış Verimi';
            }} else if (/holding/i.test(s)) {{
                kume = 'Holding & İştirak Grubu'; minNorm = 18; ustNorm = 32;
                motor = 'Konsolide İştirak Portföy Getirisi';
            }}

            let rozet, metin, stil;
            if (roe < 5.0) {{
                rozet = '<span style="background:#7f1d1d; color:#fca5a5; font-size:10px; font-weight:bold; padding:2px 6px; border-radius:4px;">🚨 REEL SERMAYE ERİMESİ</span>';
                metin = 'Nominal net kâr açıklansa da <strong>%' + roe.toFixed(1) + ' ROE</strong> sermaye maliyetinin ve enflasyonun fersah fersah altındadır. Şirketin devasa özkaynak tabanına oranla kârı yetersiz kalmakta, hissedar getirisi reel olarak erimektedir.';
                stil = 'border-left: 3px solid #ef4444; background: #450a0a22;';
            }} else if (roe < minNorm) {{
                rozet = '<span style="background:#854d0e; color:#fde047; font-size:10px; font-weight:bold; padding:2px 6px; border-radius:4px;">⚠️ SEKTÖR ALTI KÂRLILIK</span>';
                metin = 'Şirketin <strong>%' + roe.toFixed(1) + '</strong> seviyesindeki ROE\\'si, <em>' + kume + '</em> standartlarının (%' + minNorm + ' - %' + ustNorm + ') altında seyrediyor. DuPont motoru (' + motor + ') yeterli ivmeyi üretemiyor.';
                stil = 'border-left: 3px solid #f59e0b; background: #78350f22;';
            }} else if (roe <= ustNorm) {{
                rozet = '<span style="background:#065f46; color:#6ee7b7; font-size:10px; font-weight:bold; padding:2px 6px; border-radius:4px;">🟢 SEKTÖRLE UYUMLU / SAĞLIKLI</span>';
                metin = '<strong>%' + roe.toFixed(1) + ' ROE</strong> ile şirket, <em>' + kume + '</em> normlarına (%' + minNorm + ' - %' + ustNorm + ') tam uyumlu sermaye getirisi sağlıyor. Kârlılık hissedar için reel değer üretiyor.';
                stil = 'border-left: 3px solid #10b981; background: #064e3b22;';
            }} else {{
                rozet = '<span style="background:#1e3a8a; color:#93c5fd; font-size:10px; font-weight:bold; padding:2px 6px; border-radius:4px;">🚀 ÜSTÜN SERMAYE VERİMİ</span>';
                metin = '<strong>%' + roe.toFixed(1) + ' ROE</strong> sektör normunun (%' + ustNorm + ') belirgin üzerinde. Bu kârlılığın borç kaldıracından değil, operasyonel marjdan geldiği dipnotlardan teyit edilmelidir.';
                stil = 'border-left: 3px solid #38bdf8; background: #0c4a6e22;';
            }}

            return {{ rozet: rozet, metin: metin, stil: stil, kume: kume, minNorm: minNorm, ustNorm: ustNorm, motor: motor }};
        }}

        function acKarneModal(kod) {{
            const k = KARNELER[kod];
            if (!k) return;

            const katman = uretDinamikDnaVeTercume(k);
            const dna = katman.dna_ozeti;
            const serit = katman.oz_turkce_seridi;
	    
            const seritHtml = serit.map(item =>
                '<div class="oz-turkce-item"><span>' + item.ikon + ' <strong>' + item.etiket + ':</strong> ' + item.metin + '</span></div>'
            ).join('');

            document.getElementById('modalHisseBaslik').innerHTML = 
                '<div style="display:flex; align-items:center; flex-wrap:wrap;">' + 
                    kod + ' — ' + k.sirket_adi + 
                '</div>' +
                '<div style="margin-top:10px;">' + (k.etiketler_html || '') + '</div>' + 
                (k.buyume_rozetleri_html || '');

            document.getElementById('modalUstBant').innerHTML = 
                '<div style="background:#1e293b; padding:10px 14px; border-radius:6px; font-size:12px; margin-bottom:12px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">' +
                    '<div>' +
                        '<strong>Sektör:</strong> ' + k.sektor + ' &nbsp;|&nbsp; ' +
                        '<strong>Hendek:</strong> ' + k.kategori + ' &nbsp;|&nbsp; ' +
                        '<strong>Fiyat:</strong> ' + k.fiyat_tl + ' TL &nbsp;|&nbsp; ' +
                        '<strong>Piotroski:</strong> ' + k.f_skor + '/9 &nbsp;|&nbsp; ' +
                        '<strong>Portföy Tavanı:</strong> Maks %' + k.tavan_agirlik + ' (Sektör Limiti: Maks %20)' +
                    '</div>' +
                '</div>' +
                '<div class="dna-ribbon-grid">' +
                    '<div class="dna-card strength">' +
                        '<div class="dna-badge-label">🟢 EN GÜÇLÜ YANI</div>' +
                        '<div class="dna-title">' + dna.en_guclu_yani.baslik + '</div>' +
                        '<div class="dna-desc">' + dna.en_guclu_yani.detay + '</div>' +
                    '</div>' +
                    '<div class="dna-card momentum">' +
                        '<div class="dna-badge-label">🔵 İYİYE GİDEN TARAF</div>' +
                        '<div class="dna-title">' + dna.iyiye_giden_taraf.baslik + '</div>' +
                        '<div class="dna-desc">' + dna.iyiye_giden_taraf.detay + '</div>' +
                    '</div>' +
                    '<div class="dna-card risk">' +
                        '<div class="dna-badge-label">🔴 DİKKAT EDİLECEK TARAF</div>' +
                        '<div class="dna-title">' + dna.kritik_risk.baslik + '</div>' +
                        '<div class="dna-desc">' + dna.kritik_risk.detay + '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="oz-turkce-strip">' + seritHtml + '</div>';

            const st = k.sirketi_tani;
            const db = k.degerleme_bandi || {{fk: 0, pfk: 0, alt_fk: 6, medyan_fk: 11, ust_fk: 16, marj_durum: 'DENGELİ', marj_renk: 'badge-warn', konum_yuzde: 50}};

	    // ROE Analiz Motorunu Çalıştır (st tanımlandıktan sonra)
	    const safRoe = parseFloat(String(st.roe_deger).replace('%', '').replace(',', '.')) || 0;
            const roeAnaliz = uretRoeAnalizYorumu(safRoe, k.sektor);

            // === 1. TAB: ŞİRKETİ TANI & DEĞERLEME ===
            let t1 = 
                '<div class="info-card">' +
                    '<div style="color:#eab308; font-weight:bold; font-size:12px;">🏢 KİM BUNLAR & NE İŞ YAPIYORLAR?</div>' +
                    '<div class="meal-text">' + st.ne_is_yapar + '</div>' +
                    '<div style="color:#94a3b8; font-size:11px; margin-top:6px;"><strong>Ana Ürün & Pazarlar:</strong> ' + st.urun_ve_pazar + '</div>' +
                '</div>' +
                '<div class="info-grid">' +
                    '<div class="info-card">' +
                        '<div style="font-size:11px; color:#94a3b8;">PİYASA DEĞERİ & ÇARPANLAR</div>' +
                        '<div class="big-val">' + st.piyasa_degeri + '</div>' +
                        '<div class="meal-text">' + st.carpan_ozet + '</div>' +
                    '</div>' +
                    '<div class="info-card">' +
                        '<div style="display:flex; justify-content:space-between; align-items:center;">' +
                            '<span style="font-size:11px; color:#94a3b8; font-weight:bold;">SON DÖNEM NET KÂR & ROE</span>' +
                            roeAnaliz.rozet +
                        '</div>' +
                        '<div class="big-val" style="margin-top:6px;">' + st.net_kar_12ay + ' <span style="font-size:15px; color:#94a3b8; font-weight:normal;">(ROE: ' + st.roe_deger + ')</span></div>' +
                        '<div class="meal-text">' + st.net_kar_yorum + '</div>' +
                        uretRoeSparkline(k.roe_serisi?.donemler, k.roe_serisi?.roe_degerleri) +
                        
                        '<div style="margin-top:10px; padding:6px 10px; background:#0f172a; border-radius:5px; border:1px solid #1e293b; font-size:11px; display:flex; justify-content:space-between; color:#94a3b8;">' +
                            '<span>🎯 <strong>' + roeAnaliz.kume + ' Normu:</strong> %' + roeAnaliz.minNorm + ' - %' + roeAnaliz.ustNorm + '</span>' +
                            '<span style="color:#38bdf8;">⚙️ ' + roeAnaliz.motor + '</span>' +
                        '</div>' +
                        '<div style="font-size:11px; padding:9px 12px; border-radius:6px; margin-top:8px; ' + roeAnaliz.stil + '">' +
                            '<div style="color:#cbd5e1; line-height:1.45;">' + roeAnaliz.metin + '</div>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="info-card" style="border-left: 3px solid #38bdf8;">' +
                    '<div style="display:flex; justify-content:space-between; align-items:center;">' +
                        '<span style="font-size: 11px; color: #94a3b8; font-weight:bold;">📊 TARİHSEL DEĞERLEME BANDI & GÜVENLİK MARJI</span>' +
                        '<span class="' + db.marj_renk + '">' + db.marj_durum + '</span>' +
                    '</div>' +
                    '<div style="margin: 8px 0 4px 0; font-size: 12.5px;">' +
                        '<strong>Cari F/K:</strong> <span style="color:#38bdf8; font-size:14px; font-weight:bold;">' + (db.fk > 0 ? db.fk + 'x' : 'N/A') + '</span>' +
                    '</div>' +
                    '<div style="background: #1e293b; height: 10px; border-radius: 5px; position: relative; margin: 14px 0 6px 0;">' +
                        '<div style="position: absolute; left: 0%; width: 33%; height: 100%; background: #064e3b; border-radius: 5px 0 0 5px;"></div>' +
                        '<div style="position: absolute; left: 33%; width: 34%; height: 100%; background: #1e3a8a;"></div>' +
                        '<div style="position: absolute; left: 67%; width: 33%; height: 100%; background: #7f1d1d; border-radius: 0 5px 5px 0;"></div>' +
                        '<div style="position: absolute; left: ' + db.konum_yuzde + '%; top: -4px; width: 6px; height: 18px; background: #facc15; border-radius: 3px; transform: translateX(-50%); box-shadow: 0 0 6px #facc15;"></div>' +
                    '</div>' +
                    '<div style="display: flex; justify-content: space-between; font-size: 10.5px; color: #64748b;">' +
                        '<span>🟢 İskontolu (< ' + db.alt_fk + 'x)</span>' +
                        '<span>🔵 Medyan (~' + db.medyan_fk + 'x)</span>' +
                        '<span>🔴 Pahalı (> ' + db.ust_fk + 'x)</span>' +
                    '</div>' +
                '</div>';

            const fkVal = db.fk || 0;
            const pfkVal = db.pfk || 0;
            let pfkMetin = 'N/A';
            let pfkColor = '#64748b';
            let fkWidth = 0;
            let pfkWidth = 0;
            let degerlendirmeMetni = '';

            if (fkVal > 0 && pfkVal > 0) {{
                pfkMetin = pfkVal.toFixed(1) + 'x';
                pfkColor = pfkVal < fkVal * 0.9 ? '#10b981' : (pfkVal > fkVal * 1.3 ? '#ef4444' : '#f59e0b');
                fkWidth = Math.min(100, (fkVal / 40) * 100);
                pfkWidth = Math.min(100, (pfkVal / 40) * 100);
                if (pfkVal < fkVal * 0.8) {{
                    degerlendirmeMetni = '<span style="color:#10b981;">🚀 Yüksek İvme (Gelecekte Ucuzluyor)</span>';
                }} else if (pfkVal > fkVal * 1.4) {{
                    degerlendirmeMetni = '<span style="color:#ef4444;">⚠️ Değer Tuzağı / Kâr Erozyonu</span>';
                }} else {{
                    degerlendirmeMetni = '<span style="color:#f59e0b;">⚖️ İstikrarlı Fiyatlama</span>';
                }}
            }} else if (fkVal > 0) {{
                fkWidth = Math.min(100, (fkVal / 40) * 100);
            }}

            let pfkHtml = 
                '<div style="margin-top: 14px; background: #0f172a; padding: 12px; border-radius: 6px; border: 1px solid #334155;">' +
                    '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">' +
                        '<span style="font-size: 11px; color: #94a3b8; font-weight: bold; text-transform: uppercase;">Cari F/K vs P-F/K Kıyaslaması</span>' +
                        '<span style="font-size: 10.5px; font-weight: bold;">' + degerlendirmeMetni + '</span>' +
                    '</div>' +
                    '<div style="display: flex; flex-direction: column; gap: 8px;">' +
                        '<div style="display: flex; align-items: center; gap: 10px;">' +
                            '<span style="width: 55px; font-size: 11.5px; color: #cbd5e1;">Cari F/K</span>' +
                            '<div style="flex: 1; background: #1e293b; height: 12px; border-radius: 6px; overflow: hidden; border:1px solid #0b0e14;">' +
                                '<div style="width: ' + fkWidth + '%; background: #64748b; height: 100%; border-radius: 6px;"></div>' +
                            '</div>' +
                            '<span style="width: 45px; text-align: right; font-size: 12px; font-weight: bold; color: #f8fafc;">' + (fkVal > 0 ? fkVal.toFixed(1) + 'x' : 'N/A') + '</span>' +
                        '</div>' +
                        '<div style="display: flex; align-items: center; gap: 10px;">' +
                            '<span style="width: 55px; font-size: 11.5px; color: #38bdf8; font-weight: bold;">P-F/K</span>' +
                            '<div style="flex: 1; background: #1e293b; height: 12px; border-radius: 6px; overflow: hidden; border:1px solid #0b0e14;">' +
                                '<div style="width: ' + pfkWidth + '%; background: ' + pfkColor + '; height: 100%; border-radius: 6px;"></div>' +
                            '</div>' +
                            '<span style="width: 45px; text-align: right; font-size: 12px; font-weight: bold; color: ' + pfkColor + ';">' + pfkMetin + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>';

            t1 += pfkHtml;
            t1 += k.termometre_html || '';
            document.getElementById("tab1").innerHTML = t1;

            // === 2. TAB: ŞİRKETİN DNA'SI & GİDİŞAT ===
            let t2 = '<div class="section-title">🧬 ŞİRKETİN DNA\\'SI, NAKİT DÖNÜŞÜMÜ VE GİDİŞAT RÖNTGENİ</div>';
            k.dna_ve_gidisat.forEach(d => {{
                const bClass = (d.durum === 'RİSKLİ' || d.durum === 'DARALMA' || d.durum === 'İZLEMEDE' || d.durum === 'DÜŞÜŞ / ZARAR') ? 'badge-warn' : 'badge-ok';
                t2 += '<div class="info-card">' +
                    '<div style="display:flex; justify-content:space-between; align-items:center;">' +
                        '<strong style="color:#38bdf8; font-size:13px;">' + d.baslik + '</strong>' +
                        '<span><strong style="color:#f8fafc; font-size:13px;">' + d.deger + '</strong> &nbsp; <span class="' + bClass + '">' + d.durum + '</span></span>' +
                    '</div>' +
                    '<div class="meal-text">' + d.yorum + '</div>' +
                '</div>';
            }});
            document.getElementById('tab2').innerHTML = t2;

            // === 3. TAB: BİLANÇO MEALİ & KAP (2 SÜTUNLU GRID) ===
            let t3 = '<div style="display:grid; grid-template-columns: 1fr 1fr; gap:16px;">';
            t3 += '<div>';
            t3 += '<div class="section-title">📊 BİLANÇO DEĞİŞİMLERİ & VARLIK ETKİSİ</div>';
            k.bilanco_degisimleri.forEach(b => {{
                t3 += '<div class="info-card">' +
                    '<div style="display:flex; justify-content:space-between; margin-bottom:4px;">' +
                        '<strong style="color:#34d399; font-size:11.5px;">' + b.kalem + '</strong>' +
                        '<strong style="color:#f8fafc; font-size:12.5px;">' + b.guncel_tutar + '</strong>' +
                    '</div>' +
                    '<div class="meal-text"><strong style="color:#94a3b8;">Ne ifade ediyor?</strong> ' + b.ne_ifade_ediyor + '</div>' +
                '</div>';
            }});
            t3 += '</div>';

            t3 += '<div>';
            t3 += '<div class="section-title">🗞️ ÖNEMLİ KAP BİLDİRİMLERİ (MADDİ ÖNEM)</div>';
            const kapListesi = k.kap_haberleri || [];
            if (kapListesi.length > 0) {{
                kapListesi.forEach(kap => {{
                    t3 += '<div class="info-card" style="border-left: 3px solid #6366f1; margin-bottom:8px;">' +
                        '<div style="display:flex; justify-content:space-between; font-size:10.5px; color:#94a3b8; margin-bottom:4px;">' +
                            '<span style="color:#818cf8; font-weight:bold;">' + (kap.tarih || '-') + '</span>' +
                            '<a href="' + (kap.link || ('https://www.kap.org.tr/tr/Bildirim/' + kap.id)) + '" target="_blank" style="color:#38bdf8; text-decoration:none; font-weight:bold;">KAP Aç ↗</a>' +
                        '</div>' +
                        '<div style="font-size:12px; font-weight:bold; color:#f8fafc; line-height:1.4;">' + kap.baslik + '</div>' +
                    '</div>';
                }});
            }} else {{
                t3 += '<div class="info-card meal-text" style="color:#94a3b8;">Son dönemde filtreye takılan kritik bir KAP bildirimi bulunmamaktadır (Veya kap_indirici henüz çalışmadı).</div>';
            }}
            t3 += '</div>';
            t3 += '</div>';
            document.getElementById('tab3').innerHTML = t3;

            // === 4. TAB: DESTEK / DİRENÇ (60G) & TRADINGVIEW KÖPRÜSÜ ===
            const tv = k.teknik_veri;
            let t4 = '';
            
            // 1. TradingView Doğrudan Bağlantı Kartı
            const tvKartHtml = 
                '<div style="background: linear-gradient(135deg, rgba(30,58,138,0.25), rgba(15,23,42,0.95)); border: 1px solid #1e40af; border-radius: 8px; padding: 16px 20px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;">' +
                    '<div>' +
                        '<div style="font-size: 15px; font-weight: bold; color: #f8fafc; display: flex; align-items: center; gap: 8px;">' +
                            '<span>📈 ' + kod + ' Canlı TradingView Analizi</span>' +
                            '<span style="background: #0284c7; color: #fff; font-size: 10px; padding: 2px 6px; border-radius: 4px;">BIST:' + kod + '</span>' +
                        '</div>' +
                        '<div style="font-size: 11.5px; color: #94a3b8; margin-top: 4px;">' +
                            'Borsa İstanbul veri lisansı nedeniyle grafik TradingView üzerinde kendi şablon ve indikatörlerinizle tam ekran açılır.' +
                        '</div>' +
                    '</div>' +
                    '<div>' +
                        '<a href="https://tr.tradingview.com/chart/?symbol=BIST:' + kod + '" target="_blank" ' +
                           'style="background: #38bdf8; color: #0b0e14; text-decoration: none; font-weight: bold; font-size: 12.5px; padding: 10px 18px; border-radius: 6px; display: inline-flex; align-items: center; gap: 6px; box-shadow: 0 0 12px rgba(56,189,248,0.4);">' +
                            'Grafiği TradingView\\'de Aç ↗' +
                        '</a>' +
                    '</div>' +
                '</div>';

            // 2. KARTAL 60 Günlük Yerel Destek / Direnç ve R:R Matrisi
            if (tv) {{
                t4 = tvKartHtml + 
                    '<div class="info-card" style="background:#111827; border:1px solid #1f2937; padding:20px;">' +
                    '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">' +
                        '<div>' +
                            '<span style="font-size:16px; font-weight:bold; color:#38bdf8;">🎯 60 GÜNLÜK YEREL KANAL & ASİMETRİK R:R PLANI</span>' +
                            '<div style="font-size:11px; color:#94a3b8; margin-top:3px;">Son 60 işlem gününün reel tepe/dip dönüş seviyeleri baz alınmıştır.</div>' +
                        '</div>' +
                        '<div style="text-align:right;">' +
                            '<span style="background:' + tv.renk + '22; color:' + tv.renk + '; border:1px solid ' + tv.renk + '; font-size:11.5px; font-weight:bold; padding:4px 10px; border-radius:6px;">' + tv.durum + '</span>' +
                        '</div>' +
                    '</div>' +

                    '<div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; margin-bottom:16px;">' +
                        '<div style="background:#0f172a; padding:10px; border-radius:6px; border:1px solid #334155;">' +
                            '<div style="font-size:10.5px; color:#94a3b8;">GÜNCEL FİYAT</div>' +
                            '<div style="font-size:15px; font-weight:bold; color:#f8fafc; margin-top:2px;">' + tv.fiyat + ' TL</div>' +
                        '</div>' +
                        '<div style="background:#0f172a; padding:10px; border-radius:6px; border:1px solid #334155;">' +
                            '<div style="font-size:10.5px; color:#94a3b8;">60G ANA DESTEK</div>' +
                            '<div style="font-size:15px; font-weight:bold; color:#10b981; margin-top:2px;">' + tv.destek_60g + ' TL <small style="font-size:11px; color:#ef4444;">(-%' + tv.risk_yuzde + ')</small></div>' +
                        '</div>' +
                        '<div style="background:#0f172a; padding:10px; border-radius:6px; border:1px solid #334155;">' +
                            '<div style="font-size:10.5px; color:#94a3b8;">60G ANA DİRENÇ</div>' +
                            '<div style="font-size:15px; font-weight:bold; color:#ef4444; margin-top:2px;">' + tv.direnc_60g + ' TL <small style="font-size:11px; color:#10b981;">(+%' + tv.odul_yuzde + ')</small></div>' +
                        '</div>' +
                        '<div style="background:#0f172a; padding:10px; border-radius:6px; border:1px solid #334155;">' +
                            '<div style="font-size:10.5px; color:#94a3b8;">RİSK / ÖDÜL (R:R)</div>' +
                            '<div style="font-size:15px; font-weight:bold; color:#facc15; margin-top:2px;">1 : ' + tv.rr_orani + '</div>' +
                        '</div>' +
                    '</div>' +

                    '<div style="margin:20px 0 10px 0;">' +
                        '<div style="display:flex; justify-content:space-between; font-size:11px; color:#94a3b8; margin-bottom:6px;">' +
                            '<span>🛡️ Destek: ' + tv.destek_60g + ' TL</span>' +
                            '<span style="color:#f8fafc; font-weight:bold;">Kanal Konumu: %' + tv.kanal_konumu + '</span>' +
                            '<span>🎯 Direnç: ' + tv.direnc_60g + ' TL</span>' +
                        '</div>' +
                        '<div style="background:#1e293b; height:12px; border-radius:6px; position:relative; border:1px solid #0b0e14;">' +
                            '<div style="position:absolute; left:0; width:25%; height:100%; background:#064e3b; border-radius:6px 0 0 6px;"></div>' +
                            '<div style="position:absolute; left:25%; width:50%; height:100%; background:#1e3a8a;"></div>' +
                            '<div style="position:absolute; left:75%; width:25%; height:100%; background:#7f1d1d; border-radius:0 6px 6px 0;"></div>' +
                            '<div style="position:absolute; left:' + tv.kanal_konumu + '%; top:-4px; width:6px; height:20px; background:#facc15; border-radius:3px; transform:translateX(-50%); box-shadow:0 0 6px #facc15;"></div>' +
                        '</div>' +
                    '</div>' +

                    '<div style="display:flex; gap:12px; margin-top:16px;">' +
                        '<div style="background:#0f172a; padding:8px 12px; border-radius:6px; font-size:11.5px; border:1px solid #334155;">' +
                            '<span style="color:#94a3b8;">📊 20G Hacim Çarpanı:</span> <strong style="color:#f8fafc;">' + tv.hacim_kati + 'x Katı</strong>' +
                        '</div>' +
                        '<div style="background:#0f172a; padding:8px 12px; border-radius:6px; font-size:11.5px; border:1px solid #334155;">' +
                            '<span style="color:#94a3b8;">⚡ BIST 100 Göreceli Güç (1 Ay):</span> <strong style="color:' + (tv.rs_bist_fark >= 0 ? '#10b981' : '#ef4444') + ';">' + (tv.rs_bist_fark >= 0 ? '+' : '') + tv.rs_bist_fark + '%</strong>' +
                        '</div>' +
                    '</div>' +
                '</div>';
            }} else {{
                t4 = tvKartHtml + 
                    '<div class="info-card meal-text" style="color:#94a3b8;">Bu hisse için 60 günlük teknik kanal henüz hesaplanmadı. Yukarıdaki butonla TradingView grafiğini canlı inceleyebilirsiniz.</div>';
            }}
            document.getElementById('tab4').innerHTML = t4;

            // === 5. TAB: SON SÖZ & ÇIKIŞ RADARI (SADELEŞTİRİLMİŞ 2 SÜTUNLU YÖNETİCİ DÜZENİ) ===
            let t5 = '<div style="display:grid; grid-template-columns: 1.1fr 0.9fr; gap:18px;">';
            
            // Sol Sütun: Yönetici Sentezi & Dikkat Çeken / Risk Noktaları
            t5 += '<div>';
            t5 += '<div class="section-title">👁️ BORSACI GÖZÜYLE (YÖNETİCİ SENTEZİ)</div>' +
                  '<div class="info-card" style="font-size:12.5px; line-height:1.6; color:#f1f5f9; margin-bottom:14px;">' + k.son_soz.borsaci_gozuyle + '</div>';
            
            t5 += '<div class="section-title">⚖️ ÖNE ÇIKAN DENGELER (BİLANÇO DİNAMİĞİ)</div>';
            k.son_soz.dikkat_cekenler.forEach(g => {{ t5 += '<div class="good-box">✔ ' + g + '</div>'; }});
            k.son_soz.sevmediklerim.forEach(w => {{ t5 += '<div class="warn-box">! ' + w + '</div>'; }});
            t5 += '</div>';

            // Sağ Sütun: Dinamik İptal Koşulu ve Kompakt Çıkış Tetikleyicileri
            t5 += '<div>';
            const iptalKosulu = (k.son_soz && k.son_soz.iptal_kosulu) ? k.son_soz.iptal_kosulu : dna.kritik_risk.iptal_tetikleyicisi;
            t5 += '<div class="section-title" style="color:#fb7185;">🎯 ŞARTA BAĞLI TEZ İPTAL KOŞULU</div>' +
                  '<div class="invalidation-box">' +
                      '<strong>⚡ Dinamik Çıkış Kuralı (' + dna.kritik_risk.baslik + '):</strong><br>' + iptalKosulu +
                  '</div>';

            t5 += '<div class="section-title" style="color:#f87171;">🚨 SERKAN ÜNAL KESİN ÇIKIŞ TETİKLEYİCİLERİ</div>';
            t5 += '<div style="background:#111827; border:1px solid #1f2937; border-radius:8px; padding:10px;">';
            k.bolum5_karar_ve_cikis.cikis_tetikleyicileri.forEach((kural, idx) => {{ 
                t5 += '<div class="cikis-kural"><strong>' + (idx+1) + '.</strong> ' + kural + '</div>'; 
            }});
            t5 += '</div>';
            t5 += '</div>';

            t5 += '</div>';
            document.getElementById('tab5').innerHTML = t5;

            // === 6. TAB: DİPNOT RÖNTGENİ & FAALİYET MATRİSİ ===
            const bd = k.birikimli_dipnot || {{ donemler: {{}} }};
            const tumDonemler = Object.keys(bd.donemler || {{}}).sort();
            const son4Donem = tumDonemler.slice(-4);

            let enGuncelPdfDonem = null;
            for (let i = tumDonemler.length - 1; i >= 0; i--) {{
                const d = tumDonemler[i];
                const notlar = (bd.donemler[d] && bd.donemler[d].pdf_dipnot_notlari) || {{}};
                const vals = Object.values(notlar);
                if (vals.some(v => v && v.length > 5 && v !== '-')) {{
                    enGuncelPdfDonem = d;
                    break;
                }}
            }}
            if (!enGuncelPdfDonem && tumDonemler.length > 0) {{
                enGuncelPdfDonem = tumDonemler[tumDonemler.length - 1];
            }}

            const aktifVeri = (enGuncelPdfDonem && bd.donemler[enGuncelPdfDonem]) || {{}};
            const tutarlilikListesi = aktifVeri.tutarlilik_raporu || [];

            let t6 = '<div class="info-card" style="border-left: 4px solid #eab308; background: #17255422;">' +
                '<div style="display:flex; justify-content:space-between; align-items:center;">' +
                    '<div>' +
                        '<span style="background:#eab308; color:#0f172a; font-weight:bold; font-size:11px; padding:2px 8px; border-radius:4px;">🎙️ CFO İCRA KURULU BRİFİNGİ</span>' +
                        '<span style="color:#38bdf8; font-weight:bold; margin-left:8px; font-size:13px;">' + (enGuncelPdfDonem || '-') + ' Faaliyet Raporu Özeti</span>' +
                    '</div>' +
                    '<div style="font-size:11px; color:#94a3b8;">Arşiv: ' + (bd.son_guncelleme || '-') + '</div>' +
                '</div>' +
                '<div class="meal-text" style="font-size:12.5px; margin-top:8px; color:#f1f5f9;">' +
                    'Bu brifing, şirketin KAP\\'a sunduğu kurumsal faaliyet raporunun yönetim kurulu sunum hapıdır.' +
                '</div>' +
            '</div>';

            if (tutarlilikListesi.length > 0) {{
                t6 += '<div class="section-title" style="color:#facc15;">⚖️ YÖNETİM VAATLERİ & TUTARLILIK KARNESİ (' + enGuncelPdfDonem + ')</div>';
                tutarlilikListesi.forEach(item => {{
                    const sinif = item.includes('🚨') ? 'cikis-kural' : (item.includes('⚠️') ? 'warn-box' : 'good-box');
                    t6 += '<div class="' + sinif + '">' + item + '</div>';
                }});
            }}

            t6 += '<div class="section-title">🏛️ 4 ÇEYREKLİK KURUMSAL DENETİM MATRİSİ (FAALİYET RAPORU TAKİBİ)</div>' +
                '<div style="overflow-x:auto;"><table class="matris-table"><thead><tr>' +
                '<th style="width:22%;">Stratejik Alan</th>';
            son4Donem.forEach(d => {{
                const yildiz = (d === enGuncelPdfDonem) ? ' <span style="color:#eab308;">★</span>' : '';
                t6 += '<th style="width:19%;">' + d + yildiz + '</th>';
            }});
            t6 += '</tr></thead><tbody>';

            const alanlar = [
                {{ id: 'yatirim_ve_kapasite', ad: '1. Devam Eden Yatırımlar & Kapasite', alt: 'Gecikme / Takvim' }},
                {{ id: 'siparis_ve_backlog', ad: '2. Bakiye Sipariş & Backlog', alt: 'İş Hacmi / Pazar' }},
                {{ id: 'doviz_ve_turev', ad: '3. Döviz Açığı & Hedge', alt: 'Kur Riski Koruması' }},
                {{ id: 'tri_rehin_ipotek', ad: '4. Taahhüt, Rehin, İpotek (TRİ)', alt: 'Özkaynağa Oranla Kefalet' }}
            ];

            alanlar.forEach(al => {{
                t6 += '<tr>' +
                    '<td><strong style="color:#38bdf8;">' + al.ad + '</strong><br><small style="color:#64748b;">' + al.alt + '</small></td>';
                son4Donem.forEach(d => {{
                    const dVeri = bd.donemler[d] || {{}};
                    const notlar = dVeri.pdf_dipnot_notlari || {{}};
                    const metin = notlar[al.id] || '';
                    if (metin && metin.length > 5 && metin !== '-') {{
                        t6 += '<td>' + metin + '</td>';
                    }} else {{
                        t6 += '<td><span style="color:#475569;">Bu çeyrekte rapor bulunmuyor</span></td>';
                    }}
                }});
                t6 += '</tr>';
            }});
            t6 += '</tbody></table></div>';

            t6 += '<div class="section-title">📈 DÖNEMLER ARASI BİRİKİMLİ DİPNOT RASYOLARI (UFRS)</div>' +
                '<div style="overflow-x:auto;"><table style="width:100%; font-size:11.5px; margin-bottom:14px;"><thead><tr>' +
                '<th>Metrik</th>';
            son4Donem.forEach(d => {{ t6 += '<th>' + d + '</th>'; }});
            t6 += '<th>Eşik Değer</th></tr></thead><tbody>';

            const rasyolar = [
                {{ ad: '<strong>Dipnot Tehlike Skoru (DTS)</strong>', esik: '&lt; 25 Puan', fn: d => '<strong>' + (bd.donemler[d]?.dts_skor ?? '-') + '/100</strong>' }},
                {{ ad: 'Tahakkuk Oranı ((Kâr-CFO)/Aktif)', esik: '&le; %5.0 (Negatif = Sağlam)', fn: d => '%' + (bd.donemler[d]?.tahakkuk_orani ?? '-') }},
                {{ ad: 'Kağıt Üzeri Kâr Payı (Normalleştirilmiş)', esik: '&le; %35.0', fn: d => '%' + (bd.donemler[d]?.ykp_orani ?? '-') }},
                {{ ad: 'Alacak Tahsilat Süresi (DSO)', esik: 'Düşen Trend', fn: d => (bd.donemler[d]?.dso_gun ?? '-') + ' gün' }},
                {{ ad: 'Efektif Borç Maliyeti / ICR', esik: 'ICR &ge; 1.5x', fn: d => '%' + (bd.donemler[d]?.efm_yuzde ?? '-') + ' (' + (bd.donemler[d]?.icr_kati ?? '-') + 'x)' }},
                {{ ad: 'İlişkili Taraf Ticari Olmayan Alacak', esik: '&lt; %3.0', fn: d => '%' + (bd.donemler[d]?.iliskili_orani ?? '-') }}
            ];

            rasyolar.forEach(r => {{
                t6 += '<tr><td>' + r.ad + '</td>';
                son4Donem.forEach(d => {{ t6 += '<td>' + r.fn(d) + '</td>'; }});
                t6 += '<td>' + r.esik + '</td></tr>';
            }});
            t6 += '</tbody></table></div>';

            document.getElementById('tab6').innerHTML = t6;

            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById('tab1').classList.add('active');
            document.querySelector('.tab-btn').classList.add('active');

            document.getElementById('modalOverlay').style.display = 'block';
            document.getElementById('karneDrawer').classList.add('active');
        }}

        function kapatKarneModal() {{
            document.getElementById('modalOverlay').style.display = 'none';
            document.getElementById('karneDrawer').classList.remove('active');
        }}
    </script>
</body>
</html>"""

    PANO_YOLU = "index.html"

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_icerik)

# ==============================================================================
# KANTİTATİF TOLERANS VE KOMİSYON KALKANI (FAZ 5)
# ==============================================================================
POZISYON_TOLERANS_ORANI = 0.20          # %20: Pozisyon içi sapma eşiği
ASGARI_PORTFOY_AGIRLIK_ETKISI = 0.005   # %0.5: Portföyün %0.5'inden küçük farklar elenir

def toleransli_aksiyon_hesapla(mevcut_lot: int, hedef_lot: int, guncel_fiyat: float, toplam_portfoy_tl: float):
    fark_lot = int(hedef_lot - mevcut_lot)
    islem_tutari = round(abs(fark_lot * guncel_fiyat), 2)
    portfoy_etkisi = (islem_tutari / toplam_portfoy_tl) if toplam_portfoy_tl > 0 else 0.0

    if mevcut_lot > 0:
        sapma_orani = abs(fark_lot) / mevcut_lot
        # Sapma %20'den azsa VEYA toplam portföye etkisi %0.5'in altındaysa dokunma
        if sapma_orani <= POZISYON_TOLERANS_ORANI or portfoy_etkisi < ASGARI_PORTFOY_AGIRLIK_ETKISI:
            return "KORU / DENGEDE", 0, 0.0
        elif fark_lot > 0:
            return "AL / EKLE", fark_lot, islem_tutari
        else:
            return "SAT / AZALT", fark_lot, islem_tutari
    else:
        # Yeni alınacak aday hisse: Portföyün en az %0.5'i kadar ağırlık oluşturuyorsa
        if portfoy_etkisi >= ASGARI_PORTFOY_AGIRLIK_ETKISI and hedef_lot > 0:
            return "AL / EKLE", hedef_lot, round(hedef_lot * guncel_fiyat, 2)
        else:
            return "KORU / DENGEDE", 0, 0.0

# ==============================================================================
# 5. ANA AKIŞ MOTORU (GERÇEK KASA, SEKTÖR KALKANI & GOOGLE SHEETS AKTARIMI)
# ==============================================================================
if __name__ == "__main__":
    baslangic = time.time()

    SHEETS_APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxT_ytoZ5jeRBVmeqehcWF0ayntLazRwUhwmzKQvYjCKxnlKUkb944qVdrZoN6LAhI5Hw/exec"

    if not YEREL_MALI_VERITABANI:
        print("HATA: 'hektor_kutuphane/bist_mali_tablolar.json' bulunamadı.")
        exit(1)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] KARTAL Deterministik Analiz Motoru Başladı...")
    print(f"  Yerel ambardan {len(YEREL_MALI_VERITABANI)} BIST şirketi RAM'e yüklendi.")

    tum_sonuclar = []
    for kod in YEREL_MALI_VERITABANI.keys():
        s = analiz_et_sirket(kod)
        tum_sonuclar.append(s)

    bist_sozluk = {s["kod"]: s for s in tum_sonuclar}
    mevcut_pozisyonlar = portfoy_motoru.portfoy_ve_canli_fiyatlari_getir(bist_sozluk)

    # Geniş Radar Havuzu: Serkan Ünal Bölüm 1 Kırmızı Çizgileri geçen, pozitif nakit/kâr üreten ve F-Skoru >= 6 olan şirketler
    firsat_adaylari = [
        s for s in tum_sonuclar
        if s["bolum1_gecti_mi"]
        and (s["cfo_buyuk_net_kar"] or float(s.get("cfo_milyon_tl", 0.0)) > 0)
        and int(s.get("piotroski_skor", 0)) >= 6
        and float(s.get("roe", 0.0)) >= 5.0
    ]
    altin_aday_sayisi = sum(1 for s in firsat_adaylari if s["cfo_buyuk_net_kar"] and s["piotroski_skor"] >= 7)
    print(f"  Analiz Tamamlandı: Geniş Radar'da {len(firsat_adaylari)} nitelikli şirket bulundu ({altin_aday_sayisi} tanesi 1. Derece CFO>NetKâr & F>=7).")

    havuz_kodlari = set()
    for s in firsat_adaylari: havuz_kodlari.add(s["kod"])
    for k in mevcut_pozisyonlar.keys():
        if k not in ["NAKIT", "TRY", "KASA", "PPF"]:
            havuz_kodlari.add(k)

    # ==========================================================================
    # SERKAN ÜNAL 60 HİSSE HEDEF PORTFÖY VE SEKTÖR MOTORU
    # ==========================================================================
    # 1. Tüm şirketlere 4 Katmanlı Hibrit Puan (Temel + Sektörel ROE + Değerleme + Teknik) ata
    for s in tum_sonuclar:
        kod = s["kod"]
        tv_veri = KAP_VE_TEKNIK_AMBARI.get("teknik_veriler", {}).get(kod, None)
        s["hibrit_puan"] = hesapla_hibrit_kartal_puani(s, tv_veri)

    # 2. Sektör Kotalı Top 60 Kadrosunu Belirle (Tam 60 Şirket)
    # Öncelik Sırası:
    #   (1) 1. Derece Altın Standart (CFO > Net Kâr ve F-Skor >= 7) olan nitelikli şirketler
    #   (2) 60 kontenjanı doldurmak için Geniş Radar'daki en yüksek Hibrit Puanlı şirketler
    MAKS_SEKTOR_HISSE_KOTASI = 12
    mevcut_kodlar = {k for k in mevcut_pozisyonlar.keys() if k not in ["NAKIT", "TRY", "KASA", "PPF"]}
    firsat_kodlari = {s["kod"] for s in firsat_adaylari}

    nitelikli_tum_adaylar = sorted(
        firsat_adaylari,
        key=lambda x: (
            1 if (x["cfo_buyuk_net_kar"] and x["piotroski_skor"] >= 7) else 0,
            1 if x["kod"] in mevcut_kodlar else 0,
            x["hibrit_puan"],
            x["piotroski_skor"]
        ),
        reverse=True
    )

    hedef_60_kodlari = set()
    sektor_kadro_sayaci = {}

    # 1. Geçiş: Sektör başına en fazla 12 hisse kotasıyla en yüksek puanlıları seç
    for s in nitelikli_tum_adaylar:
        if len(hedef_60_kodlari) >= 60:
            break
        sek = s.get("sektor", "Sanayi")
        if sektor_kadro_sayaci.get(sek, 0) < MAKS_SEKTOR_HISSE_KOTASI:
            hedef_60_kodlari.add(s["kod"])
            sektor_kadro_sayaci[sek] = sektor_kadro_sayaci.get(sek, 0) + 1

    # 2. Geçiş (Yedek Güvence): Eğer 60 hisseye ulaşılamadıysa %20 tavanını (%1 tabanla) aşmayacak şekilde (maks 18) tamamla
    if len(hedef_60_kodlari) < 60:
        for s in nitelikli_tum_adaylar:
            if len(hedef_60_kodlari) >= 60:
                break
            if s["kod"] not in hedef_60_kodlari:
                sek = s.get("sektor", "Sanayi")
                if sektor_kadro_sayaci.get(sek, 0) < 18:
                    hedef_60_kodlari.add(s["kod"])
                    sektor_kadro_sayaci[sek] = sektor_kadro_sayaci.get(sek, 0) + 1

    # 3. Hendek ve Puan Tabanlı Katsayılar (60 Hisse İçin Uyarlanmış)
    # Korunaklı: ~%2.5 - %3.5, Kırılgan: ~%1.5 - %2.2, Hendeksiz: ~%1.0 - %1.2
    katsayi_sozluk = {"Korunaklı": 2.2, "Kırılgan Hendekli": 1.5, "Hendeksiz": 1.0}
    tavan_sozluk = {"Korunaklı": 3.5, "Kırılgan Hendekli": 2.2, "Hendeksiz": 1.2}

    sirket_havuzu = []
    for s_veri in tum_sonuclar:
        kod = s_veri["kod"]
        fiyat = mevcut_pozisyonlar.get(kod, {}).get("canli_fiyat", s_veri.get("fiyat_tl", 0.0))
        if fiyat <= 0:
            fiyat = mevcut_pozisyonlar.get(kod, {}).get("maliyet", 10.0)

        kat = s_veri.get("kategori", "Hendeksiz")
        mevcut_lot = mevcut_pozisyonlar.get(kod, {}).get("adet", 0)

        altin_kalite_mi = bool(s_veri.get("cfo_buyuk_net_kar", False) and int(s_veri.get("piotroski_skor", 0)) >= 7)
        if kod in hedef_60_kodlari:
            baz_katsayi = katsayi_sozluk.get(kat, 1.0)
            puan_carpani = max(0.7, (s_veri["hibrit_puan"] / 70.0) ** 1.35)
            # 1. Derece Altın Standart (F>=7 ve CFO>NetKâr) hisselere tam katsayı, 6/9 tamamlayıcı hisselere 0.65x katsayı ve maks %1.60 tavan
            kalite_carpani = 1.15 if altin_kalite_mi else 0.65
            katsayi = round(baz_katsayi * puan_carpani * kalite_carpani, 3)
            tavan = tavan_sozluk.get(kat, 1.5) if altin_kalite_mi else min(tavan_sozluk.get(kat, 1.5), 1.60)
            is_radar = False
            kadroda = True
        else:
            katsayi = 0.0
            tavan = 0.0
            is_radar = (kod in firsat_kodlari)
            kadroda = False

        sirket_havuzu.append({
            "kod": kod, "sektor": s_veri.get("sektor", "Sanayi / Hizmet"), "kategori": kat,
            "f_skor": s_veri.get("piotroski_skor", 6), "hibrit_puan": s_veri["hibrit_puan"],
            "katsayi": katsayi, "tavan": tavan, "fiyat": fiyat,
            "mevcut_lot": mevcut_lot,
            "ort_maliyet": mevcut_pozisyonlar.get(kod, {}).get("maliyet", fiyat),
            "ham_veri": s_veri,
            "is_radar": is_radar,
            "kadroda": kadroda
        })

    # 4. Gerçek Kasa ve Portföy Büyüklüğü Hesabı
    gercek_nakit_tl = 0.0
    gercek_kasa_bulundu = False

    if os.path.exists(PORTFOY_CSV_YOLU):
        try:
            with open(PORTFOY_CSV_YOLU, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, delimiter=";")
                for r in reader:
                    k = (r.get("Kod") or "").strip().upper()
                    if k in ["NAKIT", "TRY", "KASA", "PPF"]:
                        adet = float(str(r.get("Adet", 1)).replace(".", "").replace(",", "."))
                        maliyet = float(str(r.get("Birim_Maliyet_TL", 0)).replace(".", "").replace(",", "."))
                        gercek_nakit_tl = adet * maliyet
                        gercek_kasa_bulundu = True
                        break
        except Exception:
            pass

    toplam_hisse_mevcut_deger = sum(item["mevcut_lot"] * item["fiyat"] for item in sirket_havuzu)

    if gercek_kasa_bulundu:
        nakit_tl = round(gercek_nakit_tl, 2)
        toplam_portfoy_tl = max(toplam_hisse_mevcut_deger + nakit_tl, 1000.0)
        hisse_portfoy_tl = round(toplam_hisse_mevcut_deger, 2)
        nakit_orani = round((nakit_tl / toplam_portfoy_tl) * 100.0, 1)
        hedef_hisse_havuzu_orani = max(50.0, 100.0 - nakit_orani)
    else:
        nakit_orani = 10.0
        hedef_hisse_havuzu_orani = 90.0
        toplam_portfoy_tl = max(toplam_hisse_mevcut_deger / (hedef_hisse_havuzu_orani / 100.0), 1000.0)
        hisse_portfoy_tl = round(toplam_portfoy_tl * (hedef_hisse_havuzu_orani / 100.0), 2)
        nakit_tl = round(toplam_portfoy_tl * (nakit_orani / 100.0), 2)

    # 5 & 6. Kısıt Çözücülü (Water-Filling) Ağırlık Dağıtım ve Sektör Kalkanı Motoru
    # Eşzamanlı Garantiler:
    #   (a) Her Hedef 60 hissesi: %1.00 <= w_i <= Hendek Tavanı
    #   (b) Her sektör toplamı: <= %20.00 (SEKTOR_TAVANI)
    #   (c) Tüm Hedef 60 ağırlıkları toplamı: == hedef_hisse_havuzu_orani
    SEKTOR_TAVANI = 20.0
    TABAN_AGIRLIK = 1.0

    for item in sirket_havuzu:
        item["hedef_agirlik"] = 0.0

    kadro_60 = [x for x in sirket_havuzu if x["kadroda"]]
    if kadro_60:
        toplam_katsayi = max(sum(x["katsayi"] for x in kadro_60), 0.1)
        for x in kadro_60:
            x["_w"] = (x["katsayi"] / toplam_katsayi) * hedef_hisse_havuzu_orani

        for _ in range(40):
            # Adım A: Hisse bazlı [Taban %1.0, Hendek Tavanı] sınırlarını uygula
            for x in kadro_60:
                x["_w"] = min(x["tavan"], max(TABAN_AGIRLIK, x["_w"]))

            # Adım B: Sektör tavanını (%20.0) hisse tabanını (%1.0) delmeden uygula
            sektor_gruplari = {}
            for x in kadro_60:
                sektor_gruplari.setdefault(x["sektor"], []).append(x)

            for sek, grup in sektor_gruplari.items():
                sek_toplam = sum(x["_w"] for x in grup)
                if sek_toplam > SEKTOR_TAVANI:
                    asgari_sek_taban = len(grup) * TABAN_AGIRLIK
                    if asgari_sek_taban >= SEKTOR_TAVANI:
                        for x in grup:
                            x["_w"] = SEKTOR_TAVANI / len(grup)
                    else:
                        fazla_havuz = sum(max(0.0, x["_w"] - TABAN_AGIRLIK) for x in grup)
                        hedef_fazla = SEKTOR_TAVANI - asgari_sek_taban
                        for x in grup:
                            x["_w"] = (
                                TABAN_AGIRLIK + (max(0.0, x["_w"] - TABAN_AGIRLIK) / fazla_havuz) * hedef_fazla
                                if fazla_havuz > 0 else TABAN_AGIRLIK
                            )

            # Adım C: Toplam portföy ağırlığını hedef_hisse_havuzu_orani'na eşitle
            mevcut_toplam = sum(x["_w"] for x in kadro_60)
            fark = hedef_hisse_havuzu_orani - mevcut_toplam
            if abs(fark) < 1e-4:
                break

            sektor_toplamlari = {sek: sum(x["_w"] for x in grup) for sek, grup in sektor_gruplari.items()}
            if fark > 0:
                uygunlar = [
                    x for x in kadro_60
                    if x["_w"] < x["tavan"] - 1e-4 and sektor_toplamlari[x["sektor"]] < SEKTOR_TAVANI - 1e-4
                ]
                if not uygunlar:
                    break
                uygun_k = sum(x["katsayi"] for x in uygunlar)
                for x in uygunlar:
                    x["_w"] += fark * (x["katsayi"] / uygun_k)
            else:
                uygunlar = [x for x in kadro_60 if x["_w"] > TABAN_AGIRLIK + 1e-4]
                if not uygunlar:
                    break
                uygun_fazla = sum(x["_w"] - TABAN_AGIRLIK for x in uygunlar)
                for x in uygunlar:
                    x["_w"] += fark * ((x["_w"] - TABAN_AGIRLIK) / uygun_fazla)

        for x in kadro_60:
            x["hedef_agirlik"] = round(x["_w"], 2)

    mevcut_sektorler = {}
    for item in sirket_havuzu:
        if item["mevcut_lot"] > 0:
            sek = item["sektor"]
            pay = (item["mevcut_lot"] * item["fiyat"] / toplam_portfoy_tl) * 100.0
            mevcut_sektorler[sek] = mevcut_sektorler.get(sek, 0.0) + pay

    tablo_verileri = []
    karneler = {}

    for item in sirket_havuzu:
        kod = item["kod"]
        fiyat = item["fiyat"]
        m_lot = item["mevcut_lot"]
        m_tutar = round(m_lot * fiyat, 2)
        m_pay = round((m_tutar / toplam_portfoy_tl) * 100, 2)
        h_pay = round(item["hedef_agirlik"], 2)
        h_tutar = round(toplam_portfoy_tl * (h_pay / 100.0), 2)
        h_lot = int(round(h_tutar / fiyat)) if fiyat > 0 else 0
        
        # Matematiksel Çift Tolerans Filtresi
        aksiyon, fark_lot, islem_tutari = toleransli_aksiyon_hesapla(
            mevcut_lot=m_lot,
            hedef_lot=h_lot,
            guncel_fiyat=fiyat,
            toplam_portfoy_tl=toplam_portfoy_tl
        )
        kz_yuzde = round(((fiyat / item["ort_maliyet"]) - 1) * 100, 2) if item["ort_maliyet"] > 0 else 0.0

        # TABLO İÇİN 6 KÜMELİ SEKTÖR DUYARLI ROE ROZETİ (Modal ile %100 Senkronize)
        r_val = float(item["ham_veri"].get("roe", 0.0))
        min_norm, ust_norm, _ = sektor_roe_normu_getir(item["sektor"])

        if r_val < 5.0:
            roe_rozet = f"<span class='badge-fail'>%{r_val:.1f} 🚨</span>"
        elif r_val < min_norm:
            roe_rozet = f"<span class='badge-warn'>%{r_val:.1f} ⚠️</span>"
        elif r_val <= ust_norm:
            roe_rozet = f"<span class='badge-ok'>%{r_val:.1f} 🟢</span>"
        else:
            roe_rozet = f"<span class='badge-ok'>%{r_val:.1f} 🚀</span>"

        item["roe_rozet"] = roe_rozet

        tablo_verileri.append({
            "Kod": kod, "Sektör": item["sektor"], "Hendek": item["kategori"], "F_Skor": item["f_skor"],
            "Hibrit_Puan": item["hibrit_puan"],
            "Mevcut_Lot": m_lot, "Fiyat": fiyat, "Mevcut_Tutar": m_tutar, "Mevcut_Pay": m_pay,
            "Hedef_Pay": h_pay, "Hedef_Tutar": h_tutar, "Hedef_Lot": h_lot, "Fark_Lot": fark_lot,
            "Aksiyon": aksiyon, "KZ_Yuzde": kz_yuzde, "Katsayi": item["katsayi"], "Islem_Tutari": islem_tutari,
            "Ort_Maliyet": item["ort_maliyet"],
            "kadroda": item["kadroda"],
            "is_radar": item["is_radar"],
            "ROE_Rozet": item["roe_rozet"]
        })

        poz_bilgi = {"agirlik_yuzde": m_pay, "kademe": mevcut_pozisyonlar.get(kod, {}).get("kademe", "1/3 Başlangıç")}
        karneler[kod] = uret_serkan_unal_karnesi(item["ham_veri"], poz_bilgi)

    # --- HTML İÇİN 3 KATMANLI SIRALAMA MANTIĞI ---
    tasinan_hisseler = sorted([x for x in tablo_verileri if x["Mevcut_Lot"] > 0], key=lambda x: x["Mevcut_Tutar"], reverse=True)
    radar_hisseleri = sorted(
        [x for x in tablo_verileri if x["Mevcut_Lot"] == 0 and (x["kadroda"] or x["is_radar"])],
        key=lambda x: (1 if x["kadroda"] else 0, x["Hedef_Pay"], x["Hibrit_Puan"], x["F_Skor"]),
        reverse=True
    )
    diger_hisseler = sorted(
        [x for x in tablo_verileri if x["Mevcut_Lot"] == 0 and not x["kadroda"] and not x["is_radar"]],
        key=lambda x: (x["Hibrit_Puan"], x["F_Skor"]),
        reverse=True
    )
    sirali_liste = tasinan_hisseler + radar_hisseleri + diger_hisseler
    # ---------------------------------------------

    sheets_satir_matrisi = [[
        "Hisse Kodu", "Sektör", "Hendek", "F-Skor", "Katsayı",
        "Mevcut Lot", "Maliyet (₺)", "Fiyat (₺)", "Mevcut Tutar (₺)",
        "K/Z %", "Mevcut Pay %", "Hedef Pay %", "Hedef Tutar (₺)",
        "Hedef Lot", "Pazartesi Fark Lot", "İşlem Tutarı (₺)", "Aksiyon"
    ]]

    for x in sirali_liste:
        sheets_satir_matrisi.append([
            x["Kod"],
            x["Sektör"],
            x["Hendek"],
            f"{x['F_Skor']}/9",
            round(x["Katsayi"], 1),
            x["Mevcut_Lot"],
            round(x["Ort_Maliyet"], 2),
            round(x["Fiyat"], 2),
            round(x["Mevcut_Tutar"], 2),
            f"%{x['KZ_Yuzde']}",
            f"%{x['Mevcut_Pay']}",
            f"%{x['Hedef_Pay']}",
            round(x["Hedef_Tutar"], 2),
            x["Hedef_Lot"],
            x["Fark_Lot"],
            x["Islem_Tutari"],
            x["Aksiyon"]
        ])

    sektor_paneli_html = uret_sektor_paneli_html(mevcut_sektorler, nakit_orani, nakit_tl, gercek_kasa_bulundu)
    uret_portfoy_kalkani_html(sirali_liste, karneler, toplam_portfoy_tl, hisse_portfoy_tl, nakit_tl, len(sirket_havuzu), sektor_paneli_html)

    # ==============================================================================
    # FAZ 9: ROTASYON VE ALARM MOTORU (HAFIZA KARŞILAŞTIRMASI)
    # ==============================================================================
    ONCEKI_TARAMA_YOLU = os.path.join(PORTFOY_DIR, "onceki_tarama.json")
    
    # 1. Güncel Hedef 60 Kadrosunu Çıkar
    yeni_kadro_kodlari = {x["Kod"] for x in tablo_verileri if x["kadroda"]}
    
    # 2. Önceki Tarama Hafızasını Yükle
    onceki_kadro = set()
    if os.path.exists(ONCEKI_TARAMA_YOLU):
        try:
            with open(ONCEKI_TARAMA_YOLU, "r", encoding="utf-8") as f:
                eski_veri = json.load(f)
                onceki_kadro = set(eski_veri.get("hedef_60_kadrosu", []))
        except Exception:
            pass

    # 3. Kümelerle Farkı (Delta) Bul
    yeni_girenler = yeni_kadro_kodlari - onceki_kadro if onceki_kadro else set()
    cikanlar = onceki_kadro - yeni_kadro_kodlari if onceki_kadro else set()

    rotasyon_alarmi = None
    if yeni_girenler or cikanlar:
        print("\n🦅 KARTAL ROTASYON ALARMI:")
        if yeni_girenler: print(f"  🟢 KADROYA YENİ GİRENLER: {', '.join(yeni_girenler)}")
        if cikanlar: print(f"  🔴 KADRODAN DÜŞENLER: {', '.join(cikanlar)}")

        # === DOĞRUDAN TELEGRAM ALARM TETİKLEYİCİSİ ===
        import os
        import requests
        # BASE_DIR'i garanti altına alıyoruz (Zaten üstte varsa bile burada bir kez daha netleştirmek zarar vermez)
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    
        # Şifreleri dışarıdan okuyan güvenli yapı:
        try:
            with open(os.path.join(BASE_DIR, "token.txt"), "r") as f:
                TELEGRAM_BOT_TOKEN = f.read().strip()
            with open(os.path.join(BASE_DIR, "chat_id.txt"), "r") as f:
                TELEGRAM_CHAT_ID = f.read().strip()
            
            # Eğer dosyalar var ama içi boşsa hata fırlat
            if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
                raise ValueError("token.txt veya chat_id.txt içi boş!")
            
            mesaj = "🦅 *KARTAL PORTFÖY ROTASYON ALARMI*\n\n"
            if yeni_girenler:
                mesaj += "🟢 *Kadroya Yeni Girenler:*\n" + ", ".join(yeni_girenler) + "\n\n"
            if cikanlar:
                mesaj += "🔴 *Kadrodan Düşenler:*\n" + ", ".join(cikanlar) + "\n\n"
            mesaj += "🔗 [KARTAL Panele Git](https://hzkbb75.github.io/kartal-panel/)"

            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": "Markdown"}

            try:
                requests.post(url, json=payload, timeout=10)
                print("  [Telegram] Rotasyon alarmı telefonunuza başarıyla gönderildi.")
            except Exception as e:
                print(f"  [Telegram] Alarm gönderilemedi: {e}")
            
        except Exception as e:
            print(f"  [HATA] Şifreler okunamadı veya Telegram'a bağlanılamadı: {e}")

    # 4. Yeni Durumu Hafızaya Yaz (Bir sonraki çalışma için)
    yeni_tarama_verisi = {
        "tarih": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "hedef_60_kadrosu": list(yeni_kadro_kodlari)
    }
    with open(ONCEKI_TARAMA_YOLU, "w", encoding="utf-8") as f:
        json.dump(yeni_tarama_verisi, f, ensure_ascii=False, indent=2)

    df_masa = pd.DataFrame(tablo_verileri)
    df_masa.to_csv(DINAMIK_MASA_CSV_YOLU, sep=";", index=False, encoding="utf-8-sig")

    if SHEETS_APPS_SCRIPT_URL.strip():
        try:
            payload = {
                "ozet": {
                    "toplam_portfoy_tl": round(toplam_portfoy_tl, 2),
                    "toplam_hisse_tl": round(hisse_portfoy_tl, 2),
                    "nakit_tl": round(nakit_tl, 2),
                    "nakit_agirlik": round(nakit_orani, 1)
                },
                "satirlar": sheets_satir_matrisi
            }
            res = requests.post(SHEETS_APPS_SCRIPT_URL, json=payload, timeout=25)
            if res.status_code == 200:
                print(f"  [Google Sheets] {len(sheets_satir_matrisi)} satır Portfoy_Kalkani sayfasına canlı senkronize edildi!")
            else:
                print(f"  [Google Sheets] Yanıt kodu: {res.status_code}")
        except Exception as e:
            print(f"  [Bilgi] Google Sheets senkronizasyon uyarısı: {e}")

    sure = round(time.time() - baslangic, 2)
    print("=" * 68)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] İŞLEM TAMAMLANDI! (Süre: {sure} saniye)")
    print(f"  Toplam Portföy: {toplam_portfoy_tl:,.0f} TL")
    if gercek_kasa_bulundu:
        print(f"  Gerçek Kasa Bakiyesi (portfoyum.csv): {nakit_tl:,.0f} TL (%{nakit_orani:.1f})")
    print(f"  Sektör Tavanı Kalkanı: Maksimum %20 devrede")
    print("=" * 68)


def github_guncelle():
    print("\n🚀 Güncel panolar GitHub Pages'e aktarılıyor...")
    try:
        # 1. Hassas şifre/anahtar dosyalarının Git'e eklenmesini (.gitignore ile) kesin olarak engelle
        gizli_dosyalar = ["webhook_mail.txt", "token.txt", "chat_id.txt", ".env", "*.key"]
        gitignore_mevcut = set()
        if os.path.exists(".gitignore"):
            with open(".gitignore", "r", encoding="utf-8") as gf:
                gitignore_mevcut = {line.strip() for line in gf if line.strip()}
        eksik_gizliler = [d for d in gizli_dosyalar if d not in gitignore_mevcut]
        if eksik_gizliler:
            with open(".gitignore", "a", encoding="utf-8") as gf:
                gf.write("\n" + "\n".join(eksik_gizliler) + "\n")

        # 2. Önceki commit'te (örn. webhook_mail.txt yüzünden) takılı kalan push varsa origin/main'e yumuşak sıfırla
        subprocess.run(["git", "reset", "--soft", "origin/main"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(
            ["git", "rm", "--cached", "--ignore-unmatch", "webhook_mail.txt", "token.txt", "chat_id.txt", "hektor_kutuphane/PORTFOY/index.html"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        # 3. Temiz çalışma ağacını ekle, commit et ve GitHub Pages'e gönder
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-m", "Oto-guncelleme: index.html ve panolar güncellendi"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("✅ GitHub Pages başarıyla güncellendi (index.html yayında)!")
    except Exception as e:
        print(f"⚠️ Git güncelleme atlandı: {e}")

github_guncelle()