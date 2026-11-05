import os
import json
import time
import requests
from datetime import datetime

# ==============================================================================
# KAP FAALİYET RAPORU (PDF) OTOMATİK İNDİRİCİ
# ==============================================================================
PROJE_DIZINI = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(PROJE_DIZINI, "hektor_kutuphane")
GELEN_RAPOR_DIR = os.path.join(ROOT_DIR, "PORTFOY", "gelen_raporlar")
BIST_MALI_TABLOLAR_YOLU = os.path.join(ROOT_DIR, "bist_mali_tablolar.json")

os.makedirs(GELEN_RAPOR_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
    "Accept": "application/json, text/plain, */*"
}

def sirket_listesi_al():
    if os.path.exists(BIST_MALI_TABLOLAR_YOLU):
        try:
            with open(BIST_MALI_TABLOLAR_YOLU, "r", encoding="utf-8") as f:
                vt = json.load(f)
                # Sadece halka açıklığı uygun veya radarında olanları taramak hızlandırır
                return list(vt.get("sirketler", {}).keys())
        except Exception:
            pass
    return ["EREGL", "FROTO", "TUPRS", "THYAO", "BIMAS"] # Örnek liste

def kap_faaliyet_raporu_bul_ve_indir(session, hisse):
    print(f"  🔍 {hisse} için son KAP bildirimleri taranıyor...")
    
    # Son 1 yıllık bildirimleri çekmek için tarih aralığı
    bugun = datetime.now()
    gecen_yil = f"{bugun.year - 1}-{bugun.strftime('%m-%d')}"
    bugun_str = bugun.strftime('%Y-%m-%d')
    
    # KAP bildirim arama URL'si
    url = "https://www.kap.org.tr/tr/api/memberDisclosureQuery"
    payload = {
        "memberList": [hisse],
        "disclosureClass": "FR", # Sadece Faaliyet Raporları (FR)
        "fromDate": gecen_yil,
        "toDate": bugun_str
    }
    
    try:
        res = session.post(url, json=payload, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            bildirimler = res.json()
            indirilen_sayisi = 0
            
            for b in bildirimler:
                ozet = b.get("disclosureIndex", {}).get("summary", "").lower()
                bildirim_id = b.get("disclosureIndex", {}).get("disclosureIndex")
                
                # Gerçekten faaliyet raporu mu kontrol et (Sorumluluk beyanlarını ele)
                if "faaliyet raporu" in ozet and "sorumluluk beyanı" not in ozet and bildirim_id:
                    # Rapor dönemini bul (Örn: 2026_06)
                    yil_m = b.get("disclosureIndex", {}).get("year", bugun.year)
                    donem_m = b.get("disclosureIndex", {}).get("period", "03") # 3, 6, 9, 12 aylık
                    donem_str = str(donem_m).zfill(2)
                    
                    dosya_adi = f"{hisse}_{yil_m}_{donem_str}_Faaliyet_Raporu.pdf"
                    hedef_yol = os.path.join(GELEN_RAPOR_DIR, dosya_adi)
                    
                    # Eğer bu PDF zaten indirilmişse veya arşivdeyse atla
                    arsiv_yolu = os.path.join(ROOT_DIR, "PORTFOY", "arsiv_raporlar", dosya_adi)
                    if os.path.exists(hedef_yol) or os.path.exists(arsiv_yolu):
                        continue
                        
                    # KAP'tan PDF indirme URL'si (attachment)
                    ekler = b.get("disclosureIndex", {}).get("attachmentIndices", [])
                    if not ekler:
                        continue
                        
                    # Genelde ilk ek PDF'tir
                    pdf_id = ekler[0].get("disclosureIndex")
                    if pdf_id:
                        pdf_url = f"https://www.kap.org.tr/tr/BildirimPdf/{bildirim_id}"
                        print(f"    📥 {dosya_adi} indiriliyor...")
                        
                        pdf_res = session.get(pdf_url, headers=HEADERS, timeout=30)
                        if pdf_res.status_code == 200 and len(pdf_res.content) > 50000: # 50KB'dan büyükse geçerlidir
                            with open(hedef_yol, "wb") as f:
                                f.write(pdf_res.content)
                            indirilen_sayisi += 1
                            time.sleep(1) # Sunucuyu yormamak için bekleme
                            
            return indirilen_sayisi
    except Exception as e:
        print(f"    ❌ Hata ({hisse}): {e}")
    return 0

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] OTOMATİK KAP PDF İNDİRİCİ BAŞLADI")
    hisseler = sirket_listesi_al()
    toplam_indirilen = 0
    
    with requests.Session() as session:
        # Önemli: İlk etapta tüm listeyi (600 hisse) taramak çok uzun sürebilir.
        # Bu yüzden ilk testte sadece ilgilendiğin / portföyündeki hisseleri tarayabilirsin.
        # İstersen hisseler = ["EREGL", "TUPRS", "FROTO", "BIMAS"] gibi filtrele.
        
        for hisse in hisse_listesi[:20]: # Test amaçlı ilk 20 hisseyi tara. İstersen kaldır.
            adet = kap_faaliyet_raporu_bul_ve_indir(session, hisse)
            toplam_indirilen += adet
            time.sleep(0.5)
            
    print("="*50)
    print(f"İşlem tamam. Toplam {toplam_indirilen} yeni faaliyet raporu indirildi.")
    print("="*50)

if __name__ == "__main__":
    main()