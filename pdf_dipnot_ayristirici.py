import os
import re
import json
import shutil
from datetime import datetime

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

# ==============================================================================
# KLASÖR YAPISI VE DİNAMİK TEMİZ HAVUZ MOTORU
# ==============================================================================
ROOT_DIR = "hektor_kutuphane"
PORTFOY_DIR = os.path.join(ROOT_DIR, "PORTFOY")
SIRKETLER_DIR = os.path.join(ROOT_DIR, "SIRKETLER")
GELEN_RAPOR_DIR = os.path.join(PORTFOY_DIR, "gelen_raporlar")
ARSIV_RAPOR_DIR = os.path.join(PORTFOY_DIR, "arsiv_raporlar")
BIST_MALI_TABLOLAR_YOLU = os.path.join(ROOT_DIR, "bist_mali_tablolar.json")
TEMIZ_HAVUZ_TXT_YOLU = os.path.join(PORTFOY_DIR, "temiz_havuz_kodlari.txt")
TEMIZ_HAVUZ_JSON_YOLU = os.path.join(PORTFOY_DIR, "temiz_havuz_kodlari.json")

os.makedirs(GELEN_RAPOR_DIR, exist_ok=True)
os.makedirs(ARSIV_RAPOR_DIR, exist_ok=True)
os.makedirs(SIRKETLER_DIR, exist_ok=True)


def tr_norm(metin: str) -> str:
    if not metin:
        return ""
    ceviri = str.maketrans("İIŞĞÜÖÇıişğüöç", "iisguociisguoc")
    return re.sub(r"\s+", " ", metin.translate(ceviri).lower()).strip()


# Özel takma adlar (Unvanı borsa kodundan çok farklı olanlar için ek kökler)
OZEL_KOKLER = {
    "ASTOR": ["astor enerji", "astor"],
    "PETKM": ["petkim", "socar"],
    "ASELS": ["aselsan", "asels"],
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
    "SISE":  ["turkiye sise ve cam", "sisecam"],
    "ENKAI": ["enka insaat"],
    "PGSUS": ["pegasus hava"],
    "TAVHL": ["tav havalimanlari", "tav holding"]
}


def dinamik_temiz_havuz_yukle() -> dict:
    """
    bist_mali_tablolar.json dosyasından Bölüm 1 süzgecini geçen tüm şirketleri
    dinamik olarak yükler ve unvan kökleriyle eşleştirir.
    """
    havuz = {}
    if not os.path.exists(BIST_MALI_TABLOLAR_YOLU):
        print(f"  [Uyarı] '{BIST_MALI_TABLOLAR_YOLU}' bulunamadı, varsayılan liste kullanılıyor.")
        return OZEL_KOKLER

    try:
        with open(BIST_MALI_TABLOLAR_YOLU, "r", encoding="utf-8") as f:
            vt = json.load(f).get("sirketler", {})

        for kod, veri in vt.items():
            halka_aciklik = float(veri.get("halka_aciklik", 35.0))
            if halka_aciklik > 70.0:
                continue

            sirket_adi = veri.get("sirket_adi", kod)
            kokler = [kod, sirket_adi]
            if kod in OZEL_KOKLER:
                kokler.extend(OZEL_KOKLER[kod])
            
            temiz_unvan = re.sub(r"\b(a\.s\.|as|sanayi|ve|ticaret|holding|pazarlama|isletmeleri)\b", "", tr_norm(sirket_adi)).strip()
            if len(temiz_unvan) >= 4:
                kokler.append(temiz_unvan)

            havuz[kod] = list(set(kokler))

        # KAP botu için temiz havuz kod listesini dışa aktar
        kod_listesi = sorted(list(havuz.keys()))
        with open(TEMIZ_HAVUZ_TXT_YOLU, "w", encoding="utf-8") as f:
            f.write("\n".join(kod_listesi))
        with open(TEMIZ_HAVUZ_JSON_YOLU, "w", encoding="utf-8") as f:
            json.dump(kod_listesi, f, indent=2)

    except Exception as e:
        print(f"  [Hata] Dinamik havuz yüklenirken sorun oluştu: {e}")
        return OZEL_KOKLER

    return havuz


TEMIZ_HAVUZ_SIRKETLERI = dinamik_temiz_havuz_yukle()


def hisse_ve_donem_tespit_et(dosya_adi: str, ilk_sayfalar: str) -> tuple:
    """Temiz havuzdaki tüm şirketlerle dosya adı ve metin eşleştirmesi yapar."""
    ad_temiz = re.sub(r"^(\d{8}_\d{4}_)+", "", dosya_adi)
    ad_norm = tr_norm(ad_temiz)
    metin_norm = tr_norm(ilk_sayfalar)

    tespit_hisse = None

    # 1. Dosya adındaki kelimelerden (token) doğrudan borsa kodu tespiti
    dosya_tokenleri = set(re.split(r"[^a-z0-9]+", ad_norm))
    for kod in TEMIZ_HAVUZ_SIRKETLERI.keys():
        if kod.lower() in dosya_tokenleri:
            tespit_hisse = kod
            break

    # 2. Dosya adı içinde şirket unvanı/kökü kontrolü
    if not tespit_hisse:
        for kod, kokler in TEMIZ_HAVUZ_SIRKETLERI.items():
            for kok in kokler:
                if len(kok) >= 4 and tr_norm(kok) in ad_norm:
                    tespit_hisse = kod
                    break
            if tespit_hisse:
                break

    # 3. Metin içi kesin unvan kontrolü (İlk 6 sayfa)
    if not tespit_hisse:
        for kod, kokler in TEMIZ_HAVUZ_SIRKETLERI.items():
            for kok in kokler:
                if len(kok) >= 5 and tr_norm(kok) in metin_norm:
                    tespit_hisse = kod
                    break
            if tespit_hisse:
                break

    if not tespit_hisse:
        return None, None

    # Dönem Çözücü
    yil_m = re.search(r"(202[3-7])", ad_norm + " " + metin_norm)
    yil = yil_m.group(1) if yil_m else datetime.now().strftime("%Y")

    bilesik = ad_norm + " " + metin_norm
    if any(k in bilesik for k in ["1. ceyrek", "1 ceyrek", "q1", "c1", "ç1", "3 aylik", "31.03"]):
        donem = f"{yil}/03"
    elif any(k in bilesik for k in ["2. ceyrek", "2 ceyrek", "q2", "c2", "ç2", "6 aylik", "30.06"]):
        donem = f"{yil}/06"
    elif any(k in bilesik for k in ["3. ceyrek", "3 ceyrek", "q3", "c3", "ç3", "9 aylik", "30.09"]):
        donem = f"{yil}/09"
    elif any(k in bilesik for k in ["4. ceyrek", "entegre", "yillik", "12 aylik", "31.12"]):
        donem = f"{yil}/12"
    else:
        donem = f"{yil}/03"

    return tespit_hisse, donem


def icra_kurulu_bolumlerini_ayikla(doc) -> dict:
    """Rapordaki 4 stratejik alanı arar ve yönetici brifingi paragraflarını seçer."""
    arama_kaliplari = {
        "yatirim_ve_kapasite": ["yatirim harcamalari", "devam eden yatirimlar", "kapasite artisi", "fabrika yatirimi", "yeni tesis", "ges yatirimi", "tesis yatirimi"],
        "siparis_ve_backlog": ["bakiye siparis", "alinan siparisler", "backlog", "siparis miktari", "sozlesme bedeli", "yurt disi siparis"],
        "doviz_ve_turev": ["yabanci para pozisyonu", "kur riski", "turev finansal araclar", "forward", "para swap", "hedge"],
        "tri_rehin_ipotek": ["taahhut rehin ipotek", "verilen teminatlar", "ipotek", "kefaletler", "rehin"]
    }

    bulgular = {k: [] for k in arama_kaliplari}

    for sayfa_idx in range(len(doc)):
        metin = doc[sayfa_idx].get_text("text")
        if not metin or len(metin.strip()) < 80:
            continue

        satirlar = [s.strip() for s in metin.splitlines() if len(s.strip()) > 0]
        govde = " ".join(satirlar[2:-2]) if len(satirlar) > 6 else " ".join(satirlar)
        norm_govde = tr_norm(govde)

        for kat, anahtarlar in arama_kaliplari.items():
            for kelime in anahtarlar:
                pos = norm_govde.find(kelime)
                if pos != -1:
                    baslangic = max(0, pos - 30)
                    bitis = min(len(govde), pos + 320)
                    paragraf = re.sub(r"\s+", " ", govde[baslangic:bitis].strip())
                    bulgular[kat].append(f"[Sayfa {sayfa_idx + 1}] ...{paragraf}...")
                    break

    sunum_notlari = {}
    for kat, ornekler in bulgular.items():
        if ornekler:
            sunum_notlari[kat] = " ".join(ornekler[:2])
        else:
            sunum_notlari[kat] = "Bu dönemde olağandışı bir risk veya kritik sapma beyan edilmemiştir."

    return sunum_notlari


def tutarlilik_karnesi_uret(hisse: str, donem: str, yeni_veriler: dict, mevcut_arsiv: dict) -> list:
    """Serkan Ünal Bölüm 2: 'Yönetim geçmişte söylediklerini ne ölçüde yaptı?' denetimi."""
    karneler = []
    donemler = mevcut_arsiv.get("donemler", {})
    gecmis_donemler = sorted([d for d in donemler.keys() if d < donem])

    if not gecmis_donemler:
        karneler.append(f"📌 {donem} dönemi {hisse} için başlangıç baz dönemi olarak kaydedildi. Gelecek çeyrekte vaat-gerçekleşme karşılaştırması başlayacak.")
        return karneler

    onceki = gecmis_donemler[-1]

    # Yatırım Takvimi Denetimi
    yatirim_metni = yeni_veriler.get("yatirim_ve_kapasite", "").lower()
    if any(k in yatirim_metni for k in ["ertelendi", "gecikme", "revize edildi", "aksama"]):
        karneler.append(f"⚠️ YATIRIM GECİKMESİ: {donem} raporunda devreye alma takviminde revizyon tespit edildi ({onceki} hedefleriyle kıyaslanmalı).")
    else:
        karneler.append(f"🟢 YATIRIM DİSİPLİNİ: Kapasite yatırımları ve tesis montajları {onceki} planına sadık ilerliyor.")

    # Sipariş & Backlog İvmesi
    siparis_metni = yeni_veriler.get("siparis_ve_backlog", "").lower()
    if "siparis" in siparis_metni:
        karneler.append(f"🟢 SİPARİŞ & BACKLOG GÖRÜNÜRLÜĞÜ: Şirket teslimat takvimini ve bakiye iş hacmini şeffaf şekilde bildirmeye devam ediyor.")
    else:
        karneler.append("ℹ️ SİPARİŞ DETAYI: Bakiye siparişler rutin tablolarda yer alıyor; ana dipnotlar takip edilmeli.")

    # Döviz ve Hedge Güvenliği
    doviz_metni = yeni_veriler.get("doviz_ve_turev", "").lower()
    if "acik pozisyon" in doviz_metni and "hedge" not in doviz_metni and "turev" not in doviz_metni:
        karneler.append("🚨 KUR RİSKİ SAPMASI: Net yabancı para açık pozisyonunda korunmasız kur riski artışı sinyali!")
    else:
        karneler.append("🟢 KUR RİSKİ KORUMASI: Yabancı para pozisyonu doğal ihracat dengesi ve türev araçlarla koruma altında.")

    return karneler


def main():
    if fitz is None:
        print("HATA: PyMuPDF yüklü değil. Terminalde 'pip install pymupdf' çalıştırın.")
        return

    print("=" * 68)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] KARTAL GENİŞLETİLMİŞ FAALİYET RAPORU MOTORU (TEMİZ HAVUZ 408+)")
    print(f"  Tanımlı Temiz Havuz Kapsamı: {len(TEMIZ_HAVUZ_SIRKETLERI)} BIST Şirketi")
    print("=" * 68)

    dosyalar = [f for f in os.listdir(GELEN_RAPOR_DIR) if f.lower().endswith(".pdf")]
    if not dosyalar:
        print(f"Bilgi: '{GELEN_RAPOR_DIR}' klasöründe taranacak yeni PDF raporu yok.")
        print(f"Temiz havuz kod listesi güncellendi -> '{TEMIZ_HAVUZ_TXT_YOLU}'")
        return

    print(f"İşlenecek yeni dosya sayısı: {len(dosyalar)}")

    for dosya in dosyalar:
        yol = os.path.join(GELEN_RAPOR_DIR, dosya)
        try:
            doc = fitz.open(yol)
            ilk_metin = " ".join([doc[i].get_text("text") for i in range(min(6, len(doc)))])

            hisse, donem = hisse_ve_donem_tespit_et(dosya, ilk_metin)

            if not hisse:
                print(f"  [Atlandı] Dosya temiz havuz şirketleriyle eşleşmedi: {dosya}")
                doc.close()
                continue

            print(f"\n▶ İŞLENİYOR: {hisse} | Dönem: {donem} | Dosya: {dosya}")

            sunum_notlari = icra_kurulu_bolumlerini_ayikla(doc)
            doc.close()

            sirket_klasor = os.path.join(SIRKETLER_DIR, hisse)
            os.makedirs(sirket_klasor, exist_ok=True)
            arsiv_dosyasi = os.path.join(sirket_klasor, "04_birikimli_dipnot_arsivi.json")

            mevcut_arsiv = {"kod": hisse, "son_guncelleme": "", "donemler": {}}
            if os.path.exists(arsiv_dosyasi):
                try:
                    with open(arsiv_dosyasi, "r", encoding="utf-8") as f:
                        mevcut_arsiv = json.load(f)
                except Exception:
                    pass

            tutarlilik_raporu = tutarlilik_karnesi_uret(hisse, donem, sunum_notlari, mevcut_arsiv)

            if donem not in mevcut_arsiv.get("donemler", {}):
                mevcut_arsiv.setdefault("donemler", {})[donem] = {
                    "efm_yuzde": 0.0, "icr_kati": 3.0, "tahakkuk_orani": 0.0, "aga_orani": 0.0,
                    "ykp_orani": 0.0, "dso_gun": 0.0, "acm_makasi": 0.0, "serefiye_ozk_orani": 0.0,
                    "karsilik_ozk_orani": 0.0, "iliskili_orani": 0.0, "dts_skor": 0, "dts_delta": 0,
                    "uyarilar": []
                }

            mevcut_arsiv["donemler"][donem]["pdf_dipnot_notlari"] = sunum_notlari
            mevcut_arsiv["donemler"][donem]["tutarlilik_raporu"] = tutarlilik_raporu
            mevcut_arsiv["son_guncelleme"] = datetime.now().strftime("%Y-%m-%d %H:%M")

            with open(arsiv_dosyasi, "w", encoding="utf-8") as f:
                json.dump(mevcut_arsiv, f, ensure_ascii=False, indent=2)

            hedef_ad = f"{hisse}_{donem.replace('/', '_')}_Faaliyet_Raporu.pdf"
            hedef_yol = os.path.join(ARSIV_RAPOR_DIR, hedef_ad)
            shutil.move(yol, hedef_yol)

            print(f"  ✅ {hisse} ({donem}) faaliyet raporu hafızaya işlendi.")
            print(f"  📁 Dosya '{hedef_ad}' olarak arşive kaldırıldı.")

        except Exception as e:
            print(f"  ❌ Hata ({dosya}): {e}")

    print("\n" + "=" * 68)
    print("FAALİYET RAPORU İŞLEME DÖNGÜSÜ TAMAMLANDI!")
    print("=" * 68)


if __name__ == "__main__":
    main()