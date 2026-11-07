import os
import re
import sys
import csv
import glob
import html
import json
import time
import shutil
import subprocess
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

# ==============================================================================
# 1. BAĞIMSIZ FAALİYET MERKEZİ KLASÖR MİMARİSİ
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(BASE_DIR, "hektor_kutuphane")
PORTFOY_DIR = os.path.join(ROOT_DIR, "PORTFOY")
SIRKETLER_DIR = os.path.join(ROOT_DIR, "SIRKETLER")

# Eski klasörler (Varsa içindeki PDF'leri yeni merkeze otomatik taşımak için)
ESKI_GELEN_PDF_DIR = os.path.join(PORTFOY_DIR, "gelen_raporlar")
ESKI_ARSIV_PDF_DIR = os.path.join(PORTFOY_DIR, "arsiv_raporlar")

# Tamamen izole yeni klasör sistemi
FAALIYET_MERKEZI_DIR = os.path.join(ROOT_DIR, "FAALIYET_MERKEZI")
FM_GELEN_PDF_DIR = os.path.join(FAALIYET_MERKEZI_DIR, "gelen_pdf")
FM_ARSIV_PDF_DIR = os.path.join(FAALIYET_MERKEZI_DIR, "arsiv_pdf")
FM_KARNE_HAFIZA_DIR = os.path.join(FAALIYET_MERKEZI_DIR, "karne_hafiza")
FM_OKUNAN_ID_YOLU = os.path.join(FAALIYET_MERKEZI_DIR, "okunan_kap_idleri.json")
FM_BILDIRIM_HAFIZA_YOLU = os.path.join(FAALIYET_MERKEZI_DIR, "bildirilen_donemler.json")

# Mevcut sistemden sadece OKUMA (Read-Only) yapılacak dosyalar
BIST_MALI_TABLOLAR_YOLU = os.path.join(ROOT_DIR, "bist_mali_tablolar.json")
TUM_KAP_HABERLERI_YOLU = os.path.join(ROOT_DIR, "tum_kap_haberleri.json")
ONCEKI_TARAMA_YOLU = os.path.join(PORTFOY_DIR, "onceki_tarama.json")
PORTFOY_CSV_YOLU = os.path.join(PORTFOY_DIR, "portfoyum.csv")
DINAMIK_MASA_CSV_YOLU = os.path.join(PORTFOY_DIR, "portfoy_kalkani_dinamik.csv")

MIN_DONEM = 4
MAKS_DONEM = 6

for klasor in [FAALIYET_MERKEZI_DIR, FM_GELEN_PDF_DIR, FM_ARSIV_PDF_DIR, FM_KARNE_HAFIZA_DIR, SIRKETLER_DIR]:
    os.makedirs(klasor, exist_ok=True)

TV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://tr.tradingview.com/"
}

KAP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.kap.org.tr/"
}

OZEL_KOKLER = {
    "ASTOR": ["astor enerji", "astor"],
    "PETKM": ["petkim", "socar"],
    "ASELS": ["aselsan", "asels"],
    "AEFES": ["anadolu efes", "efes biracilik", "aefes"],
    "EREGL": ["eregli demir", "erdemir"],
    "TABGD": ["tab gida", "burger king"],
    "AKBNK": ["akbank"],
    "BIMAS": ["bim birlesik", "bim magazalar"],
    "MPARK": ["mlp saglik", "medical park", "liv hospital"],
    "TTKOM": ["turk telekom", "ttkom"],
    "ANSGR": ["anadolu sigorta", "anadolu anonim"],
    "DSTKF": ["destek faktoring", "destek finans"],
    "FROTO": ["ford otomotiv", "ford otosan"],
    "THYAO": ["turk hava yollari", "thy"],
    "TUPRS": ["tupras", "turkiye petrol rafinerileri"],
    "KCHOL": ["koc holding"],
    "SAHOL": ["sabanci holding"],
    "SISE": ["turkiye sise ve cam", "sisecam"],
    "ENKAI": ["enka insaat"],
    "PGSUS": ["pegasus hava"],
    "TAVHL": ["tav havalimanlari", "tav holding"],
    "SANEL": ["sanel muhendislik", "san-el"]
}


def tr_norm(metin: str) -> str:
    if not metin:
        return ""
    ceviri = str.maketrans("İIŞĞÜÖÇıişğüöç", "iisguociisguoc")
    return re.sub(r"\s+", " ", str(metin).translate(ceviri).lower()).strip()


def eski_pdfleri_merkeze_tasi():
    """Eski gelen_raporlar veya arsiv_raporlar klasöründe PDF varsa yeni FAALIYET_MERKEZI içine kopyalar."""
    for kaynak_klasor, hedef_klasor in [(ESKI_GELEN_PDF_DIR, FM_GELEN_PDF_DIR), (ESKI_ARSIV_PDF_DIR, FM_ARSIV_PDF_DIR)]:
        if os.path.exists(kaynak_klasor):
            for f_adi in os.listdir(kaynak_klasor):
                if f_adi.lower().endswith(".pdf"):
                    kaynak = os.path.join(kaynak_klasor, f_adi)
                    hedef = os.path.join(hedef_klasor, f_adi)
                    if not os.path.exists(hedef) and not os.path.exists(os.path.join(FM_ARSIV_PDF_DIR, f_adi)):
                        try:
                            shutil.copy2(kaynak, hedef)
                        except Exception:
                            pass


# ==============================================================================
# 2. UFRS 4-6 DÖNEM DERİN DİPNOT VE RASYO MOTORU (HER HİSSE İÇİN GARANTİLİ BAZ)
# ==============================================================================
def satir_bul(satirlar: list, aranan_kelimeler: list, mod: str = "ilk") -> tuple:
    norm_arananlar = [tr_norm(k) for k in aranan_kelimeler]

    def _yasakli_satir_mi(ad: str, aranan: str) -> bool:
        if any(k in aranan for k in ["donem kar", "donem net kar", "ana ortaklik paylari"]):
            if any(y in ad for y in ["vergi", "yukumluluk", "karsilik", "dagilimi", "onceki"]):
                return True
        if aranan in ("esas faaliyet kari", "faaliyet kari") and "finansman gideri oncesi" in ad:
            return True
        return False

    if mod == "ilk":
        for aranan in norm_arananlar:
            for satir in satirlar:
                ad = tr_norm(satir[0])
                if _yasakli_satir_mi(ad, aranan):
                    continue
                v1 = float(satir[1] or 0.0)
                v2 = float(satir[2] if len(satir) > 2 and satir[2] is not None else v1)
                if ad == aranan and (v1 != 0.0 or v2 != 0.0):
                    return v1, v2
        for aranan in norm_arananlar:
            for satir in satirlar:
                ad = tr_norm(satir[0])
                if _yasakli_satir_mi(ad, aranan):
                    continue
                v1 = float(satir[1] or 0.0)
                v2 = float(satir[2] if len(satir) > 2 and satir[2] is not None else v1)
                if aranan in ad and (v1 != 0.0 or v2 != 0.0):
                    return v1, v2
        return 0.0, 0.0

    toplam_c, toplam_o = 0.0, 0.0
    bulundu = False
    for satir in satirlar:
        ad = tr_norm(satir[0])
        v1 = float(satir[1] or 0.0)
        v2 = float(satir[2] if len(satir) > 2 and satir[2] is not None else v1)
        for aranan in norm_arananlar:
            if aranan in ad or ad == aranan:
                if v1 != 0.0 or v2 != 0.0:
                    toplam_c += v1
                    toplam_o += v2
                    bulundu = True
                    break
    return (toplam_c, toplam_o) if bulundu else (0.0, 0.0)


def _donem_gun_ve_yillik_carpan(donem_adi: str) -> tuple:
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


def hesapla_ufrs_donem_paketi(satirlar_cari: list, satirlar_onceki: list, donem_adi: str, onceki_donem_adi: str) -> dict:
    donem_gun, yillik_carpan = _donem_gun_ve_yillik_carpan(donem_adi)
    _, onceki_yillik_carpan = _donem_gun_ve_yillik_carpan(onceki_donem_adi or donem_adi)

    satis_c = satir_bul(satirlar_cari, ["hasilat", "satis gelirleri", "faiz, ucret, prim, komisyon ve diger gelirler", "faiz gelirleri"])[0]
    satis_o = satir_bul(satirlar_onceki, ["hasilat", "satis gelirleri", "faiz, ucret, prim, komisyon ve diger gelirler", "faiz gelirleri"])[0]
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
    ozk_c = max(_ozk_d, 1.0)
    nakit_c = satir_bul(satirlar_cari, ["nakit ve nakit benzerleri", "nakit degerler"])[0]
    fin_borc_c = satir_bul(satirlar_cari, ["finansal borclar"], mod="topla")[0]
    fin_borc_o = satir_bul(satirlar_onceki, ["finansal borclar"], mod="topla")[0]
    fin_gider_c = abs(satir_bul(satirlar_cari, ["finansman giderleri", "(esas faaliyet disi) finansal giderler", "esas faaliyet disi finansal giderler"])[0])
    fin_gelir_c = abs(satir_bul(satirlar_cari, ["(esas faaliyet disi) finansal gelirler", "esas faaliyet disi finansal gelirler"])[0])

    alacak_c = satir_bul(satirlar_cari, ["ticari alacaklar"])[0]
    alacak_o = satir_bul(satirlar_onceki, ["ticari alacaklar"])[0]
    iliskili_alacak_c = satir_bul(satirlar_cari, ["iliskili taraflardan diğer alacaklar", "iliskili taraflardan ticari olmayan alacaklar"], mod="topla")[0]
    modv_c = satir_bul(satirlar_cari, ["maddi olmayan duran varliklar"])[0]
    modv_o = satir_bul(satirlar_onceki, ["maddi olmayan duran varliklar"])[0]
    serefiye_c = satir_bul(satirlar_cari, ["serefiye"])[0]

    yat_gelir_c = max(0.0, satir_bul(satirlar_cari, ["yatirim faaliyetlerinden gelirler"])[0])
    yat_gider_c = abs(satir_bul(satirlar_cari, ["yatirim faaliyetlerinden giderler"])[0])
    net_yat_gelir_c = max(0.0, yat_gelir_c - yat_gider_c)
    ert_vergi_gelir_c = max(0.0, satir_bul(satirlar_cari, ["ertelenmis vergi geliri"])[0])
    kagit_kar = net_yat_gelir_c + ert_vergi_gelir_c
    karsiliklar_c = satir_bul(satirlar_cari, ["borc karsiliklari", "kisa vadeli karsiliklar", "uzun vadeli karsiliklar", "calisanlara saglanan"], mod="topla")[0]

    ticari_borc_c = satir_bul(satirlar_cari, ["ticari borclar"])[0]
    avans_c_val = satir_bul(satirlar_cari, ["musteri soz. dogan yuk.", "musteri soz.dogan yuk.", "ertelenmis gelirler", "alinan siparis avanslari"], mod="topla")[0]
    net_nakit_mi = (fin_borc_c <= nakit_c) or (fin_borc_c < varlik_c * 0.015)
    ort_borc = max((fin_borc_c + fin_borc_o) / 2.0, 1.0)
    ham_net_fin_gider = max(0.0, fin_gider_c - fin_gelir_c)
    toplam_fin_tabani = max(ort_borc + (ticari_borc_c * 0.60) + (avans_c_val * 0.35), ort_borc, 1.0)
    kredi_payi_carpani = max(0.25, ort_borc / toplam_fin_tabani)
    net_fin_gider_c = (ham_net_fin_gider * kredi_payi_carpani) if fin_borc_c > 0 else 0.0

    efm_yuzde = 0.0 if (net_nakit_mi or fin_borc_c <= 0) else round(((net_fin_gider_c * yillik_carpan) / ort_borc) * 100.0, 1)
    operasyonel_nakit_kar = max(efk_c + amort_c, efk_c)
    if net_nakit_mi or net_fin_gider_c <= 0:
        icr_kati = round(operasyonel_nakit_kar / max(fin_gider_c, 1.0), 2) if fin_gider_c > 0 else 99.0
        icr_kati = max(icr_kati, 9.9) if net_nakit_mi and efk_c > 0 else icr_kati
    else:
        icr_kati = round(operasyonel_nakit_kar / max(net_fin_gider_c, 1.0), 2)

    tahakkuk_orani = round(((net_c - cfo_c) / varlik_c) * 100.0, 2)
    aga_orani = round((max(0.0, modv_c - modv_o) / max(efk_c, 1.0)) * 100.0, 1) if efk_c > 0 else 0.0
    ykp_orani = min(100.0, round((kagit_kar / max(efk_c + kagit_kar if efk_c > 0 else varlik_c, 1.0)) * 100.0, 1))

    dso_gun = round(donem_gun * (alacak_c / max(satis_c, 1.0)), 1) if satis_c > 0 else 0.0
    satis_c_yillik = satis_c * yillik_carpan
    satis_o_yillik = satis_o * onceki_yillik_carpan
    alacak_buyume = ((alacak_c / max(alacak_o, 1.0)) - 1.0) * 100.0 if alacak_o > 0 else 0.0
    satis_buyume = ((satis_c_yillik / max(satis_o_yillik, 1.0)) - 1.0) * 100.0 if satis_o_yillik > 0 else 0.0
    acm_makasi = round(alacak_buyume - satis_buyume, 1)

    serefiye_ozk_orani = round((serefiye_c / ozk_c) * 100.0, 1)
    karsilik_ozk_orani = round((karsiliklar_c / ozk_c) * 100.0, 1)
    iliskili_orani = round((iliskili_alacak_c / varlik_c) * 100.0, 2)

    mdv_c_val = satir_bul(satirlar_cari, ["maddi duran varliklar"])[0]
    capex_c_val = abs(satir_bul(satirlar_cari, ["sabit sermaye yatirimlari"])[0])
    ydisi_satis_c = satir_bul(satirlar_cari, ["yurtdisi satislar"])[0]
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
        "capex_amort_kati": capex_amort_kati, "ihracat_payi": ihracat_payi,
        "avans_ciro_orani": avans_ciro_orani, "ypp_ozk_orani": ypp_ozk_orani,
        "pdf_dipnot_notlari": {
            "yatirim_ve_kapasite": not_yatirim,
            "siparis_ve_backlog": not_siparis,
            "doviz_ve_turev": not_doviz,
            "tri_rehin_ipotek": not_tri
        }
    }


def uret_temel_4_6_donem_arsivi(hisse: str, mali_veritabani: dict, yerel_kap_ambari: dict) -> dict:
    """
    Şirketin UFRS çok dönemli tablolarından ve KAP haberlerinden 4-6 dönemlik
    birikimli faaliyet & dipnot arşivini garantili olarak oluşturur/günceller.
    """
    kopru_json_yolu = os.path.join(SIRKETLER_DIR, hisse, "04_birikimli_dipnot_arsivi.json")
    mevcut_arsiv = {"kod": hisse, "son_guncelleme": "", "donemler": {}}
    if os.path.exists(kopru_json_yolu):
        try:
            with open(kopru_json_yolu, "r", encoding="utf-8") as f:
                mevcut_arsiv = json.load(f)
        except Exception:
            pass

    sirket_kaydi = mali_veritabani.get(hisse, {})
    cok_donem = sirket_kaydi.get("donemler", {})
    sirali_anahtarlar = sorted(cok_donem.keys())[-MAKS_DONEM:]

    for idx, d_adi in enumerate(sirali_anahtarlar):
        ay_kodu = d_adi.split("/")[-1] if "/" in d_adi else ""
        es_donem_adi = d_adi
        tum_oncekiler = [k for k in sorted(cok_donem.keys()) if k < d_adi]
        for k_onceki in reversed(tum_oncekiler):
            if k_onceki.endswith("/" + ay_kodu):
                es_donem_adi = k_onceki
                break
        if es_donem_adi == d_adi and tum_oncekiler:
            es_donem_adi = tum_oncekiler[-1]

        cari_s = [(s[0], s[1], s[1]) for s in cok_donem[d_adi]]
        onceki_s = [(s[0], s[1], s[1]) for s in cok_donem.get(es_donem_adi, cok_donem[d_adi])]
        hesaplanan = hesapla_ufrs_donem_paketi(cari_s, onceki_s, d_adi, es_donem_adi)

        # Varsa önceki PDF eklerini koru
        eski_d = mevcut_arsiv.get("donemler", {}).get(d_adi, {})
        eski_notlar = eski_d.get("pdf_dipnot_notlari") or {}
        for k_alan, v_oto in hesaplanan["pdf_dipnot_notlari"].items():
            eski_v = str(eski_notlar.get(k_alan, "")).strip()
            if "Faaliyet Raporu:" in eski_v and "<div style='margin-top:6px;" in eski_v:
                pdf_ek = "<div style='margin-top:6px;" + eski_v.split("<div style='margin-top:6px;", 1)[1]
                hesaplanan["pdf_dipnot_notlari"][k_alan] = v_oto + pdf_ek

        if eski_d.get("tutarlilik_raporu"):
            hesaplanan["tutarlilik_raporu"] = eski_d["tutarlilik_raporu"]

        mevcut_arsiv.setdefault("donemler", {})[d_adi] = hesaplanan

    # En son çeyreğe varsa güncel KAP sipariş/yatırım başlıklarını da iliştir
    son_d_liste = sorted(mevcut_arsiv.get("donemler", {}).keys())
    if son_d_liste:
        son_d = son_d_liste[-1]
        kap_list = yerel_kap_ambari.get(hisse, [])
        for kh in kap_list[:3]:
            bsl = str(kh.get("baslik") or "").strip()
            trh = str(kh.get("tarih") or "").strip()
            norm_b = tr_norm(bsl)
            hedef_alan = None
            if any(w in norm_b for w in ["siparis", "ihale", "sozlesme", "is iliskisi", "satis"]):
                hedef_alan = "siparis_ve_backlog"
            elif any(w in norm_b for w in ["yatirim", "kapasite", "tesvik", "fabrika", "tesis"]):
                hedef_alan = "yatirim_ve_kapasite"
            if hedef_alan:
                mevcut_h = mevcut_arsiv["donemler"][son_d]["pdf_dipnot_notlari"].get(hedef_alan, "")
                if bsl[:30] not in mevcut_h and "KAP Notu:" not in mevcut_h:
                    mevcut_arsiv["donemler"][son_d]["pdf_dipnot_notlari"][hedef_alan] = (
                        mevcut_h + f"<div style='margin-top:5px; font-size:10px; color:#93c5fd;'>📌 <strong>KAP Notu ({trh}):</strong> {html.escape(bsl[:90])}</div>"
                    )

    tum_adlar = sorted(mevcut_arsiv["donemler"].keys())
    for i, d_adi in enumerate(tum_adlar):
        curr = mevcut_arsiv["donemler"][d_adi]
        if i > 0:
            prev = mevcut_arsiv["donemler"][tum_adlar[i - 1]]
            curr["dts_delta"] = curr["dts_skor"] - prev["dts_skor"]
            curr["dso_delta"] = round(curr["dso_gun"] - prev["dso_gun"], 1)
        else:
            curr["dts_delta"], curr["dso_delta"] = 0, 0.0

    return mevcut_arsiv


# ==============================================================================
# 3. HEDEF 60, MEVCUT PORTFÖY VE TEK HİSSE OKUYUCU
# ==============================================================================
def portfoy_ve_masa_verilerini_getir() -> dict:
    """portfoy_kalkani_dinamik.csv ve portfoyum.csv içinden güncel kalkan verilerini okur."""
    masa = {}
    if os.path.exists(DINAMIK_MASA_CSV_YOLU):
        try:
            with open(DINAMIK_MASA_CSV_YOLU, "r", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f, delimiter=";"):
                    kod = (r.get("Kod") or "").strip().upper()
                    if kod:
                        masa[kod] = {
                            "sektor": r.get("Sektör", "Sanayi"),
                            "hendek": r.get("Hendek", "Hendeksiz"),
                            "f_skor": int(float(r.get("F_Skor", 6) or 6)),
                            "hibrit_puan": float(r.get("Hibrit_Puan", 50.0) or 50.0),
                            "mevcut_lot": int(float(r.get("Mevcut_Lot", 0) or 0)),
                            "fiyat": float(r.get("Fiyat", 0.0) or 0.0),
                            "mevcut_pay": float(r.get("Mevcut_Pay", 0.0) or 0.0),
                            "hedef_pay": float(r.get("Hedef_Pay", 0.0) or 0.0),
                            "fark_lot": int(float(r.get("Fark_Lot", 0) or 0)),
                            "aksiyon": str(r.get("Aksiyon", "İZLEMEDE")),
                            "kadroda": str(r.get("kadroda", "")).lower() == "true"
                        }
        except Exception:
            pass

    if os.path.exists(PORTFOY_CSV_YOLU):
        try:
            with open(PORTFOY_CSV_YOLU, "r", encoding="utf-8-sig") as f:
                for satir in csv.reader(f, delimiter=";"):
                    if satir and 3 <= len(satir[0].strip()) <= 6:
                        kod = satir[0].strip().upper()
                        if kod not in {"KOD", "HISSE", "NAKIT", "TRY", "KASA", "PPF"}:
                            masa.setdefault(kod, {})["portfoyde_tasiniyor"] = True
        except Exception:
            pass
    return masa


def hedef_hisseleri_belirle(masa: dict) -> list:
    if len(sys.argv) > 1:
        ozel_kodlar = [arg.strip().upper() for arg in sys.argv[1:] if 3 <= len(arg.strip()) <= 6]
        if ozel_kodlar:
            return ozel_kodlar

    hedef_set = set()
    if os.path.exists(ONCEKI_TARAMA_YOLU):
        try:
            with open(ONCEKI_TARAMA_YOLU, "r", encoding="utf-8") as f:
                for k in json.load(f).get("hedef_60_kadrosu", []):
                    hedef_set.add(str(k).strip().upper())
        except Exception:
            pass

    for kod, bilgi in masa.items():
        if bilgi.get("kadroda") or bilgi.get("mevcut_lot", 0) > 0 or bilgi.get("portfoyde_tasiniyor"):
            hedef_set.add(kod)

    sirali = sorted(
        list(hedef_set),
        key=lambda k: (1 if masa.get(k, {}).get("mevcut_lot", 0) > 0 else 0, masa.get(k, {}).get("hibrit_puan", 0.0)),
        reverse=True
    )
    return sirali


# ==============================================================================
# 4. KAP & TRADINGVIEW RAPOR İNDİRİCİ + AKILLI PDF EŞLEŞTİRİCİ
# ==============================================================================
def okunan_idleri_yukle() -> set:
    if os.path.exists(FM_OKUNAN_ID_YOLU):
        try:
            with open(FM_OKUNAN_ID_YOLU, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def okunan_idleri_kaydet(id_set: set):
    try:
        with open(FM_OKUNAN_ID_YOLU, "w", encoding="utf-8") as f:
            json.dump(sorted(list(id_set))[-5000:], f, indent=2)
    except Exception:
        pass


def gercek_kap_id_cikar(session, b_id: str, yol_bilgi: str) -> str:
    r"""
    TradingView matriks:XXXXXXX ID'lerini doğrudan KAP ID sanma hatasını önler;
    yalnızca gerçek kap.org.tr/tr/Bildirim/(\d+) veya kap:(\d+) ID'lerini döndürür.
    """
    birlesik = f"{b_id} {yol_bilgi}"
    m_kap = re.search(r"(?:kap[:/_-]|Bildirim/)(\d{6,8})", birlesik, re.IGNORECASE)
    if m_kap:
        return m_kap.group(1)

    if b_id.startswith("matriks:") or "matriks:" in birlesik:
        try:
            st_url = f"https://news-headlines.tradingview.com/v2/story?id={urllib.parse.quote(b_id)}&client=web&lang=tr"
            r = session.get(st_url, headers=TV_HEADERS, timeout=6)
            if r.status_code == 200:
                st_txt = r.text
                m_st = re.search(r"kap\.org\.tr/tr/Bildirim/(\d{6,8})", st_txt, re.IGNORECASE)
                if m_st:
                    return m_st.group(1)
        except Exception:
            pass
    return ""


def hisse_mevcut_arsiv_sayisi(hisse: str) -> int:
    gelen = glob.glob(os.path.join(FM_GELEN_PDF_DIR, f"{hisse}_*.pdf"))
    arsiv = glob.glob(os.path.join(FM_ARSIV_PDF_DIR, f"{hisse}_*.pdf"))
    return len(set(os.path.basename(p) for p in gelen + arsiv))


def hisse_raporlarini_indir(session, hisse: str, okunan_idler: set, yerel_kap_ambari: dict) -> int:
    mevcut_sayi = hisse_mevcut_arsiv_sayisi(hisse)
    if mevcut_sayi >= MAKS_DONEM:
        return 0

    aday_bildirimler = []
    for kh in yerel_kap_ambari.get(hisse, []):
        baslik = str(kh.get("baslik") or "")
        link = str(kh.get("link") or "")
        b_id = str(kh.get("id") or "")
        tarih_str = str(kh.get("tarih") or "")
        yil_ay = datetime.now().strftime("%Y_%m")
        m_t = re.search(r"(\d{2})\.(\d{2})\.(202\d)", tarih_str)
        if m_t:
            yil_ay = f"{m_t.group(3)}_{m_t.group(2)}"
        aday_bildirimler.append((baslik, b_id, link, yil_ay))

    url = f"https://news-headlines.tradingview.com/v2/headlines?client=web&lang=tr&symbol=BIST%3A{hisse}"
    try:
        r = session.get(url, headers=TV_HEADERS, timeout=8)
        if r.status_code == 200:
            for it in r.json().get("items", []):
                baslik = str(it.get("title") or "")
                ozet = str(it.get("shortDescription") or baslik)
                b_id = str(it.get("id") or "")
                story_path = str(it.get("storyPath") or "")
                pub_ts = it.get("published")
                dt = datetime.fromtimestamp(pub_ts) if pub_ts else datetime.now()
                yil_ay = dt.strftime("%Y_%m")
                aday_bildirimler.append((f"{baslik} {ozet}", b_id, story_path, yil_ay))
    except Exception:
        pass

    indirilen = 0
    for metin, b_id, yol_bilgi, yil_ay in aday_bildirimler:
        if mevcut_sayi + indirilen >= MAKS_DONEM:
            break
        norm_m = tr_norm(metin)
        if any(w in norm_m for w in ["faaliyet raporu", "finansal rapor", "entegre faaliyet", "ara donem faaliyet"]):
            kap_id = gercek_kap_id_cikar(session, b_id, yol_bilgi)
            if not kap_id or kap_id in okunan_idler:
                continue

            r_tipi = "Faaliyet" if "faaliyet" in norm_m else "Finansal"
            dosya_adi = f"{hisse}_{yil_ay}_{r_tipi}_{kap_id}.pdf"
            hedef_yol = os.path.join(FM_GELEN_PDF_DIR, dosya_adi)
            arsiv_yol = os.path.join(FM_ARSIV_PDF_DIR, dosya_adi)

            if os.path.exists(hedef_yol) or os.path.exists(arsiv_yol):
                okunan_idler.add(kap_id)
                continue

            pdf_url = f"https://www.kap.org.tr/tr/BildirimPdf/{kap_id}"
            try:
                time.sleep(0.6)
                res = session.get(pdf_url, headers=KAP_HEADERS, timeout=35)
                if res.status_code == 200 and len(res.content) > 40000 and res.content[:4] == b"%PDF":
                    with open(hedef_yol, "wb") as f:
                        f.write(res.content)
                    okunan_idler.add(kap_id)
                    indirilen += 1
                    print(f"  📥 [PDF İNDİRİLDİ] {hisse} ({yil_ay}) {r_tipi} Raporu -> {dosya_adi}")
            except Exception:
                pass

    return indirilen


def pdf_hisse_ve_donem_coz(dosya_adi: str, ilk_sayfalar: str, mali_veritabani: dict) -> tuple:
    """Dosya adı HISSE_ ile başlamasa bile PDF'in ilk 6 sayfasından şirketi ve dönemi tespit eder."""
    ad_norm = tr_norm(dosya_adi)
    metin_norm = tr_norm(ilk_sayfalar)

    tespit_hisse = None
    ilk_token = dosya_adi.split("_")[0].strip().upper()
    if ilk_token in mali_veritabani:
        tespit_hisse = ilk_token

    if not tespit_hisse:
        dosya_tokenleri = set(re.split(r"[^a-z0-9]+", ad_norm))
        for kod in mali_veritabani.keys():
            if kod.lower() in dosya_tokenleri:
                tespit_hisse = kod
                break

    if not tespit_hisse:
        for kod, kok_list in OZEL_KOKLER.items():
            if any(tr_norm(k) in ad_norm or tr_norm(k) in metin_norm for k in kok_list):
                tespit_hisse = kod
                break

    if not tespit_hisse:
        for kod, v in mali_veritabani.items():
            unvan = tr_norm(v.get("sirket_adi", ""))
            temiz_u = re.sub(r"\b(a\.s\.|as|sanayi|ve|ticaret|holding|pazarlama|isletmeleri)\b", "", unvan).strip()
            if len(temiz_u) >= 5 and (temiz_u in ad_norm or temiz_u in metin_norm):
                tespit_hisse = kod
                break

    if not tespit_hisse:
        return None, None

    gecerli_donemler = list(mali_veritabani.get(tespit_hisse, {}).get("donemler", {}).keys())
    bilesik = ad_norm + " " + metin_norm
    yil_m = re.search(r"(202[3-7])", bilesik)
    yil = yil_m.group(1) if yil_m else datetime.now().strftime("%Y")

    tahmin = None
    if any(k in bilesik for k in ["1. ceyrek", "1 ceyrek", "q1", "3 aylik", "31.03", "31 mart"]):
        tahmin = f"{yil}/03"
    elif any(k in bilesik for k in ["2. ceyrek", "2 ceyrek", "q2", "6 aylik", "30.06", "30 haziran", "yariyil"]):
        tahmin = f"{yil}/06"
    elif any(k in bilesik for k in ["3. ceyrek", "3 ceyrek", "q3", "9 aylik", "30.09", "30 eylul"]):
        tahmin = f"{yil}/09"
    elif any(k in bilesik for k in ["4. ceyrek", "yillik", "12 aylik", "31.12", "31 aralik"]):
        tahmin = f"{yil}/12"
    else:
        ay_m = re.search(r"(202[3-7])_(\d{2})", dosya_adi)
        if ay_m:
            y_val, a_val = int(ay_m.group(1)), int(ay_m.group(2))
            tahmin = f"{y_val - 1}/12" if a_val in (1, 2, 3) else (f"{y_val}/03" if a_val in (4, 5, 6) else (f"{y_val}/06" if a_val in (7, 8, 9) else f"{y_val}/09"))
        else:
            tahmin = gecerli_donemler[-1] if gecerli_donemler else f"{yil}/06"

    if gecerli_donemler and tahmin not in gecerli_donemler:
        sirali_g = sorted(gecerli_donemler)
        alt = [d for d in sirali_g if d <= tahmin]
        tahmin = alt[-1] if alt else sirali_g[-1]

    return tespit_hisse, tahmin


def pdf_stratejik_bolumleri_oku(doc) -> dict:
    kaliplar = {
        "yatirim_ve_kapasite": [
            "yatirim harcamalari", "devam eden yatirimlar", "kapasite kullanim",
            "kapasite artisi", "fabrika yatirimi", "yeni tesis", "ges yatirimi", "modernizasyon"
        ],
        "siparis_ve_backlog": [
            "bakiye siparis", "alinan siparisler", "backlog", "siparis miktari",
            "sozlesme bedeli", "ihracat", "pazar payi", "beklentiler", "hedefler"
        ],
        "doviz_ve_turev": [
            "yabanci para pozisyonu", "kur riski", "turev finansal araclar",
            "forward", "para swap", "hedge", "doviz kuru duyarlilik"
        ],
        "tri_rehin_ipotek": [
            "taahhut rehin ipotek", "verilen teminatlar", "ipotek",
            "kefaletler", "rehin", "sarta bagli yukumlulukler", "davalar"
        ]
    }

    bulgular = {k: [] for k in kaliplar}
    for sayfa_no in range(len(doc)):
        ham = doc[sayfa_no].get_text("text")
        if not ham or len(ham.strip()) < 100:
            continue
        satirlar = [s.strip() for s in ham.splitlines() if len(s.strip()) > 0]
        govde = " ".join(satirlar[1:-1]) if len(satirlar) > 4 else " ".join(satirlar)
        norm_g = tr_norm(govde)

        for kat, kelimeler in kaliplar.items():
            for kel in kelimeler:
                pos = norm_g.find(kel)
                if pos != -1:
                    bas = max(0, pos - 40)
                    bit = min(len(govde), pos + 360)
                    kesit = html.escape(re.sub(r"\s+", " ", govde[bas:bit].strip()))
                    skor = len(re.findall(r"(%\s*\d+|\d+[.,]\d+|\b202[4-7]\b)", kesit))
                    bulgular[kat].append((skor, f"[S.{sayfa_no + 1}] {kesit}"))
                    break

    sonuc = {}
    for kat, liste in bulgular.items():
        if liste:
            en_iyiler = [x[1] for x in sorted(liste, key=lambda i: i[0], reverse=True)[:1]]
            sonuc[kat] = (
                "<div style='margin-top:6px; padding-top:5px; border-top:1px dashed #334155; "
                "font-size:10.5px; color:#cbd5e1;'>📄 <strong>Faaliyet Raporu:</strong> "
                + " ".join(en_iyiler) + "</div>"
            )
        else:
            sonuc[kat] = None
    return sonuc


# ==============================================================================
# 5. 4-6 DÖNEM BİRİKİMLİ KARAR MEKANİZMASI & INDEX.HTML CANLI GÜNCELLEYİCİ
# ==============================================================================
def uret_birikimli_karar_ve_tutarlilik(hisse: str, donem: str, yeni_notlar: dict, mevcut_arsiv: dict, masa_bilgi: dict) -> list:
    donemler_dict = mevcut_arsiv.get("donemler", {})
    tum_donemler = sorted(list(set(list(donemler_dict.keys()) + [donem])))[-MAKS_DONEM:]
    gecmis_donemler = [d for d in tum_donemler if d < donem]

    cari_d = donemler_dict.get(donem, {})
    if not cari_d or (cari_d.get("dso_gun", 0.0) == 0.0 and gecmis_donemler):
        cari_d = donemler_dict.get(gecmis_donemler[-1], cari_d)

    dts_c = int(cari_d.get("dts_skor", 0))
    dso_c = float(cari_d.get("dso_gun", 0.0))
    icr_c = float(cari_d.get("icr_kati", 3.0))
    tahakkuk_c = float(cari_d.get("tahakkuk_orani", 0.0))
    capex_kati = float(cari_d.get("capex_amort_kati", 1.0))
    ihracat_p = float(cari_d.get("ihracat_payi", 0.0))
    ypp_ozk = float(cari_d.get("ypp_ozk_orani", 0.0))

    maddeler = []

    # 1. 4-6 Dönemlik Birikimli Trend Analizi
    if gecmis_donemler:
        ilk_d = gecmis_donemler[0]
        dts_ilk = int(donemler_dict.get(ilk_d, {}).get("dts_skor", dts_c))
        dso_ilk = float(donemler_dict.get(ilk_d, {}).get("dso_gun", dso_c))
        dts_fark = dts_c - dts_ilk
        dso_fark = round(dso_c - dso_ilk, 1)

        if dts_c >= 45 or dts_fark >= 20:
            maddeler.append(
                f"🔴 BİRİKİMLİ DİPNOT BOZULMASI ({ilk_d} → {donem}): {len(tum_donemler)} çeyrekte DTS {dts_ilk} puandan "
                f"{dts_c}/100 puana yükseldi (DSO: {dso_ilk:.0f}g → {dso_c:.0f}g, {dso_fark:+.1f} gün)."
            )
        elif dts_fark < 0 and dts_c <= 25:
            maddeler.append(
                f"🟢 BİRİKİMLİ BİLANÇO İYİLEŞMESİ ({ilk_d} → {donem}): {len(tum_donemler)} çeyrekte DTS {dts_ilk} puandan "
                f"{dts_c}/100 seviyesine geriledi (DSO: {dso_c:.1f} gün); dipnot kalitesi güçleniyor."
            )
        else:
            maddeler.append(
                f"🟢 {len(tum_donemler)} ÇEYREKLİK DİPNOT İSTİKRARI ({ilk_d} → {donem}): DTS {dts_c}/100 puan, "
                f"Alacak tahsil süresi {dso_c:.1f} gün ({dso_fark:+.1f}g değişim) ve ICR {icr_c:.2f}x düzeyinde."
            )
    else:
        maddeler.append(f"🟢 BAZ DÖNEM ({donem}): {hisse} faaliyet ve dipnot karnesi oluşturuldu (DTS: {dts_c}/100, DSO: {dso_c:.1f} gün).")

    # 2. Yatırım (CAPEX), İhracat ve Kur Koruması Sentezi
    yat_norm = tr_norm((yeni_notlar or {}).get("yatirim_ve_kapasite") or "")
    if any(k in yat_norm for k in ["ertelendi", "gecikme", "revize edildi", "aksama", "durduruldu"]):
        maddeler.append(f"🟡 YATIRIM TAKVİMİ UYARISI: {donem} faaliyet raporunda yatırım/kapasite takviminde gecikme veya revizyon sinyali var.")
    else:
        capex_etiket = "agresif kapasite artışı" if capex_kati >= 1.5 else ("kapasite korunumu" if capex_kati >= 0.7 else "hasat/düşük CAPEX")
        ihracat_ifade = f"İhracat payı %{ihracat_p:.1f}" if ihracat_p > 0.0 else "Konsolide/Yurt içi satış yapısı"
        ypp_ifade = f"Net YPP/ÖZK %{ypp_ozk:+.1f} ile dengeli" if ypp_ozk >= -15.0 else f"Net YPP/ÖZK %{ypp_ozk:+.1f} (Kur açık pozisyonu izlenmeli)"
        ikon_yat = "🟢" if ypp_ozk >= -15.0 else "🟡"
        maddeler.append(
            f"{ikon_yat} YATIRIM & PAZAR DİNAMİĞİ: CAPEX/Amortisman {capex_kati:.1f}x ({capex_etiket}), "
            f"{ihracat_ifade} ve {ypp_ifade}."
        )

    # 3. Söylem - Nakit (Tahakkuk) Çapraz Kontrolü
    if tahakkuk_c > 5.0:
        maddeler.append(
            f"🟡 SÖYLEM - NAKİT ÇELİŞKİSİ: Muhasebesel kâra karşın tahakkuk oranı (%{tahakkuk_c:+.1f}) yüksek; kârın kasaya nakit (CFO) girişi izlenmeli."
        )
    else:
        maddeler.append(f"🟢 NAKİT & TAHAKKUK TEYİDİ: Tahakkuk oranı %{tahakkuk_c:+.1f} (≤%5.0 sağlam) ile bilanço kârı gerçek işletme nakdine dönüşüyor.")

    # 4. Hisseye Özel Karar Mekanizması
    m_lot = masa_bilgi.get("mevcut_lot", 0)
    m_pay = masa_bilgi.get("mevcut_pay", 0.0)
    h_pay = masa_bilgi.get("hedef_pay", 0.0)
    aksiyon = masa_bilgi.get("aksiyon", "İZLEMEDE")
    h_puan = masa_bilgi.get("hibrit_puan", 50.0)
    f_skor = masa_bilgi.get("f_skor", 6)

    if dts_c >= 50 or (icr_c < 1.25 and dts_c >= 35):
        karar = (
            f"🚨 KARAR MEKANİZMASI [RİSK FRENİ / AZALT]: {len(tum_donemler)} çeyreklik incelemede DTS ({dts_c}/100) "
            f"veya ICR ({icr_c:.2f}x) kritik sınırı aştı. Mevcut Pay: %{m_pay:.2f} | Hedef Pay: %{h_pay:.2f} -> Yeni alım durdurulmalı."
        )
    elif m_lot > 0:
        karar = (
            f"🟢 KARAR MEKANİZMASI [TAŞINAN HİSSE — {aksiyon}]: {len(tum_donemler)} çeyreklik faaliyet/dipnot karnesi onaylandı "
            f"(DTS: {dts_c}/100 | F-Skor: {f_skor}/9 | Hibrit Puan: {h_puan:.1f}). Mevcut Pay: %{m_pay:.2f} → Hedef Pay: %{h_pay:.2f} (Pazartesi: {masa_bilgi.get('fark_lot', 0):+,} lot)."
        )
    elif masa_bilgi.get("kadroda"):
        karar = (
            f"🟢 KARAR MEKANİZMASI [HEDEF 60 ASİL KADRO — {aksiyon}]: {len(tum_donemler)} çeyreklik faaliyet/dipnot taraması temiz "
            f"(DTS: {dts_c}/100 | F-Skor: {f_skor}/9 | Hibrit Puan: {h_puan:.1f}). Kalkan Hedef Ağırlığı: %{h_pay:.2f} ({masa_bilgi.get('fark_lot', 0):+,} lot)."
        )
    else:
        karar = (
            f"🟡 KARAR MEKANİZMASI [KADRO DIŞI / İZLEME — {aksiyon}]: {len(tum_donemler)} çeyreklik dipnot incelemesinde "
            f"DTS: {dts_c}/100, F-Skor: {f_skor}/9, Hibrit Puan: {h_puan:.1f}. Hedef 60 asil kadrosu dışında ({aksiyon}) izleniyor."
        )

    return [karar] + maddeler


def index_html_6_kart_anlik_yama(guncellenen_arsivler: dict):
    """
    Tek hisse modunda 600 hisselik kartal.py'yi baştan çalıştırmaya gerek kalmadan
    index.html içindeki KARNELER JSON objesini 0.1 saniyede yerinde günceller.
    """
    index_yolu = os.path.join(BASE_DIR, "index.html")
    if not os.path.exists(index_yolu) or not guncellenen_arsivler:
        return False
    try:
        with open(index_yolu, "r", encoding="utf-8") as f:
            icerik = f.read()
        isaret = "const KARNELER = "
        bas = icerik.find(isaret)
        if bas == -1:
            return False
        json_bas = bas + len(isaret)
        satir_sonu = icerik.find(";\n", json_bas)
        if satir_sonu == -1:
            return False
        ham_json = icerik[json_bas:satir_sonu].replace("<\\/", "</")
        karneler = json.loads(ham_json)
        degisti = False
        for hisse, arsiv_veri in guncellenen_arsivler.items():
            if hisse in karneler:
                karneler[hisse]["birikimli_dipnot"] = arsiv_veri
                degisti = True
        if degisti:
            yeni_json_str = json.dumps(karneler, ensure_ascii=False).replace("</", "<\\/")
            yeni_icerik = icerik[:json_bas] + yeni_json_str + icerik[satir_sonu:]
            with open(index_yolu, "w", encoding="utf-8") as f:
                f.write(yeni_icerik)
            return True
    except Exception:
        pass
    return False


def telegram_ozel_bildirim_at(hisse: str, donem: str, karar_maddeleri: list, masa_bilgi: dict):
    token_yolu = os.path.join(BASE_DIR, "token.txt")
    chat_yolu = os.path.join(BASE_DIR, "chat_id.txt")
    if not (os.path.exists(token_yolu) and os.path.exists(chat_yolu)):
        return
    try:
        with open(token_yolu, "r", encoding="utf-8") as f:
            token = f.read().strip()
        with open(chat_yolu, "r", encoding="utf-8") as f:
            chat_id = f.read().strip()
        if not token or not chat_id:
            return

        durum_etiket = "TAŞINAN HİSSE" if masa_bilgi.get("mevcut_lot", 0) > 0 else ("HEDEF 60 ADAYI" if masa_bilgi.get("kadroda") else "TEFTİŞ ANALİZİ")
        temiz_maddeler = [html.escape(re.sub(r"<[^>]+>", "", m)) for m in karar_maddeleri[:4]]
        ozet = "\n\n".join(f"• {m}" for m in temiz_maddeler)
        mesaj = (
            f"📑 <b>FAALİYET MERKEZİ — 4-6 DÖNEM KARAR RAPORU</b>\n"
            f"📌 <b>{html.escape(hisse)} ({html.escape(donem)})</b> — <i>{durum_etiket}</i>\n\n"
            f"{ozet}\n\n"
            f"🔍 <b>index.html 6. Sekme (Dipnot &amp; Matris)</b> güncellendi."
        )
        res = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": mesaj, "parse_mode": "HTML"},
            timeout=10
        )
        if res.status_code == 200:
            print(f"  📲 [Telegram] {hisse} ({donem}) için 4-6 dönemlik karar raporu telefonunuza gönderildi.")
        else:
            print(f"  ⚠️ [Telegram] Bildirim hatası ({res.status_code}): {res.text[:120]}")
    except Exception as e:
        print(f"  ⚠️ [Telegram] Bağlantı uyarısı: {e}")


# ==============================================================================
# 6. ANA ÇALIŞTIRICI (ÇİFT MOTORLU: PDF MADENCİSİ + 4-6 ÇEYREK UFRS DENETÇİSİ)
# ==============================================================================
def main():
    print("=" * 72)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] BAĞIMSIZ FAALİYET RAPORU & 4-6 DÖNEM KARAR MOTORU BAŞLADI")
    print("=" * 72)

    eski_pdfleri_merkeze_tasi()
    masa = portfoy_ve_masa_verilerini_getir()
    hedef_hisseler = hedef_hisseleri_belirle(masa)
    tek_hisse_modu = len(sys.argv) > 1

    mali_veritabani = {}
    if os.path.exists(BIST_MALI_TABLOLAR_YOLU):
        try:
            with open(BIST_MALI_TABLOLAR_YOLU, "r", encoding="utf-8") as f:
                mali_veritabani = json.load(f).get("sirketler", {})
        except Exception as e:
            print(f"  ⚠️ Mali veri tabanı okunamadı: {e}")

    yerel_kap_ambari = {}
    if os.path.exists(TUM_KAP_HABERLERI_YOLU):
        try:
            with open(TUM_KAP_HABERLERI_YOLU, "r", encoding="utf-8") as f:
                yerel_kap_ambari = json.load(f).get("kap_haberleri", {})
        except Exception:
            pass

    print(f"🎯 Taranacak Şirket Sayısı: {len(hedef_hisseler)} ({'Tek Hisse Modu: ' + ', '.join(hedef_hisseler) if tek_hisse_modu else 'Hedef 60 + Mevcut Lotlar'})")

    # 1. AŞAMA: KAP / TradingView Üzerinden Varsa Yeni PDF İndir
    okunan_idler = okunan_idleri_yukle()
    toplam_inen = 0
    with requests.Session() as session:
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(hisse_raporlarini_indir, session, h, okunan_idler, yerel_kap_ambari): h for h in hedef_hisseler}
            for f in as_completed(futures):
                toplam_inen += f.result()
    okunan_idleri_kaydet(okunan_idler)

    # 2. AŞAMA: gelen_pdf ve arsiv_pdf Klasörlerindeki PDF'leri Eşleştir ve Oku
    pdf_not_havuzu = {}  # {hisse: {donem: notlar_dict}}
    yeni_pdf_islenen_hisseler = set()

    if fitz is not None:
        aday_pdf_yollari = []
        for f_adi in os.listdir(FM_GELEN_PDF_DIR):
            if f_adi.lower().endswith(".pdf"):
                aday_pdf_yollari.append(("gelen", f_adi, os.path.join(FM_GELEN_PDF_DIR, f_adi)))
        if tek_hisse_modu:
            for f_adi in os.listdir(FM_ARSIV_PDF_DIR):
                if f_adi.lower().endswith(".pdf"):
                    aday_pdf_yollari.append(("arsiv", f_adi, os.path.join(FM_ARSIV_PDF_DIR, f_adi)))

        for kaynak_tipi, pdf_adi, tam_yol in aday_pdf_yollari:
            try:
                doc = fitz.open(tam_yol)
                ilk_metin = " ".join([doc[i].get_text("text") for i in range(min(6, len(doc)))])
                hisse_kodu, donem = pdf_hisse_ve_donem_coz(pdf_adi, ilk_metin, mali_veritabani)
                if not hisse_kodu or (tek_hisse_modu and hisse_kodu not in set(hedef_hisseler)):
                    doc.close()
                    continue

                notlar = pdf_stratejik_bolumleri_oku(doc)
                doc.close()
                pdf_not_havuzu.setdefault(hisse_kodu, {})[donem] = notlar

                if kaynak_tipi == "gelen":
                    yeni_ad = f"{hisse_kodu}_{donem.replace('/', '_')}_{pdf_adi}" if not pdf_adi.upper().startswith(hisse_kodu + "_") else pdf_adi
                    shutil.move(tam_yol, os.path.join(FM_ARSIV_PDF_DIR, yeni_ad))
                    yeni_pdf_islenen_hisseler.add(hisse_kodu)
                    print(f"  📄 [PDF OKUNDU] {hisse_kodu} ({donem}) -> {yeni_ad}")
            except Exception as e:
                print(f"  ❌ PDF okuma hatası ({pdf_adi}): {e}")

    # 3. AŞAMA: 4-6 Dönemlik Birikimli UFRS Dipnot + PDF Sentezini Üret ve Kaydet
    bildirilen_hafiza = {}
    if os.path.exists(FM_BILDIRIM_HAFIZA_YOLU):
        try:
            with open(FM_BILDIRIM_HAFIZA_YOLU, "r", encoding="utf-8") as f:
                bildirilen_hafiza = json.load(f)
        except Exception:
            pass

    guncellenen_arsivler = {}
    bildirim_sayaci = 0

    for hisse in hedef_hisseler:
        if hisse not in mali_veritabani:
            print(f"  ⚠️ {hisse} kodu 'bist_mali_tablolar.json' içinde bulunamadı.")
            continue

        arsiv_veri = uret_temel_4_6_donem_arsivi(hisse, mali_veritabani, yerel_kap_ambari)
        donemler_dict = arsiv_veri.get("donemler", {})
        if not donemler_dict:
            continue

        # Varsa PDF'den okunan paragrafları ilgili dönemlere ekle
        for d_pdf, pdf_notlari in pdf_not_havuzu.get(hisse, {}).items():
            hedef_d = d_pdf if d_pdf in donemler_dict else sorted(donemler_dict.keys())[-1]
            mevcut_notlar = donemler_dict[hedef_d].setdefault("pdf_dipnot_notlari", {})
            for k_alan, pdf_ek_html in pdf_notlari.items():
                if pdf_ek_html:
                    ufrs_kismi = str(mevcut_notlar.get(k_alan, "")).split("<div style='margin-top:6px;")[0].strip()
                    mevcut_notlar[k_alan] = (ufrs_kismi + pdf_ek_html) if ufrs_kismi else pdf_ek_html

        sirali_d = sorted(donemler_dict.keys())
        if len(sirali_d) > MAKS_DONEM:
            for sil_d in sirali_d[:-MAKS_DONEM]:
                del donemler_dict[sil_d]
            sirali_d = sorted(donemler_dict.keys())

        son_donem = sirali_d[-1]
        hisse_masa = masa.get(hisse, {})
        son_notlar = donemler_dict[son_donem].get("pdf_dipnot_notlari", {})
        tutarlilik = uret_birikimli_karar_ve_tutarlilik(hisse, son_donem, son_notlar, arsiv_veri, hisse_masa)
        donemler_dict[son_donem]["tutarlilik_raporu"] = tutarlilik
        arsiv_veri["son_guncelleme"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 1. FAALIYET_MERKEZI/karne_hafiza içine kaydet
        fm_karne_yolu = os.path.join(FM_KARNE_HAFIZA_DIR, f"{hisse}_faaliyet_karne.json")
        with open(fm_karne_yolu, "w", encoding="utf-8") as f:
            json.dump(arsiv_veri, f, ensure_ascii=False, indent=2)

        # 2. SIRKETLER/{HISSE}/04_birikimli_dipnot_arsivi.json köprüsüne kaydet
        sirket_dir = os.path.join(SIRKETLER_DIR, hisse)
        os.makedirs(sirket_dir, exist_ok=True)
        kopru_json_yolu = os.path.join(sirket_dir, "04_birikimli_dipnot_arsivi.json")
        with open(kopru_json_yolu, "w", encoding="utf-8") as f:
            json.dump(arsiv_veri, f, ensure_ascii=False, indent=2)

        guncellenen_arsivler[hisse] = arsiv_veri

        if tek_hisse_modu:
            print(f"\n📊 [{hisse}] {len(sirali_d)} ÇEYREKLİK BİRİKİMLİ FAALİYET & DİPNOT KARAR RAPORU ({sirali_d[0]} → {son_donem}):")
            for m in tutarlilik:
                print(f"   • {re.sub(r'<[^>]+>', '', m)}")
            telegram_ozel_bildirim_at(hisse, son_donem, tutarlilik, hisse_masa)
            bildirilen_hafiza[hisse] = son_donem
        else:
            tasiniyor_mu = hisse_masa.get("mevcut_lot", 0) > 0 or hisse_masa.get("portfoyde_tasiniyor")
            if tasiniyor_mu and (hisse in yeni_pdf_islenen_hisseler or bildirilen_hafiza.get(hisse) != son_donem) and bildirim_sayaci < 12:
                telegram_ozel_bildirim_at(hisse, son_donem, tutarlilik, hisse_masa)
                bildirilen_hafiza[hisse] = son_donem
                bildirim_sayaci += 1

    try:
        with open(FM_BILDIRIM_HAFIZA_YOLU, "w", encoding="utf-8") as f:
            json.dump(bildirilen_hafiza, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    # 4. AŞAMA: index.html 6. Sekme Verisini Anında Güncelle
    if guncellenen_arsivler:
        yama_basarili = index_html_6_kart_anlik_yama(guncellenen_arsivler)
        if yama_basarili:
            print(f"\n✅ {len(guncellenen_arsivler)} şirketin 4-6 dönemlik karnesi 'FAALIYET_MERKEZI' ve 'index.html' 6. Kart içine anında işlendi!")
        elif os.path.exists(os.path.join(BASE_DIR, "kartal.py")):
            print("\n🔄 6. Kart verilerini index.html'e basmak için kartal.py çalıştırılıyor...")
            subprocess.run([sys.executable, os.path.join(BASE_DIR, "kartal.py")], cwd=BASE_DIR)

    print("=" * 72)
    print("BAĞIMSIZ FAALİYET RAPORU İŞLEMİ TAMAMLANDI!")
    print("=" * 72)


if __name__ == "__main__":
    main()
