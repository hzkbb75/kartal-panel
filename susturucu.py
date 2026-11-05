import json
import os

ROOT_DIR = "hektor_kutuphane"
CIKTI_KAP_YOLU = os.path.join(ROOT_DIR, "tum_kap_haberleri.json")
STATE_FILE = "son_bildirimler.json"

def main():
    if not os.path.exists(CIKTI_KAP_YOLU):
        print("Haber dosyası bulunamadı.")
        return

    with open(CIKTI_KAP_YOLU, "r", encoding="utf-8") as f:
        haber_ambari = json.load(f).get("kap_haberleri", {})

    seen_ids = set()
    
    # Mevcut kaydedilmişleri al
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            seen_ids = set(json.load(f))

    sayac = 0
    for hisse, bildirimler in haber_ambari.items():
        for b in bildirimler:
            b_id = str(b.get("id", ""))
            if b_id and b_id not in seen_ids:
                seen_ids.add(b_id)
                sayac += 1

    # Tüm ID'leri son_bildirimler.json dosyasına yaz (Geçmişi temizle)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids)[-3000:], f, ensure_ascii=False, indent=2)

    print(f"🛑 Susturucu çalıştı! Geçmişten kalan {sayac} adet haber 'okundu' işaretlendi.")
    print("Artık sadece gerçekten YENİ gelen KAP bildirimleri telefona düşecek.")

if __name__ == "__main__":
    main()