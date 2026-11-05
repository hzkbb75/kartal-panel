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

# 2. kap_indirici.py Tarafından Üretilen KAP & Teknik Veri Ambarını Yükle
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
                print(f"  [Bilgi] 'kap_ve_teknik.json' başarıyla hafızaya alındı ({yol}).")
                break
        except Exception as e:
            print(f"  [Uyarı] kap_ve_teknik.json okunamadı: {e}")

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
    "ANSGR", "AKGRT", "RAYSG", "ISMEN", "INFO", "OSMEN", "GEDIK", "GLCVY", "OYYAT", "DSTKF"
}
SADECE_ENDEKS_PARA_HARIC = {"BIST", "XU100", "XU030", "TRY", "USD", "EUR", "XU050", "XUMKR", "XUTUM", "XUSIN", "XBANK", "NAKIT", "KASA", "PPF"}
CEKIRDEK_KORUNAKLI_SIRKETLER = {"ASELS", "BIMAS", "KCHOL", "CCOLA", "FROTO", "MGROS", "AGESA", "ENKAI", "THYAO", "TUPRS"}


def tr_norm(metin: str) -> str:
    if not metin:
        return ""
    ceviri = str.maketrans("İIŞĞÜÖÇıişğüöç", "iisguociisguoc")
    return re.sub(r"\s+", " ", metin.translate(ceviri).lower()).strip()


def satir_bul(satirlar: list, aranan_kelimeler: list, mod: str = "ilk") -> tuple:
    norm_arananlar = [tr_norm(k) for k in aranan_kelimeler]
    toplam_c, toplam_o = 0.0, 0.0
    bulundu = False
    for satir in satirlar:
        ad = satir[0]
        v1 = float(satir[1] or 0.0)
        v2 = float(satir[2] or 0.0)
        for aranan in norm_arananlar:
            if aranan in ad or ad == aranan:
                if v1 != 0.0 or v2 != 0.0:
                    if mod == "ilk":
                        return v1, v2
                    toplam_c += v1
                    toplam_o += v2
                    bulundu = True
                    break
    if mod == "topla" and bulundu:
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
        roe = float(roe) / 100.0 if roe > 1 else float(roe) 
        
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


def hesapla_donem_dipnot_metrikleri(satirlar_cari: list, satirlar_onceki: list = None) -> dict:
    onceki = satirlar_onceki if satirlar_onceki else satirlar_cari
    satis_c = satir_bul(satirlar_cari, ["hasilat", "satis gelirleri", "faiz gelirleri"])[0]
    satis_o = satir_bul(onceki, ["hasilat", "satis gelirleri", "faiz gelirleri"])[0]
    efk_c = satir_bul(satirlar_cari, ["esas faaliyet kari", "faaliyet kari"])[0]
    net_c = satir_bul(satirlar_cari, ["donem net kar", "donem kari", "ana ortaklik paylari"])[0]
    cfo_c = satir_bul(satirlar_cari, ["isletme faaliyetlerinden", "faaliyetlerden nakit", "kaynaklanan net nakit"])[0]

    varlik_c = max(satir_bul(satirlar_cari, ["toplam varliklar", "toplam aktifler", "varliklar"])[0], 1.0)
    ozk_c = max(satir_bul(satirlar_cari, ["ana ortakliga ait ozkaynaklar", "toplam ozkaynaklar", "ozkaynaklar"])[0], 1.0)
    fin_borc_c = satir_bul(satirlar_cari, ["finansal borclar"], mod="topla")[0]
    fin_borc_o = satir_bul(onceki, ["finansal borclar"], mod="topla")[0]
    fin_gider_c = abs(satir_bul(satirlar_cari, ["finansman giderleri", "esas faaliyet disi finansal giderler"])[0])

    alacak_c = satir_bul(satirlar_cari, ["ticari alacaklar"])[0]
    alacak_o = satir_bul(onceki, ["ticari alacaklar"])[0]
    iliskili_alacak_c = satir_bul(satirlar_cari, ["iliskili taraflardan diğer alacaklar", "iliskili taraflardan ticari olmayan alacaklar"], mod="topla")[0]
    modv_c = satir_bul(satirlar_cari, ["maddi olmayan duran varliklar"])[0]
    modv_o = satir_bul(onceki, ["maddi olmayan duran varliklar"])[0]
    serefiye_c = satir_bul(satirlar_cari, ["serefiye"])[0]
    kagit_kar = max(0.0, satir_bul(satirlar_cari, ["yatirim faaliyetlerinden gelirler"])[0]) + max(0.0, satir_bul(satirlar_cari, ["ertelenmis vergi geliri"])[0])
    karsiliklar_c = satir_bul(satirlar_cari, ["kisa vadeli karsiliklar", "uzun vadeli karsiliklar", "calisanlara saglanan"], mod="topla")[0]

    ort_borc = max((fin_borc_c + fin_borc_o) / 2.0, 1.0)
    efm_yuzde = round((fin_gider_c / ort_borc) * 100, 1) if fin_borc_c > 0 else 0.0
    icr_kati = round(efk_c / max(fin_gider_c, 1.0), 2) if fin_gider_c > 0 else 99.0

    tahakkuk_orani = round(((net_c - cfo_c) / varlik_c) * 100, 2)
    aga_orani = round((max(0.0, modv_c - modv_o) / max(efk_c, 1.0)) * 100, 1) if efk_c > 0 else 0.0
    
    if efk_c > 0:
        ykp_orani = min(100.0, round((kagit_kar / efk_c) * 100, 1))
    else:
        ykp_orani = min(100.0, round((kagit_kar / max(varlik_c, 1.0)) * 100, 1))

    dso_gun = round(365.0 * (alacak_c / max(satis_c, 1.0)), 1) if satis_c > 0 else 0.0
    alacak_buyume = ((alacak_c / max(alacak_o, 1.0)) - 1.0) * 100 if alacak_o > 0 else 0.0
    satis_buyume = ((satis_c / max(satis_o, 1.0)) - 1.0) * 100 if satis_o > 0 else 0.0
    acm_makasi = round(alacak_buyume - satis_buyume, 1)

    serefiye_ozk_orani = round((serefiye_c / ozk_c) * 100, 1)
    karsilik_ozk_orani = round((karsiliklar_c / ozk_c) * 100, 1)
    iliskili_orani = round((iliskili_alacak_c / varlik_c) * 100, 2)

    ceza = 0
    uyarilar = []
    if iliskili_orani > 3.0: ceza += 20; uyarilar.append(f"🔴 İlişkili taraflardan ticari olmayan alacaklar aktifin %{iliskili_orani}'ine ulaştı.")
    if tahakkuk_orani > 5.0: ceza += 20; uyarilar.append(f"🔴 Tahakkuk anomalisi (%{tahakkuk_orani}): Kâr nakde dönüşmüyor.")
    if ykp_orani > 35.0: ceza += 15; uyarilar.append(f"🟡 Kağıt üzeri kâr payı yüksek (%{ykp_orani}).")
    if acm_makasi > 15.0: ceza += 15; uyarilar.append(f"🟡 Alacak-Ciro makası +%{acm_makasi} açıldı (DSO: {dso_gun} gün).")
    if icr_kati < 1.5 and fin_gider_c > 0: ceza += 15; uyarilar.append(f"🔴 Faiz karşılama oranı {icr_kati}x (ICR zayıf).")

    if not uyarilar: uyarilar.append("🟢 Muhasebe makyajı veya anomali tespit edilmedi.")

    return {
        "efm_yuzde": efm_yuzde, "icr_kati": icr_kati, "tahakkuk_orani": tahakkuk_orani,
        "aga_orani": aga_orani, "ykp_orani": ykp_orani, "dso_gun": dso_gun, "acm_makasi": acm_makasi,
        "serefiye_ozk_orani": serefiye_ozk_orani, "karsilik_ozk_orani": karsilik_ozk_orani,
        "iliskili_orani": iliskili_orani, "dts_skor": min(100, ceza), "uyarilar": uyarilar,
        "pdf_dipnot_notlari": {}
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
        onceki_satirlar = cok_donem[sirali_anahtarlar[idx - 1]] if idx > 0 else cok_donem[d_adi]
        cari_satirlar = [(s[0], s[1], s[1]) for s in cok_donem[d_adi]]
        onceki_s = [(s[0], s[1], s[1]) for s in onceki_satirlar]

        hesaplanan = hesapla_donem_dipnot_metrikleri(cari_satirlar, onceki_s)

        if d_adi in mevcut_arsiv.get("donemler", {}):
            eski = mevcut_arsiv["donemler"][d_adi]
            if eski.get("pdf_dipnot_notlari"):
                hesaplanan["pdf_dipnot_notlari"] = eski["pdf_dipnot_notlari"]
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

    ibre_pozisyonu = 50 + (iskonto_orani)
    ibre_pozisyonu = max(5, min(95, ibre_pozisyonu))

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
# 2. ŞİRKET ELEME VE ANALİZ MOTORU
# ==============================================================================
def hesapla_hibrit_kartal_puani(sirket_verisi: dict, teknik_veri: dict = None) -> tuple:
    """
    Temel Analiz (%60) + 60G Teknik Kanal Uzaklığı (%40) Hibrit Puanı (0 - 100).
    """
    # 1. Temel Puan (Maks 60 Puan)
    f_skor = int(sirket_verisi.get("piotroski_skor", 5))
    temel_puan = (f_skor / 9.0) * 30.0  # Piotroski'den maks 30 puan
    
    roe = float(sirket_verisi.get("roe", 0.0))
    if roe >= 40: temel_puan += 15.0
    elif roe >= 25: temel_puan += 10.0
    elif roe >= 15: temel_puan += 5.0
    
    nb_ozk = float(sirket_verisi.get("net_borc_ozkaynak", 1.0))
    if nb_ozk <= 0.1: temel_puan += 15.0
    elif nb_ozk <= 0.6: temel_puan += 10.0
    elif nb_ozk <= 1.2: temel_puan += 5.0
    
    # 2. Teknik Puan (Maks 40 Puan)
    teknik_puan = 20.0  # Teknik veri yoksa nötr varsayılan
    if teknik_veri:
        kanal_konumu = float(teknik_veri.get("kanal_konumu", 50.0))
        # Desteğe ne kadar yakınsa puan o kadar yüksek
        if kanal_konumu <= 20: teknik_puan = 40.0
        elif kanal_konumu <= 40: teknik_puan = 30.0
        elif kanal_konumu <= 60: teknik_puan = 20.0
        elif kanal_konumu <= 80: teknik_puan = 10.0
        else: teknik_puan = 0.0
        
    toplam_skor = round(temel_puan + teknik_puan, 1)
    return toplam_skor

def hesapla_hibrit_kartal_puani(s: dict, teknik_veri: dict = None) -> float:
    """
    Sektör Duyarlı Serkan Ünal Hibrit Puanı: Temel Kalite (%60) + 60G Destek (%40)
    """
    f_skor = int(s.get("piotroski_skor", 5))
    temel = (f_skor / 9.0) * 25.0  # F-skor'dan maks 25 puan
    
    # Sektör Duyarlı ROE Puanlaması (Maks 20 Puan)
    roe = float(s.get("roe", 0.0))
    sek_l = tr_norm(s.get("sektor", ""))
    
    if any(x in sek_l for x in ["perakende", "gida", "icecek"]):
        min_roe = 35.0
    elif any(x in sek_l for x in ["yazilim", "teknoloji", "savunma", "telekom"]):
        min_roe = 30.0
    elif any(x in sek_l for x in ["banka", "sigorta", "faktoring", "finans"]):
        min_roe = 22.0
    elif any(x in sek_l for x in ["demir", "celik", "petrol", "kimya", "cimento", "otomotiv"]):
        min_roe = 15.0
    else:
        min_roe = 10.0

    if roe < 5.0:
        temel -= 10.0  # Reel sermaye erimesine ceza puanı
    elif roe >= min_roe * 1.3:
        temel += 20.0  # Sektörün belirgin üzerinde üstün performans
    elif roe >= min_roe:
        temel += 15.0  # Sektör normuyla uyumlu
    else:
        temel += 5.0   # Sektörün altında

    # Borçsuzluk / Bilanço Puanı (Maks 15 Puan)
    nb_ozk = float(s.get("net_borc_ozkaynak", 1.0))
    if nb_ozk <= 0.1: temel += 15.0
    elif nb_ozk <= 0.6: temel += 10.0
    elif nb_ozk <= 1.2: temel += 5.0

    # 60G Teknik Destek Puanı (Maks 40 Puan)
    teknik = 20.0
    if teknik_veri:
        konum = float(teknik_veri.get("kanal_konumu", 50.0))
        if konum <= 25.0: teknik = 40.0
        elif konum <= 45.0: teknik = 30.0
        elif konum <= 65.0: teknik = 20.0
        elif konum <= 85.0: teknik = 10.0
        else: teknik = 0.0

    return round(temel + teknik, 1)

def analiz_et_sirket(kod: str) -> dict:
    sirket_kaydi = YEREL_MALI_VERITABANI.get(kod)
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

    satis_c, satis_o = satir_bul(satirlar, ["hasilat", "satis gelirleri", "faaliyet gelirleri", "faiz gelirleri"])
    brut_c, brut_o = satir_bul(satirlar, ["brut kar", "net faiz geliri"])
    efk_c, efk_o = satir_bul(satirlar, ["esas faaliyet kari", "faaliyet kari"])
    net_c, net_o = satir_bul(satirlar, ["donem net kar", "donem kari", "ana ortaklik paylari"])
    cfo_c, cfo_o = satir_bul(satirlar, ["isletme faaliyetlerinden", "faaliyetlerden nakit", "kaynaklanan net nakit"])

    varlik_c, varlik_o = satir_bul(satirlar, ["toplam varliklar", "toplam aktifler", "varliklar", "aktifler"])
    ozk_c, ozk_o = satir_bul(satirlar, ["ana ortakliga ait ozkaynaklar", "toplam ozkaynaklar", "ozkaynaklar"])
    nakit_c, nakit_o = satir_bul(satirlar, ["nakit ve nakit benzerleri", "nakit degerler"])
    fin_borc_c, fin_borc_o = satir_bul(satirlar, ["finansal borclar"], mod="topla")
    alacak_c, alacak_o = satir_bul(satirlar, ["ticari alacaklar"], mod="ilk")
    iliskili_alacak_c, _ = satir_bul(satirlar, ["iliskili taraflardan diğer alacaklar", "iliskili taraflardan ticari olmayan alacaklar"], mod="topla")
    stok_c, stok_o = satir_bul(satirlar, ["stoklar"], mod="ilk")
    mdv_c, mdv_o = satir_bul(satirlar, ["maddi duran varliklar"], mod="ilk")
    ertelenmis_c, ertelenmis_o = satir_bul(satirlar, ["ertelenmis gelirler", "alinan siparis avanslari"], mod="topla")
    donen_c, donen_o = satir_bul(satirlar, ["donen varliklar"], mod="ilk")
    kv_c, kv_o = satir_bul(satirlar, ["kisa vadeli yukumlulukler"], mod="ilk")
    uv_c, uv_o = satir_bul(satirlar, ["uzun vadeli yukumlulukler"], mod="ilk")

    iliskili_oran = round((iliskili_alacak_c / max(varlik_c, 1.0)) * 100, 2)
    net_borc_c = max(fin_borc_c - nakit_c, 0.0)
    net_borc_o = max(fin_borc_o - nakit_o, 0.0)
    net_borc_ozkaynak = round(net_borc_c / max(ozk_c, 1.0), 2)
    kv_borc_oran = round((kv_c / max(kv_c + uv_c, 1.0)) * 100, 1)

    kirmizi_cizgi = (halka_aciklik > 70.0) or (iliskili_oran > 5.0) or (not banka_mi and (ozk_c <= 0 or (net_borc_ozkaynak > 2.0 and efk_c < 0) or (cfo_c < 0 and cfo_o < 0)))
    bolum1_gecti_mi = not kirmizi_cizgi

    efk_marj_c = (efk_c / max(satis_c, 1.0))
    yatirilan_sermaye = max(ozk_c + net_borc_c, 1.0)
    roic_tahmini = efk_c / yatirilan_sermaye

    if kod in CEKIRDEK_KORUNAKLI_SIRKETLER or (efk_marj_c >= 0.14 and roic_tahmini >= 0.15 and cfo_c > 0):
        kategori, tavan = "Korunaklı", 10.0
    elif efk_marj_c >= 0.07 and cfo_c > 0:
        kategori, tavan = "Kırılgan Hendekli", 5.0
    else:
        kategori, tavan = "Hendeksiz", 3.0

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
    ziraat = BIST_ANALIZ_TABANI.get(kod, {})
    if ziraat:
        sektor = ziraat.get("sektor", sirket_kaydi.get("sektor", "Sanayi"))
        sirket_adi = ziraat.get("sirket_adi", sirket_kaydi.get("sirket_adi", kod))
        if ziraat.get("fiyat", 0) > 0: fiyat = ziraat["fiyat"]
        if ziraat.get("fk", 0) > 0: fk = ziraat["fk"]
        if ziraat.get("fd_favok", 0) > 0: fd_favok = ziraat["fd_favok"]
        if ziraat.get("roe", 0) != 0: roe_hesap = ziraat["roe"]
        if ziraat.get("net_kar_son4c_mn", 0) != 0: net_c = ziraat["net_kar_son4c_mn"]

    return {
        "kod": kod, "sirket_adi": sirket_kaydi.get("sirket_adi", kod), "sektor": sirket_kaydi.get("sektor", "Sanayi"),
        "kategori": kategori, "tavan_agirlik": tavan, "halka_aciklik": halka_aciklik, "net_borc_ozkaynak": net_borc_ozkaynak,
        "bolum1_gecti_mi": bolum1_gecti_mi, "fiyat_tl": fiyat, "cfo_buyuk_net_kar": cfo_kaliteli, "piotroski_skor": f_skor,
        "iliskili_oran": iliskili_oran, "kv_borc_oran": kv_borc_oran, "cfo_milyon_tl": round(cfo_c, 1), "net_kar_milyon_tl": round(net_c, 1),
        "net_kar_onceki_milyon_tl": round(net_o, 1), "satis_milyon_tl": round(satis_c, 1), "satis_onceki_milyon_tl": round(satis_o, 1),
        "efk_milyon_tl": round(efk_c, 1), "efk_onceki_milyon_tl": round(efk_o, 1), "ozk_c": round(ozk_c, 1), "ozk_o": round(ozk_o, 1),
        "varlik_c": round(varlik_c, 1), "varlik_o": round(varlik_o, 1), "mdv_c": round(mdv_c, 1), "mdv_o": round(mdv_o, 1),
        "ertelenmis_c": round(ertelenmis_c, 1), "ertelenmis_o": round(ertelenmis_o, 1), "stok_c": round(stok_c, 1), "stok_o": round(stok_o, 1),
        "alacak_c": round(alacak_c, 1), "alacak_o": round(alacak_o, 1), "net_borc_c": round(net_borc_c, 1), "net_borc_o": round(net_borc_o, 1),
        "fin_borc_c": round(fin_borc_c, 1), "nakit_c": round(nakit_c, 1),
        "piyasa_degeri_milyar": pd_milyar, "fk": fk, "fd_favok": fd_favok, "roe": roe_hesap
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

    banka_mi = (
        (kod in BANKA_VE_FINANS_KODLARI)
        or ("banka" in tr_norm(sektor))
        or ("sigorta" in tr_norm(sektor))
        or ("emeklilik" in tr_norm(sektor))
        or ("faktoring" in tr_norm(sektor))
    )

    if pd_milyar >= 15.0 and 10.0 <= halka_aciklik <= 70.0:
        tahta_derinligi = {
            "sinif": "Kurumsal / Likit 250 Tahta",
            "rozet_renk": "badge-ok",
            "aciklama": f"Piyasa değeri {pd_milyar:,.1f} milyar TL, halka açıklık %{halka_aciklik:.1f} — kurumsal derinliği yüksek tahta."
        }
    elif pd_milyar >= 2.5 and 5.0 <= halka_aciklik <= 70.0:
        tahta_derinligi = {
            "sinif": "Orta Ölçek / Standart Tahta",
            "rozet_renk": "badge-ok",
            "aciklama": f"Piyasa değeri {pd_milyar:,.1f} milyar TL, halka açıklık %{halka_aciklik:.1f} — standart likidite koşullarına uygun."
        }
    else:
        tahta_derinligi = {
            "sinif": "Sığ Tahta / Likidite Uyarısı",
            "rozet_renk": "badge-warn",
            "aciklama": f"Piyasa değeri {pd_milyar:,.1f} milyar TL, halka açıklık %{halka_aciklik:.1f} — kademe derinliği sığ olabilir, kademeli emir şart."
        }

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
        if 0 < fk <= 6.0 and roe >= 10.0:
            en_guclu = {
                "baslik": "Güçlü ROE & Çarpan İskontosu",
                "detay": f"%{roe:.1f} özkaynak kârlılığı ve {fk:.1f}x F/K çarpanıyla tarihsel iskonto bölgesinde."
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
    else:
        en_guclu = {
            "baslik": f"{kategori} Yapı & Nakit Akışı ({f_skor}/9)",
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
    elif not banka_mi and ((kv_borc_orani >= 75.0 and net_borc_ozk >= 0.50) or (0 < icr < 1.5) or net_borc_ozk >= 1.20):
        kritik_risk = {
            "kod": "KISA_VADELI_BORC_VE_FAIZ_BASKISI",
            "baslik": "Kısa Vadeli Borç Çevirme & Finansman Yükü",
            "detay": f"Borcun %{kv_borc_orani:.0f}'i kısa vadeli; Net Borç/ÖZK {net_borc_ozk:.2f}x ve faiz karşılama (ICR) {icr:.2f}x sınırında.",
            "iptal_tetikleyicisi": (
                f"ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: Faiz karşılama oranı (ICR) 1.25x'in altına sarkar veya "
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
    elif marj_degisim_qoq <= -2.0 or efk_marji < 0:
        kritik_risk = {
            "kod": "OPERASYONEL_MARJ_EROZYONU",
            "baslik": "Operasyonel Marj Daralması & Maliyet Baskısı",
            "detay": f"Esas faaliyet marjı %{efk_marji:.1f} seviyesinde (Önceki döneme göre değişim: {marj_degisim_qoq:+.1f} puan).",
            "iptal_tetikleyicisi": (
                f"ŞARTA BAĞLI İPTAL TETİKLEYİCİSİ: Esas faaliyet kâr marjı gelecek çeyrekte de toparlanamaz ve "
                f"%{max(3.0, efk_marji - 1.5):.1f} eşiğinin altında kalırsa büyüme hikayesi çürümüş sayılır, pozisyon azaltılır."
            )
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
        "oz_turkce_seridi": oz_turkce_seridi,
        "tahta_derinligi": tahta_derinligi
    }


def uret_serkan_unal_karnesi(s: dict, pozisyon: dict = None) -> dict:
    kod = s["kod"]
    sirket_adi = s.get("sirket_adi", kod)
    sektor = s.get("sektor", "Sanayi")
    # Sektör "DİĞER" ise yerel kayıttan gerçek sektörü al
    ziraat = BIST_ANALIZ_TABANI.get(kod, {})
    if ziraat and ziraat.get("sektor") and ziraat.get("sektor") != "DİĞER":
        sektor = ziraat.get("sektor")
    elif s.get("sektor") and s.get("sektor") != "DİĞER":
        sektor = s.get("sektor")
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
    fd_satis = round((pd_milyar * 1000 + net_borc_c) / max(satis_c, 1.0), 2) if satis_c > 0 else 0.0
    roe = float(s.get("roe", round((net_c / max(ozk_c, 1.0)) * 100, 1)))

    satis_buyume = round(((satis_c / max(satis_o, 1.0)) - 1) * 100, 1) if satis_o > 0 else 0.0
    net_kar_buyume = round(((net_c / max(abs(net_o), 1.0)) - 1) * 100, 1) if net_o > 0 else 0.0
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

    # SENTETİK P-F/K MOTORU
    avans_buyume = round(((ertelenmis_c / max(abs(ertelenmis_o), 1.0)) - 1) * 100, 1) if ertelenmis_o > 0 else 0.0
    satis_ivmesi = max(-0.50, min(1.00, satis_buyume / 100.0))
    marj_ivmesi = max(-0.30, min(0.30, marj_fark_puan / 100.0))
    avans_ivmesi = max(-0.30, min(0.50, (avans_buyume / 100.0) * 0.35))
    toplam_ivme = max(-0.70, min(1.50, satis_ivmesi + marj_ivmesi + avans_ivmesi))

    if fk > 0 and (1.0 + toplam_ivme) > 0.15:
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

    konum_yuzde = int(min(95, max(5, (fk / 20.0) * 100))) if fk > 0 else 5
    marj_durum = "GÜÇLÜ GÜVENLİK MARJI (İSKONTOLU)" if (0 < fk < 6.0) else ("DENGELİ / MAKUL DEĞERLEME" if fk <= 16.0 else "DOYUM NOKTASI / PAHALI")
    marj_renk = "badge-ok" if (0 < fk < 6.0) else ("badge-warn" if fk <= 16.0 else "badge-fail")

    degerleme_bandi = {
        "fk": fk, "pfk": pfk, "alt_fk": 6.0, "medyan_fk": 11.0, "ust_fk": 16.0,
        "marj_durum": marj_durum, "marj_renk": marj_renk, "konum_yuzde": konum_yuzde
    }
    
    # HİBRİT DEĞERLEME MODÜLÜ (ZİRAAT GÜVENCELİ)
    ziraat = BIST_ANALIZ_TABANI.get(kod, {})
    if ziraat:
        sektor = ziraat.get("sektor", sektor)
        if ziraat.get("fk", 0) > 0: fk = ziraat["fk"]
        if ziraat.get("fd_favok", 0) > 0: fd_favok = ziraat["fd_favok"]
        if ziraat.get("roe", 0) != 0: roe = ziraat["roe"]
        if ziraat.get("net_kar_son4c_mn", 0) != 0: net_c = ziraat["net_kar_son4c_mn"]

    # Hatasız Hisse Başına Kâr (HBK) ve Defter Değeri (DD) Türetimi
    sirket_hbk = round(fiyat / fk, 2) if fk > 0 else 0.0
    pddd = ziraat.get("pddd", 0.0) if ziraat else 0.0
    sirket_dd = round(fiyat / pddd, 2) if pddd > 0 else (ozk_c / max((pd_milyar * 1000) / max(fiyat, 1.0), 1.0) if pd_milyar > 0 else 0.0)

    adil_deger_tl, iskonto_yuzde, degerleme_durumu = rasyonel_degerleme_hesapla(
        fiyat=fiyat, 
        hbk=sirket_hbk, 
        dd=sirket_dd, 
        roe=roe
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

    sirketi_tani = {
        "sirket_adi": sirket_adi,
        "ne_is_yapar": f"{sirket_adi}, {sektor} sektöründe faaliyet gösteren, Bölüm 1 süzgeçlerini geçen BIST şirketidir.",
        "urun_ve_pazar": f"{sektor} ana ürün ve hizmetleri | Yurt içi ve ihracat pazarları",
        "piyasa_degeri": f"{pd_milyar:,.1f} milyar TL" if pd_milyar > 0 else format_milyar_milyon(ozk_c * 1.3),
        "net_kar_12ay": format_milyar_milyon(net_c),
        "net_kar_yorum": f"Son dönem net kârı {format_milyar_milyon(net_c)} oldu (%{net_kar_buyume:+.1f}).",
        "roe_deger": f"%{roe:.1f}",
        "roe_yorum": f"Özkaynak kârlılığı (ROE) %{roe:.1f} düzeyindedir.",
        "carpan_ozet": f"F/K: {fk if fk > 0 else 'N/A'}x | FD/FAVÖK: {fd_favok if fd_favok > 0 else 'N/A'}x | FD/Satış: {fd_satis}x"
    }

    dna_ve_gidisat = [
        {"baslik": "Satışlar Hızlanıyor mu?", "deger": f"%{satis_buyume:+.1f}", "durum": "GÜÇLÜ" if satis_buyume >= 20.0 else "DENGELİ", "yorum": f"Yıllık satış gelirleri {format_milyar_milyon(satis_o)}'den {format_milyar_milyon(satis_c)}'ye geldi."},
        {"baslik": "Kâr Artıyor mu?", "deger": f"%{net_kar_buyume:+.1f}", "durum": "ARTIŞ VAR" if net_kar_buyume > 0 else "DÜŞÜŞ / ZARAR", "yorum": f"Net kâr {format_milyar_milyon(net_o)}'den {format_milyar_milyon(net_c)}'ye değişti."},
        {"baslik": "Marjlar Güçleniyor mu?", "deger": f"EFK %{efk_marj_c:.1f}", "durum": "İYİLEŞİYOR" if marj_fark_puan > 0 else "DARALMA", "yorum": f"Operasyonel kâr marjı önceki döneme göre {marj_fark_puan:+.1f} puan değişti."},
        {"baslik": "Borç Azalıyor mu? (Sağlamlık)", "deger": f"{net_borc_ozk:.2f}x", "durum": "GÜVENLİ" if net_borc_ozk <= 1.0 else "RİSKLİ", "yorum": f"Net Borç / Özkaynak oranı {net_borc_ozk:.2f}x. Kısa vadeli borç payı %{kv_borc_oran:.0f}."},
        {"baslik": "Parası Var mı? (Nakit Üretimi)", "deger": f"{nakit_donusum_kati:.1f} TL Nakit", "durum": "SAĞLAM" if cfo_ok else "İZLEMEDE", "yorum": f"Her 1 TL net kâra karşılık {nakit_donusum_kati:.2f} TL işletme nakdi üretildi."}
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

    bilanco_degisimleri = [
        degisim_karti("ANA ORTAKLIK ÖZKAYNAĞI", ozk_c, ozk_o, varlik_o, "Özkaynak tamponunun net kârla büyümesi içsel değeri artırır."),
        degisim_karti("MADDİ DURAN VARLIKLAR", mdv_c, mdv_o, varlik_o, "Kapasite yatırımları gelecek reel büyümenin göstergesidir."),
        degisim_karti("SİPARİŞ AVANSLARI (ERTELENMİŞ GELİR)", ertelenmis_c, ertelenmis_o, varlik_o, "Alınan peşin avanslar şirketin hazır hasılatıdır."),
        degisim_karti("STOKLAR", stok_c, stok_o, varlik_o, "Stokların satış büyümesiyle dengeli ilerlemesi izlenmelidir."),
        degisim_karti("TİCARİ ALACAKLAR", alacak_c, alacak_o, varlik_o, "Satıştan hızlı büyüyen alacaklar vade açılmasına işaret eder.")
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
        "tahta_derinligi": dna_katmani["tahta_derinligi"],
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
    # 4'LÜ DİNAMİK BUTON SAYAÇLARI
    sayi_hedef60 = sum(1 for r in tablo_verileri if r.get('kadroda', False))
    sayi_mevcut = sum(1 for r in tablo_verileri if r['Mevcut_Lot'] > 0)
    sayi_radar = sum(1 for r in tablo_verileri if r.get('is_radar', False))
    sayi_tum = len(tablo_verileri)

    satirlar_html = ""
    for r in tablo_verileri:
        kod = r["Kod"]
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

        if not r.get('kadroda', False) and r['Mevcut_Lot'] == 0 and not r.get('is_radar', False):
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
            <td><strong style="color:#38bdf8;">{kod} 🔍</strong></td>
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
                return {{ dna_ozeti: k.dna_ozeti, oz_turkce_seridi: k.oz_turkce_seridi, tahta_derinligi: k.tahta_derinligi }};
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
            const s = (sektorAdi || '').toLowerCase();
            
            let kume = 'Ağır Sanayi & Üretim';
            let minNorm = 15;
            let ustNorm = 30;
            let motor = 'Kapasite Kullanımı & Emtia/Spread Dengesi';

            if (/perakende|mağaza|gıda|içecek/i.test(s)) {{
                kume = 'Organize Perakende & Tüketim'; minNorm = 35; ustNorm = 60;
                motor = 'Yüksek Aktif Devir Hızı (Stok Sirkülasyonu)';
            }} else if (/yazılım|bilişim|teknoloji|savunma|telekom/i.test(s)) {{
                kume = 'Hafif Varlık & Teknoloji'; minNorm = 30; ustNorm = 50;
                motor = 'Yüksek Net Faaliyet Marjı';
            }} else if (/banka|sigorta|faktoring|finans|menkul/i.test(s)) {{
                kume = 'Banka & Finans'; minNorm = 22; ustNorm = 38;
                motor = 'Finansal Kaldıraç & Net Faiz Marjı';
            }} else if (/demir|çelik|petrol|kimya|çimento|otomotiv|maden/i.test(s)) {{
                kume = 'Ağır Sanayi & Döngüsel'; minNorm = 15; ustNorm = 30;
                motor = 'Emtia Döngüsü & Operasyonel Verimlilik';
            }} else if (/gyo|gayrimenkul|inşaat|turizm|enerji|otel|tarım/i.test(s)) {{
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
            const td = katman.tahta_derinligi || {{ sinif: 'Standart Tahta', rozet_renk: 'badge-ok' }};
	    
            const seritHtml = serit.map(item =>
                '<div class="oz-turkce-item"><span>' + item.ikon + ' <strong>' + item.etiket + ':</strong> ' + item.metin + '</span></div>'
            ).join('');

            document.getElementById('modalHisseBaslik').innerHTML = 
                '<div style="display:flex; align-items:center; flex-wrap:wrap;">' + 
                    kod + ' — ' + k.sirket_adi + 
                    ' <span class="' + td.rozet_renk + '" style="font-size:11px; margin-left:12px; padding:4px 8px; border-radius:4px; font-weight:bold;">' + td.sinif + '</span>' +
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

    firsat_adaylari = [
        s for s in tum_sonuclar
        if s["bolum1_gecti_mi"] and s["cfo_buyuk_net_kar"] and s["piotroski_skor"] >= 7 and s.get("roe", 0.0) >= 5.0
    ]
    print(f"  Analiz Tamamlandı: {len(firsat_adaylari)} şirket filtreyi geçerek fırsat adayı oldu.")

    havuz_kodlari = set()
    for s in firsat_adaylari: havuz_kodlari.add(s["kod"])
    for k in mevcut_pozisyonlar.keys():
        if k not in ["NAKIT", "TRY", "KASA", "PPF"]:
            havuz_kodlari.add(k)

    katsayi_sozluk = {"Korunaklı": 1.8, "Kırılgan Hendekli": 1.4, "Hendeksiz": 1.0}
    tavan_sozluk = {"Korunaklı": 10.0, "Kırılgan Hendekli": 5.0, "Hendeksiz": 3.0}

    # --- YENİ SİRKET HAVUZU DÖNGÜSÜ (630 HİSSE) ---
    # ==========================================================================
    # SERKAN ÜNAL 60 HİSSE HEDEF PORTFÖY VE SEKTÖR MOTORU
    # ==========================================================================
    # 1. Tüm fırsat adaylarına Hibrit Puan (Temel + Teknik) ata
    for s in tum_sonuclar:
        kod = s["kod"]
        tv_veri = KAP_VE_TEKNIK_AMBARI.get("teknik_veriler", {}).get(kod, None)
        s["hibrit_puan"] = hesapla_hibrit_kartal_puani(s, tv_veri)

    # 2. Top 60 Kadrosunu Belirle (Mevcut hisseler + En yüksek puanlı radarlar)
    mevcut_kodlar = {k for k in mevcut_pozisyonlar.keys() if k not in ["NAKIT", "TRY", "KASA", "PPF"]}
    
    # Elemeyi geçen adayları puanına göre sırala
    sirali_adaylar = sorted(
        [s for s in firsat_adaylari if s["kod"] not in mevcut_kodlar],
        key=lambda x: x["hibrit_puan"],
        reverse=True
    )
    
    # Hedef 60 kadrosu: Mevcutlar + En iyi adaylar (Tam 60 hisse)
    hedef_60_kodlari = set(mevcut_kodlar)
    kalan_kontenjan = max(0, 60 - len(hedef_60_kodlari))
    for s in sirali_adaylar[:kalan_kontenjan]:
        hedef_60_kodlari.add(s["kod"])

    # 3. Hendek ve Puan Tabanlı Katsayılar (60 Hisse İçin Uyarlanmış)
    # Korunaklı: ~%2.5 - %3.5, Kırılgan: ~%1.5 - %2.0, Hendeksiz: ~%1.0
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

        if kod in hedef_60_kodlari:
            # 60'lık ana portföy kadrosunda
            baz_katsayi = katsayi_sozluk.get(kat, 1.0)
            # Hibrit puanı yüksek olan hisseye ek katsayı çarpanı
            puan_carpani = max(0.7, s_veri["hibrit_puan"] / 70.0)
            katsayi = round(baz_katsayi * puan_carpani, 2)
            tavan = tavan_sozluk.get(kat, 1.5)
            is_radar = (mevcut_lot == 0)
            kadroda = True
        else:
            # 60 dışında kalan diğer hisseler (Yedek kulübesi)
            katsayi = 0.0
            tavan = 0.0
            is_radar = (s_veri in firsat_adaylari)
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

    # 4. Kasa ve Portföy Büyüklüğü
    toplam_hisse_mevcut_deger = sum(item["mevcut_lot"] * item["fiyat"] for item in sirket_havuzu)
    nakit_orani = 10.0  # Asgari %10 kasa kalkanı
    hedef_hisse_havuzu_orani = 90.0
    toplam_portfoy_tl = max(toplam_hisse_mevcut_deger / (hedef_hisse_havuzu_orani / 100.0), 1000.0)
    hisse_portfoy_tl = round(toplam_portfoy_tl * (hedef_hisse_havuzu_orani / 100.0), 2)
    nakit_tl = round(toplam_portfoy_tl * (nakit_orani / 100.0), 2)

    # 5. Ağırlıkları 60 Hisseye Dağıt (Serkan Ünal %1 Taban ve Tavan Kuralı)
    kadro_60 = [x for x in sirket_havuzu if x["kadroda"]]
    toplam_kadro_katsayi = max(sum(x["katsayi"] for x in kadro_60), 0.1)

    for item in sirket_havuzu:
        if item["kadroda"]:
            # Ham dağıtım
            pay = (item["katsayi"] / toplam_kadro_katsayi) * hedef_hisse_havuzu_orani
            # Asgari %1.0 taban, hendek tavanı ile sınırla
            item["hedef_agirlik"] = min(item["tavan"], max(1.0, round(pay, 2)))
        else:
            item["hedef_agirlik"] = 0.0

    # 6. Sektörel Çeşitlendirme Kalkanı (Maksimum %20 Sektör Üst Sınırı)

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

    SEKTOR_TAVANI = 20.0
    toplam_katsayi = max(sum(item["katsayi"] for item in sirket_havuzu), 0.1)

    for item in sirket_havuzu:
        item["hedef_agirlik"] = min((item["katsayi"] / toplam_katsayi) * hedef_hisse_havuzu_orani, item["tavan"])

    sektor_toplamlari = {}
    for item in sirket_havuzu:
        sek = item["sektor"]
        sektor_toplamlari[sek] = sektor_toplamlari.get(sek, 0.0) + item["hedef_agirlik"]

    for sek, toplam_pay in sektor_toplamlari.items():
        if toplam_pay > SEKTOR_TAVANI:
            kirpma = SEKTOR_TAVANI / toplam_pay
            for item in sirket_havuzu:
                if item["sektor"] == sek:
                    item["hedef_agirlik"] = round(item["hedef_agirlik"] * kirpma, 2)

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
        h_lot = int(h_tutar / fiyat) if fiyat > 0 else 0
        fark_lot = int(h_lot - m_lot)
        islem_tutari = round(abs(fark_lot * fiyat), 2)

        aksiyon = "AL / EKLE" if fark_lot > 0 else ("SAT / AZALT" if fark_lot < 0 else "KORU / DENGEDE")
        kz_yuzde = round(((fiyat / item["ort_maliyet"]) - 1) * 100, 2) if item["ort_maliyet"] > 0 else 0.0

	# TABLO İÇİN HIZLI ROE ROZETİ
        sek_l = str(item["sektor"]).lower()
        r_val = float(item["ham_veri"].get("roe", 0.0))
        
        # Sektör alt tabanı
        if any(x in sek_l for x in ["perakende", "gıda", "içecek"]):
            taban = 35.0
        elif any(x in sek_l for x in ["yazılım", "teknoloji", "savunma", "telekom"]):
            taban = 30.0
        elif any(x in sek_l for x in ["banka", "sigorta", "faktoring", "finans"]):
            taban = 22.0
        elif any(x in sek_l for x in ["demir", "çelik", "petrol", "kimya", "çimento", "otomotiv"]):
            taban = 15.0
        else:
            taban = 10.0

        if r_val < 5.0:
            roe_rozet = f"<span class='badge-fail'>%{r_val:.1f} 🚨</span>"
        elif r_val < taban:
            roe_rozet = f"<span class='badge-warn'>%{r_val:.1f} ⚠️</span>"
        else:
            roe_rozet = f"<span class='badge-ok'>%{r_val:.1f} 🟢</span>"

        item["roe_rozet"] = roe_rozet

        tablo_verileri.append({
            "Kod": kod, "Sektör": item["sektor"], "Hendek": item["kategori"], "F_Skor": item["f_skor"],
            "Mevcut_Lot": m_lot, "Fiyat": fiyat, "Mevcut_Tutar": m_tutar, "Mevcut_Pay": m_pay,
            "Hedef_Pay": h_pay, "Hedef_Tutar": h_tutar, "Hedef_Lot": h_lot, "Fark_Lot": fark_lot,
            "Aksiyon": aksiyon, "KZ_Yuzde": kz_yuzde, "Katsayi": item["katsayi"], "Islem_Tutari": islem_tutari,
            "Ort_Maliyet": item["ort_maliyet"],
	    "kadroda": item["kadroda"], # YENİ EKLENDİ
	    "is_radar": item["is_radar"], # YENİ EKLENDİ
	    "ROE_Rozet": item["roe_rozet"] # YENİ EKLENDİ
        })

        poz_bilgi = {"agirlik_yuzde": m_pay, "kademe": mevcut_pozisyonlar.get(kod, {}).get("kademe", "1/3 Başlangıç")}
        karneler[kod] = uret_serkan_unal_karnesi(item["ham_veri"], poz_bilgi)

    # --- HTML İÇİN 3 KATMANLI SIRALAMA MANTIĞI ---
    tasinan_hisseler = sorted([x for x in tablo_verileri if x["Mevcut_Lot"] > 0], key=lambda x: x["Mevcut_Tutar"], reverse=True)
    radar_hisseleri = sorted([x for x in tablo_verileri if x["Mevcut_Lot"] == 0 and x["is_radar"]], key=lambda x: (x["F_Skor"], x["Hedef_Pay"]), reverse=True)
    diger_hisseler = sorted([x for x in tablo_verileri if x["Mevcut_Lot"] == 0 and not x["is_radar"]], key=lambda x: x["F_Skor"], reverse=True)
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
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-m", "Oto-guncelleme: index.html ve panolar güncellendi"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("✅ GitHub Pages başarıyla güncellendi (index.html yayında)!")
    except Exception as e:
        print(f"⚠️ Git güncelleme atlandı: {e}")

github_guncelle()