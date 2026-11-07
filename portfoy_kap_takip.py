import os
import re
import csv
import json
import time
import requests
from datetime import datetime

MAKE_WEBHOOK_URL = "https://hook.eu1.make.com/w2bw4cexf24z4huxs3nikopnofif1t1j"
ROOT_DIR = "hektor_kutuphane"
CIKTI_KAP_YOLU = os.path.join(ROOT_DIR, "tum_kap_haberleri.json")
STATE_FILE = os.path.join(ROOT_DIR, "PORTFOY", "son_bildirimler.json")
BIST_MALI_YOLU = os.path.join(ROOT_DIR, "bist_mali_tablolar.json")
DINAMIK_MASA_CSV_YOLU = os.path.join(ROOT_DIR, "PORTFOY", "portfoy_kalkani_dinamik.csv")

# 1. POZİTİF BÜYÜME/NAKİT KELİMELERİ + SERKAN ÜNAL KIRMIZI ÇİZGİ / ÇIKIŞ KELİMELERİ
FILTRE_KELIMELER = [
    # Okan Hoca Nakit & Büyüme Katalizörleri
    "yeni iş ilişkisi", "ihale", "sipariş", "sözleşme",
    "kapasite", "teşvik", "yeni yatırım", "yatırım kararı", "yatırım teşvik",
    "birleşme", "devralma", "satın alma", "stratejik ortaklık", "yeni ortaklık",
    "maddi duran varlık", "fabrika", "arsa alımı", "arsa satışı", "gayrimenkul alımı", "gayrimenkul satışı",
    "geri alım", "temettü", "kâr payı", "bedelsiz", "bedelli",
    # Serkan Ünal Kırmızı Çizgi & Çıkış Tetikleyicileri (Görev 1 İçin Zorunlu)
    "pay alım satım", "pay satış", "hisse satış", "ortaklık yapısı", "pay devri",
    "ilişkili taraf", "fon aktar", "kaynak aktar", "sermaye azalt",
    "üretim durdur", "faaliyet durdur", "fesih", "iptal", "ceza", "vergi cezası", "rekabet kurumu", "konkordato"
]

# FAALİYET RAPORU VE FİNANSAL TABLO BİLDİRİMLERİ KESİN İPTAL LİSTESİ (Ayrı modüle taşınacak)
FAALIYET_RAPORU_KESIN_IPTALLER = [
    "faaliyet raporu", "ara dönem faaliyet", "yıllık faaliyet", "entegre faaliyet",
    "entegre rapor", "finansal rapor", "finansal tablo", "bağımsız denetim",
    "sorumluluk beyanı", "sürdürülebilirlik raporu", "kurumsal yönetim"
]

# "pay alım satım bildirimi" yasağı kaldırıldı (Hâkim ortak pay satışlarını yakalamak için)
YASAKLI_KELIMELER = [
    "devre kesici", "volatilite", "likidite sağlayıcı", "olağandışı fiyat",
    "fiyat hareketleri", "işlem gören tipe dönüşüm", "kredi derecelendirme",
    "kayıtlı sermaye tavanı", "esas sözleşme", "fon sürekli bilgilendirme",
    "özel durum açıklaması (genel)",
    "görev dağılımı", "imza sirküleri", "genel kurul çağrısı", "toplantı tutanağı",
    "yatırımcı ilişkileri", "hedef fiyat", "tavsiyesi", "tavsiye",
    "geri alım endeksi", "endeks değişiklikleri", "aracı kurum", "piyasa yorumu"
] + FAALIYET_RAPORU_KESIN_IPTALLER


def tr_norm(metin: str) -> str:
    if not metin:
        return ""
    ceviri = str.maketrans("İIŞĞÜÖÇıişğüöç", "iisguociisguoc")
    return re.sub(r"\s+", " ", str(metin).translate(ceviri).lower()).strip()


def load_seen_ids():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen_ids(seen_ids):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids)[-2000:], f, ensure_ascii=False, indent=2)


def send_to_make(payload):
    if not MAKE_WEBHOOK_URL:
        return False
    try:
        res = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=15)
        return res.status_code == 200
    except Exception:
        return False


def filtreye_uyuyor_mu(baslik, ozet, item=None):
    birlesik = tr_norm(f"{baslik} {ozet}")
    # 1. Faaliyet Raporu veya Finansal Tablo bildirimi ise koşulsuz iptal et
    if any(tr_norm(fr) in birlesik for fr in FAALIYET_RAPORU_KESIN_IPTALLER):
        return False
    if item and str(item.get("bildirim_tipi", "")).upper() in {"FR", "FAR", "FINANSAL_RAPOR", "FAALIYET_RAPORU"}:
        return False
    if item and str(item.get("link", "")).lower().endswith(".pdf"):
        return False
    # 2. Genel gürültü filtresi
    if any(tr_norm(yk) in birlesik for yk in YASAKLI_KELIMELER):
        return False
    if not FILTRE_KELIMELER:
        return True
    return any(tr_norm(k) in birlesik for k in FILTRE_KELIMELER)


def portfoydeki_hisseleri_al():
    portfoy_yolu = os.path.join(ROOT_DIR, "PORTFOY", "portfoyum.csv")
    hisseler = set()

    if not os.path.exists(portfoy_yolu):
        print(f" ⚠️ Uyarı: Portföy dosyası bulunamadı ({portfoy_yolu}).")
        return hisseler

    try:
        with open(portfoy_yolu, "r", encoding="utf-8-sig") as f:
            satirlar = f.readlines()
            if len(satirlar) > 1:
                for satir in satirlar[1:]:
                    ayirici = ";" if ";" in satir else ","
                    parcalar = satir.strip().split(ayirici)
                    if parcalar and len(parcalar[0]) >= 3:
                        hisse_kodu = parcalar[0].strip().upper()
                        if hisse_kodu not in {"NAKIT", "TRY", "KASA", "PPF", "BIST", "XU100"}:
                            hisseler.add(hisse_kodu)
    except Exception as e:
        print(f" ❌ Portföy okuma hatası: {e}")

    return hisseler


def yukle_kartal_baglam_ambari():
    """
    Make.com AI Promptuna (Görev 1, 2 ve 3) tam bağlam sağlamak için
    şirketin Yıllıklandırılmış Cirosunu, EFK Marjını, F-Skorunu, Hendeğini ve
    60G Kanal / R:R verilerini yerel dosyalardan okur.
    """
    mali_sirketler = {}
    if os.path.exists(BIST_MALI_YOLU):
        try:
            with open(BIST_MALI_YOLU, "r", encoding="utf-8") as f:
                mali_sirketler = json.load(f).get("sirketler", {})
        except Exception:
            pass

    ziraat_taban = {}
    if os.path.exists("bist_analiz_tabani.json"):
        try:
            with open("bist_analiz_tabani.json", "r", encoding="utf-8") as f:
                ziraat_taban = json.load(f)
        except Exception:
            pass

    teknik_ambar = {}
    for yol in ["kap_ve_teknik.json", os.path.join(ROOT_DIR, "kap_ve_teknik.json")]:
        if os.path.exists(yol):
            try:
                with open(yol, "r", encoding="utf-8") as f:
                    teknik_ambar = json.load(f).get("teknik_veriler", {})
                    if teknik_ambar:
                        break
            except Exception:
                pass

    masa_sozluk = {}
    if os.path.exists(DINAMIK_MASA_CSV_YOLU):
        try:
            with open(DINAMIK_MASA_CSV_YOLU, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, delimiter=";")
                for r in reader:
                    k = (r.get("Kod") or "").strip().upper()
                    if k:
                        masa_sozluk[k] = r
        except Exception:
            pass

    return mali_sirketler, ziraat_taban, teknik_ambar, masa_sozluk


def uret_hisse_kartal_baglami(kod: str, mali_sirketler: dict, ziraat_taban: dict, teknik_ambar: dict, masa_sozluk: dict) -> str:
    s_kayit = mali_sirketler.get(kod, {})
    z_kayit = ziraat_taban.get(kod, {})
    t_kayit = teknik_ambar.get(kod, {})
    m_kayit = masa_sozluk.get(kod, {})

    # 1. Ciro ve EFK Marjı (Yıllıklandırılmış Ölçek Testi İçin)
    son_donem = str(s_kayit.get("son_donem") or "2026/06")
    ay_str = son_donem.split("/")[-1].strip() if "/" in son_donem else "12"
    yillik_carpan = 4.0 if ay_str in ("03", "3") else (2.0 if ay_str in ("06", "6") else (12.0 / 9.0 if ay_str in ("09", "9") else 1.0))

    satis_c = 0.0
    efk_c = 0.0
    cfo_c = 0.0
    net_c = 0.0
    for satir in s_kayit.get("satirlar_cari_onceki", []):
        ad = tr_norm(satir[0])
        val = float(satir[1] or 0.0)
        if satis_c == 0.0 and any(k in ad for k in ["hasilat", "satis gelirleri", "faiz gelirleri"]):
            satis_c = val
        if efk_c == 0.0 and ("esas faaliyet kari" in ad or ad == "faaliyet kari (zarari)"):
            efk_c = val
        if cfo_c == 0.0 and "isletme faaliyetlerinden" in ad:
            cfo_c = val
        if net_c == 0.0 and ad in ("donem net kar/zarari", "ana ortaklik paylari", "donem kari (zarari)"):
            net_c = val

    yillik_ciro_mn = satis_c * yillik_carpan
    if yillik_ciro_mn >= 1000.0:
        ciro_str = f"{yillik_ciro_mn / 1000.0:,.2f} Milyar TL (%5 Maddi Önem Eşiği: {(yillik_ciro_mn * 0.05) / 1000.0:,.2f} Milyar TL)"
    elif yillik_ciro_mn > 0:
        ciro_str = f"{yillik_ciro_mn:,.0f} Milyon TL (%5 Maddi Önem Eşiği: {yillik_ciro_mn * 0.05:,.1f} Milyon TL)"
    else:
        ciro_str = "Finans/Banka Bilançosu"

    efk_marj = round((efk_c / satis_c) * 100.0, 1) if satis_c > 0 else 0.0
    nakit_donusum = round(cfo_c / net_c, 2) if net_c > 0 else 0.0

    hendek = m_kayit.get("Hendek") or "Belirsiz"
    f_skor = m_kayit.get("F_Skor") or "-"
    aksiyon = m_kayit.get("Aksiyon") or "İZLEMEDE"
    fk = z_kayit.get("fk") or s_kayit.get("fk") or 0.0
    roe = z_kayit.get("roe") or 0.0

    if t_kayit:
        fiyat = t_kayit.get("fiyat", 0.0)
        destek = t_kayit.get("destek_60g", 0.0)
        direnc = t_kayit.get("direnc_60g", 0.0)
        konum = t_kayit.get("kanal_konumu", 50.0)
        rr = t_kayit.get("rr_orani", 1.0)
        teknik_str = f"Fiyat: {fiyat} TL | 60G Kanal Konumu: %{konum} (60G Destek: {destek} TL, 60G Direnç: {direnc} TL) | Asimetrik R:R: 1:{rr}"
    else:
        teknik_str = "60G Kanal verisi bekleniyor"

    return (
        f"[KARTAL BİLANÇO & 60G R:R VERİSİ -> Yıllıklandırılmış Ciro: {ciro_str} | "
        f"EFK Marjı: %{efk_marj} | İNA/Net Kâr: {nakit_donusum}x | Hendek: {hendek} | "
        f"F-Skor: {f_skor}/9 | ROE: %{roe} | F/K: {fk}x | {teknik_str} | "
        f"KARTAL Masa Aksiyonu: {aksiyon}]"
    )


def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Make.com KAP Taraması Başladı (SADECE PORTFÖY - BİLANÇO & 60G R:R DESTEKLİ).")
    if not os.path.exists(CIKTI_KAP_YOLU):
        print(f" ⚠️ Uyarı: {CIKTI_KAP_YOLU} bulunamadı.")
        return

    try:
        with open(CIKTI_KAP_YOLU, "r", encoding="utf-8") as f:
            haber_ambari = json.load(f).get("kap_haberleri", {})
    except json.JSONDecodeError:
        print(f" ❌ Hata: {CIKTI_KAP_YOLU} dosyası bozuk veya boş!")
        return

    portfoy_hisseleri = portfoydeki_hisseleri_al()
    if not portfoy_hisseleri:
        print(" ⚠️ Portföy listesi boş veya okunamadı. Tarama güvenlik amacıyla iptal edildi.")
        return

    mali_sirketler, ziraat_taban, teknik_ambar, masa_sozluk = yukle_kartal_baglam_ambari()
    seen_ids = load_seen_ids()
    yeni_sayac = 0
    MAX_MESAJ = 20

    for hisse, bildirimler in haber_ambari.items():
        if hisse not in portfoy_hisseleri:
            continue

        for item in bildirimler:
            if yeni_sayac >= MAX_MESAJ:
                break

            bildirim_id = str(item.get("id") or "")
            if not bildirim_id or bildirim_id in seen_ids:
                continue

            baslik = item.get("baslik") or item.get("title") or "KAP Bildirimi"
            ham_ozet = str(item.get("ozet") or item.get("summary") or baslik).strip()

            if filtreye_uyuyor_mu(baslik, ham_ozet, item):
                link = item.get("link") or f"https://www.kap.org.tr/tr/Bildirim/{bildirim_id}"
                kartal_baglam = uret_hisse_kartal_baglami(hisse, mali_sirketler, ziraat_taban, teknik_ambar, masa_sozluk)
                zengin_ozet = f"{ham_ozet} — {kartal_baglam}"

                payload = {
                    "sembol": hisse,
                    "baslik": baslik,
                    "zaman": item.get("tarih", ""),
                    "kap_link": link,
                    "ozet": zengin_ozet
                }

                print(f" [PORTFÖY KRİTİK HABER] {hisse}: {baslik}")
                basarili = send_to_make(payload)
                if not basarili:
                    print(" ⚠️ Uyarı: Make.com'a iletilemedi (Webhook reddetti veya limit doldu).")

                time.sleep(4)
                seen_ids.add(bildirim_id)
                yeni_sayac += 1
            else:
                seen_ids.add(bildirim_id)

        if yeni_sayac >= MAX_MESAJ:
            print("🛑 Spam koruması devreye girdi. Kalanlar sonraki taramaya bırakıldı.")
            break

    save_seen_ids(seen_ids)


if __name__ == "__main__":
    main()
