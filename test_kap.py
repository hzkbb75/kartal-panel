import requests
import json

session = requests.Session()
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://tr.tradingview.com/"
}

print("1. TradingView BIST:THYAO Test Ediliyor...")
tv_url = "https://news-headlines.tradingview.com/v2/headlines?client=web&lang=tr&symbol=BIST%3ATHYAO"
try:
    r = session.get(tv_url, headers=headers, timeout=6)
    print(f"   HTTP Kodu: {r.status_code}")
    items = r.json().get("items", [])
    print(f"   Gelen Haber Sayısı: {len(items)}")
    if items:
        print(f"   İlk Haber Başlığı: {items[0].get('title')}")
except Exception as e:
    print(f"   TradingView Hatası: {e}")

print("\n2. Bigpara THYAO Test Ediliyor...")
bp_url = "https://bigpara.hurriyet.com.tr/api/v1/hisse/kaphaberleri/THYAO"
try:
    r2 = session.get(bp_url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://bigpara.hurriyet.com.tr/"}, timeout=6)
    print(f"   HTTP Kodu: {r2.status_code}")
    data = r2.json()
    print(f"   Gelen Veri Tipi/Uzunluğu: {len(data.get('data', [])) if isinstance(data, dict) else len(data)}")
except Exception as e:
    print(f"   Bigpara Hatası: {e}")