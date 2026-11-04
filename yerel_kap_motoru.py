import os
import json
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT_DIR = "hektor_kutuphane"
BIST_MALI_TABLOLAR_YOLU = os.path.join(ROOT_DIR, "bist_mali_tablolar.json")
CIKTI_KAP_YOLU = os.path.join(ROOT_DIR, "tum_kap_haberleri.json")

ONEMLI_KELIMELER = [
    "yeni iş", "sözleşme", "ihale", "sipariş", "temettü", 
    "kâr payı", "bedelli", "bedelsiz", "pay alım", "geri alım", 
    "özel durum", "yatırım", "birleşme", "devralma", "kap", "faaliyet"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://tr.tradingview.com/"
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
                
                # Önemli haber / KAP filtresi
                if any(k in birlesik for k in ONEMLI_KELIMELER):
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
                if len(secilenler) >= 4:
                    break
            
            # Eğer filtreye takılan çıkmazsa son 2 güncel haberi yedek olarak al
            if not secilenler and items:
                for it in items[:2]:
                    pub_ts = it.get("published")
                    pub_str = datetime.fromtimestamp(pub_ts).strftime("%d.%m.%Y") if pub_ts else datetime.now().strftime("%d.%m.%Y")
                    story_path = it.get("storyPath", "")
                    secilenler.append({
                        "id": str(it.get("id") or ""),
                        "baslik": str(it.get("title") or "")[:95],
                        "tarih": pub_str,
                        "link": f"https://www.tradingview.com{story_path}" if story_path else "https://www.kap.org.tr"
                    })

            return hisse, secilenler
    except Exception:
        pass
    return hisse, []

def main():
    hisse_listesi = tum_hisseleri_oku()
    if not hisse_listesi:
        print("❌ 'bist_mali_tablolar.json' içinde hisse bulunamadı!")
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Yerel BIST Haber & KAP Motoru Başladı...")
    print(f"  Taranacak şirket sayısı: {len(hisse_listesi)} hisse")

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
    print(f"  📊 Haber yakalanan şirket sayısı: {len(haber_ambari)} / {len(hisse_listesi)}")

    cikti = {
        "son_guncelleme": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "kap_haberleri": haber_ambari
    }

    os.makedirs(os.path.dirname(CIKTI_KAP_YOLU), exist_ok=True)
    with open(CIKTI_KAP_YOLU, "w", encoding="utf-8") as f:
        json.dump(cikti, f, ensure_ascii=False, indent=2)

    print(f"📁 Ambar kaydedildi: {CIKTI_KAP_YOLU}")

if __name__ == "__main__":
    main()