import os
import json
import requests
from datetime import datetime

# ==========================================
# AYARLAR VE ÇEVRE DEĞİŞKENLERİ
# ==========================================
MAKE_WEBHOOK_URL = "https://hook.eu1.make.com/w2bw4cexf24z4huxs3nikopnofif1t1j"

ROOT_DIR = "hektor_kutuphane"
CIKTI_KAP_YOLU = os.path.join(ROOT_DIR, "tum_kap_haberleri.json")
STATE_FILE = "son_bildirimler.json"

# === 1. ALTIN KELİMELER (SADECE HİKAYEYİ DEĞİŞTİRECEK HABERLER) ===
FILTRE_KELIMELER = [
    # Büyüme & Operasyonel İvme
    "yeni iş ilişkisi", "ihale", "sipariş", "sözleşme", 
    "kapasite", "teşvik", "yeni yatırım", "yatırım kararı", "yatırım teşvik", 
    # Şirket Yapısı & Varlıklar
    "birleşme", "devralma", "satın alma", "ortaklık",
    "maddi duran varlık", "fabrika", "arsa", "gayrimenkul",
    # Finansal Aksiyonlar
    "geri alım", "temettü", "kâr payı", "bedelsiz", "bedelli",
    # Sadece okumak isteyeceğin temel rapor
    "faaliyet raporu"
]

# === 2. GİYOTİN FİLTRESİ (ÇÖPE ATILACAK RUTİN KAP KİRLİLİĞİ) ===
YASAKLI_KELIMELER = [
    # Borsa & SPK Otomatik Rutinleri
    "devre kesici", "volatilite", "likidite sağlayıcı", "olağandışı fiyat", 
    "fiyat hareketleri", "işlem gören tipe dönüşüm", "kredi derecelendirme", 
    "kayıtlı sermaye tavanı", "esas sözleşme", "fon sürekli bilgilendirme",
    # Bilanço Gönderim Çöpleri (Sayıları zaten KARTAL hesaplıyor)
    "sorumluluk beyanı", "özel durum açıklaması (genel)", 
    # Günlük / Yönetimsel Rutinler
    "görev dağılımı", "imza sirküleri", "genel kurul çağrısı", "toplantı tutanağı",
    "pay alım satım bildirimi", # Patronun/yöneticinin ufak şahsi alım satımlarını eler (Şirketin kendi "geri alım"ı filtreye takılmaz)
    # Haber/Medya Çöpleri
    "hedef fiyat", "tavsiyesi", "tavsiye", "aracı kurum", "piyasa yorumu"
]
def load_seen_ids():
    """Daha önce işlenmiş bildirim ID'lerini yükler."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen_ids(seen_ids):
    """İşlenen bildirim ID'lerini kaydeder (Son 2000 ID)."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids)[-2000:], f, ensure_ascii=False, indent=2)

def send_to_make(payload):
    """Bildirimi Make.com Webhook'una iletir."""
    if not MAKE_WEBHOOK_URL:
        return False
    try:
        res = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=15)
        return res.status_code == 200
    except Exception as e:
        print(f"Make.com iletim hatası: {e}")
        return False

def filtreye_uyuyor_mu(baslik, ozet):
    """Başlık veya özet metninde kritik kelime var mı ve yasaklı kelime yok mu kontrol eder."""
    if not FILTRE_KELIMELER:
        return True
    
    birlesik = f"{baslik} {ozet}".lower()
    
    # Yasaklı (dedikodu/tavsiye) kelime varsa anında elenir
    if any(yk in birlesik for yk in YASAKLI_KELIMELER):
        return False
        
    return any(k in birlesik for k in FILTRE_KELIMELER)

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Yerel JSON üzerinden KAP Taraması başlatıldı.")

    if not os.path.exists(CIKTI_KAP_YOLU):
        print(f"HATA: {CIKTI_KAP_YOLU} bulunamadı. Lütfen önce yerel_kap_motoru.py'yi çalıştırın.")
        return

    try:
        with open(CIKTI_KAP_YOLU, "r", encoding="utf-8") as f:
            kap_verisi = json.load(f)
            haber_ambari = kap_verisi.get("kap_haberleri", {})
    except Exception as e:
        print(f"JSON okuma hatası: {e}")
        return

    seen_ids = load_seen_ids()
    yeni_sayac = 0

    for hisse, bildirimler in haber_ambari.items():
        for item in bildirimler:
            bildirim_id = str(item.get("id") or "")
            baslik = item.get("baslik") or item.get("title") or "KAP Bildirimi"
            zaman = item.get("tarih") or item.get("date") or datetime.now().strftime("%d.%m.%Y")
            ozet = str(item.get("ozet") or item.get("summary") or baslik).strip()
            link = item.get("link") or f"https://www.kap.org.tr/tr/Bildirim/{bildirim_id}"

            if not bildirim_id or bildirim_id in seen_ids:
                continue

            # Haberleri filtrele
            if filtreye_uyuyor_mu(baslik, ozet):
                payload = {
                    "sembol": hisse,
                    "baslik": baslik,
                    "zaman": zaman,
                    "kap_link": link,
                    "ozet": ozet,
                }
                print(f"  [KRİTİK BİLDİRİM] {hisse}: {baslik}")
                basarili = send_to_make(payload)
                if basarili:
                    seen_ids.add(bildirim_id)
                    yeni_sayac += 1
            else:
                # Filtreye uymasa da tekrar kontrol etmemek için ID'yi kaydet
                seen_ids.add(bildirim_id)

    save_seen_ids(seen_ids)
    print(f"Tarama tamamlandı. {yeni_sayac} adet kritik bildirim Make.com'a aktarıldı.")

if __name__ == "__main__":
    main()