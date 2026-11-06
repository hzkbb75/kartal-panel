import os
import requests

# 1. ÖNCE KLASÖR YOLLARI TANIMLANMALI
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
portfoy_csv_yolu = os.path.join(BASE_DIR, "hektor_kutuphane", "PORTFOY", "portfoyum.csv")

# 2. SONRA ŞİFRELER OKUNMALI
try:
    with open(os.path.join(BASE_DIR, "token.txt"), "r") as f:
        TELEGRAM_BOT_TOKEN = f.read().strip()
    with open(os.path.join(BASE_DIR, "chat_id.txt"), "r") as f:
        TELEGRAM_CHAT_ID = f.read().strip()
except FileNotFoundError:
    print("HATA: token.txt veya chat_id.txt bulunamadı!")
    TELEGRAM_BOT_TOKEN = ""
    TELEGRAM_CHAT_ID = ""

# 3. İŞLEMLER BAŞLAMALI
mesaj = "🦅 *KARTAL HAFTALIK KAPANIŞ RAPORU*\n\n"

# ... (Kodun geri kalanı mevcut haliyle devam edebilir)

try:
    if os.path.exists(portfoy_csv_yolu):
        with open(portfoy_csv_yolu, "r", encoding="utf-8") as f:
            satirlar = f.readlines()
            if satirlar:
                son_durum = satirlar[-1].strip()
                mesaj += f"💼 *Güncel Kasa ve Portföy:*\n{son_durum}\n\n"
    else:
        mesaj += "⚠️ portfoyum.csv bulunamadı.\n\n"
except Exception as e:
    mesaj += f"⚠️ Veri okunamadı: {e}\n\n"

mesaj += "Haftalık taramalar tamamlandı. Panelinizi inceleyebilirsiniz:\n"
mesaj += "🔗 [KARTAL HTML Panel](https://hzkbb75.github.io/kartal-panel/)"

url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": "Markdown"}

try:
    requests.post(url, json=payload, timeout=10)
    print("Haftalık rapor başarıyla gönderildi.")
except Exception as e:
    print(f"Rapor gönderilemedi: {e}")