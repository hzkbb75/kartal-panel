import os
import json
import requests
from datetime import datetime

MAKE_WEBHOOK_URL = "https://hook.eu1.make.com/w2bw4cexf24z4huxs3nikopnofif1t1j"
# Yeni Hali (Burayla Değiştirin)
ROOT_DIR = "hektor_kutuphane"
CIKTI_KAP_YOLU = os.path.join(ROOT_DIR, "tum_kap_haberleri.json") 
# son_bildirimler.json dosyası PORTFOY klasörünün içinde olduğu için yolu güncelledik:
STATE_FILE = os.path.join(ROOT_DIR, "PORTFOY", "son_bildirimler.json")

# Yeni Hali (Burayla Değiştirin)
FILTRE_KELIMELER = [
    "yeni iş ilişkisi", "ihale", "sipariş", "sözleşme", 
    "kapasite", "teşvik", "yeni yatırım", "yatırım kararı", "yatırım teşvik", 
    "birleşme", "devralma", "satın alma", "stratejik ortaklık", "yeni ortaklık",
    "maddi duran varlık", "fabrika", "arsa alımı", "arsa satışı", "gayrimenkul alımı", "gayrimenkul satışı",
    "geri alım", "temettü", "kâr payı", "bedelsiz", "bedelli"
    # "faaliyet raporu" BURADAN SİLİNDİ
]

YASAKLI_KELIMELER = [
    "devre kesici", "volatilite", "likidite sağlayıcı", "olağandışı fiyat", 
    "fiyat hareketleri", "işlem gören tipe dönüşüm", "kredi derecelendirme", 
    "kayıtlı sermaye tavanı", "esas sözleşme", "fon sürekli bilgilendirme",
    "sorumluluk beyanı", "özel durum açıklaması (genel)", 
    "görev dağılımı", "imza sirküleri", "genel kurul çağrısı", "toplantı tutanağı",
    "pay alım satım bildirimi", "yatırımcı ilişkileri",
    "hedef fiyat", "tavsiyesi", "tavsiye", "geri alım endeksi", "endeks değişiklikleri", "aracı kurum", "piyasa yorumu",
    "faaliyet raporu" # YASAKLILARA EKLENDİ (Make.com'a gitmemesi için)
]

def load_seen_ids():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen_ids(seen_ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids)[-2000:], f, ensure_ascii=False, indent=2)

def send_to_make(payload):
    if not MAKE_WEBHOOK_URL:
        return False
    try:
        res = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=15)
        return res.status_code == 200
    except Exception as e:
        return False

def filtreye_uyuyor_mu(baslik, ozet):
    if not FILTRE_KELIMELER:
        return True
    birlesik = f"{baslik} {ozet}".lower()
    if any(yk in birlesik for yk in YASAKLI_KELIMELER):
        return False
    return any(k in birlesik for k in FILTRE_KELIMELER)

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Make.com KAP Taraması Başladı.")
    if not os.path.exists(CIKTI_KAP_YOLU):
        print(f"  ⚠️ Uyarı: {CIKTI_KAP_YOLU} bulunamadı.")
        return

    try:
        with open(CIKTI_KAP_YOLU, "r", encoding="utf-8") as f:
            haber_ambari = json.load(f).get("kap_haberleri", {})
    except json.JSONDecodeError:
        print(f"  ❌ Hata: {CIKTI_KAP_YOLU} dosyası bozuk veya boş!")
        return

    seen_ids = load_seen_ids()
    yeni_sayac = 0
    MAX_MESAJ = 5 

    for hisse, bildirimler in haber_ambari.items():
        for item in bildirimler:
            if yeni_sayac >= MAX_MESAJ:
                break 

            bildirim_id = str(item.get("id") or "")
            if not bildirim_id or bildirim_id in seen_ids:
                continue

            baslik = item.get("baslik") or item.get("title") or "KAP Bildirimi"
            ozet = str(item.get("ozet") or item.get("summary") or baslik).strip()
            
            if filtreye_uyuyor_mu(baslik, ozet):
                link = item.get("link") or f"https://www.kap.org.tr/tr/Bildirim/{bildirim_id}"
                payload = {"sembol": hisse, "baslik": baslik, "zaman": item.get("tarih", ""), "kap_link": link, "ozet": ozet}
                
                print(f"  [KRİTİK BİLDİRİM] {hisse}: {baslik}")
                basarili = send_to_make(payload)
                if not basarili:
                    print("    ⚠️ Uyarı: Make.com'a iletilemedi (Webhook reddetti veya limit doldu).")
                
                # ÖNEMLİ: Gönderim başarısız olsa bile sayacı artır ki sistem sonsuz döngüden çıksın!
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