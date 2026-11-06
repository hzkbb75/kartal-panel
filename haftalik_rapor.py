import os
import requests

# Kendi bilgilerinizi buraya girin (kartal_bot.py içindeki ile aynı)
TELEGRAM_BOT_TOKEN = "8843188278:AAH26HlS26ECK1_DZ0AVVfCpq8c_vCaKAnQ"
TELEGRAM_CHAT_ID = "932085366" 

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
portfoy_csv_yolu = os.path.join(BASE_DIR, "portfoyum.csv")

mesaj = "🦅 *KARTAL HAFTALIK KAPANIŞ RAPORU*\n\n"

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