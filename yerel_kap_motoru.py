import os
import json
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT_DIR = "hektor_kutuphane"
BIST_MALI_TABLOLAR_YOLU = os.path.join(ROOT_DIR, "bist_mali_tablolar.json")
CIKTI_KAP_YOLU = os.path.join(ROOT_DIR, "tum_kap_haberleri.json")
GELEN_RAPOR_DIR = os.path.join(ROOT_DIR, "PORTFOY", "gelen_raporlar")

os.makedirs(GELEN_RAPOR_DIR, exist_ok=True)

ONEMLI_KELIMELER = [
    "yeni iş ilişkisi", "ihale", "sipariş", "sözleşme", 
    "kapasite", "teşvik", "yeni yatırım", "yatırım kararı", "yatırım teşvik", 
    "birleşme", "devralma", "satın alma", "stratejik ortaklık", "yeni ortaklık",
    "maddi duran varlık", "fabrika", "arsa alımı", "arsa satışı", "gayrimenkul alımı", "gayrimenkul satışı",
    "geri alım", "temettü", "kâr payı", "bedelsiz", "bedelli",
    "faaliyet raporu"
]

YASAKLI_KELIMELER = [
    "devre kesici", "volatilite", "likidite sağlayıcı", "olağandışı fiyat", 
    "fiyat hareketleri", "işlem gören tipe dönüşüm", "kredi derecelendirme", 
    "kayıtlı sermaye tavanı", "esas sözleşme", "fon sürekli bilgilendirme",
    "sorumluluk beyanı", "özel durum açıklaması (genel)", 
    "görev dağılımı", "imza sirküleri", "genel kurul çağrısı", "toplantı tutanağı",
    "pay alım satım bildirimi", "hedef fiyat", "tavsiyesi", "tavsiye", "aracı kurum", "piyasa yorumu"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://tr.tradingview.com/"
}

# KAP sunucusuna PDF çekmek için daha profesyonel bir header (bot zannetmemesi için)
KAP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/pdf"
}

def tum_hisseleri_oku() -> list:
    if os.path.exists(BIST_MALI_TABLOLAR_YOLU):
        try:
            with open(BIST_MALI_TABLOLAR_YOLU, "r", encoding="utf-8") as f:
                vt = json.load(f)
                kodlar = sorted(list(vt.get("sirketler", {}).keys()))
                if kodlar:
                    return kodlar
        except Exception:
            pass
    return []

def faaliyet_raporu_pdf_indir(session, hisse, bildirim_id, pub_ts):
    """TradingView'da tespit edilen faaliyet raporunun PDF'ini KAP'tan (arka kapı) zorla indirir."""
    # Tarihi 2026_06 gibi formata çevirelim (veya varsayılan olarak şu anki yılı alalım)
    yil = datetime.fromtimestamp(pub_ts).strftime("%Y") if pub_ts else datetime.now().strftime("%Y")
    
    # Basit isimlendirme
    dosya_adi = f"{hisse}_{yil}_00_Faaliyet_Raporu.pdf" 
    hedef_yol = os.path.join(GELEN_RAPOR_DIR, dosya_adi)
    arsiv_yolu = os.path.join(ROOT_DIR, "PORTFOY", "arsiv_raporlar", dosya_adi)

    # Zaten indirilmişse veya arşivdeyse tekrar indirme
    if os.path.exists(hedef_yol) or os.path.exists(arsiv_yolu):
        return

    pdf_url = f"https://www.kap.org.tr/tr/BildirimPdf/{bildirim_id}"
    print(f"    📥 [ARKA KAPI] {hisse} Faaliyet Raporu KAP'tan direkt indiriliyor...")
    
    try:
        res = session.get(pdf_url, headers=KAP_HEADERS, timeout=45)
        if res.status_code == 200 and len(res.content) > 50000: # 50KB'dan büyükse başarılıdır
            with open(hedef_yol, "wb") as f:
                f.write(res.content)
    except Exception as e:
        pass # Sessizce atla, panik yapmasın

def tek_hisse_tradingview_cek(session, hisse: str) -> tuple:
    url = f"https://news-headlines.tradingview.com/v2/headlines?client=web&lang=tr&symbol=BIST%3A{hisse}"
    try:
        r = session.get(url, headers=HEADERS, timeout=6)
        if r.status_code == 200:
            items = r.json().get("items", [])
            secilenler = []
            for it in items:
                baslik = str(it.get("title") or "")
                ozet = str(it.get("shortDescription") or baslik)
                birlesik = f"{baslik} {ozet}".lower()
                
                if any(k in birlesik for k in ONEMLI_KELIMELER) and not any(yk in birlesik for yk in YASAKLI_KELIMELER):
                    b_id = str(it.get("id") or "")
                    pub_ts = it.get("published")
                    pub_str = datetime.fromtimestamp(pub_ts).strftime("%d.%m.%Y") if pub_ts else datetime.now().strftime("%d.%m.%Y")
                    story_path = it.get("storyPath", "")
                    link = f"https://www.tradingview.com{story_path}" if story_path else f"https://www.kap.org.tr/tr/Bildirim/{b_id}"
                    
                    secilenler.append({
                        "id": b_id,
                        "baslik": baslik[:95],
                        "tarih": pub_str,
                        "link": link
                    })

                    # --- ARKA KAPI: EĞER BU HABER BİR FAALİYET RAPORUYSA, HEMEN PDF'İNİ İNDİR! ---
                    if "faaliyet raporu" in birlesik and b_id and not story_path:
                        faaliyet_raporu_pdf_indir(session, hisse, b_id, pub_ts)

                if len(secilenler) >= 4:
                    break
            
            return hisse, secilenler
    except Exception:
        pass
    return hisse, []

def main():
    hisse_listesi = tum_hisseleri_oku()
    if not hisse_listesi:
        print("❌ 'bist_mali_tablolar.json' içinde hisse bulunamadı!")
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Yerel BIST Haber & KAP Motoru (PDF HIZLANDIRICILI) Başladı...")
    t_basla = time.time()
    haber_ambari = {}

    with requests.Session() as session:
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(tek_hisse_tradingview_cek, session, h) for h in hisse_listesi]
            for f in as_completed(futures):
                hisse, haberler = f.result()
                if haberler:
                    haber_ambari[hisse] = haberler

    gecen_sure = round(time.time() - t_basla, 1)
    print(f"  ✅ Tarama tamamlandı: {gecen_sure} saniye sürdü.")
    
    cikti = {
        "son_guncelleme": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "kap_haberleri": haber_ambari
    }
    with open(CIKTI_KAP_YOLU, "w", encoding="utf-8") as f:
        json.dump(cikti, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()