import os
import re
import json

HTML_YOLU = "ANA_KAPSAM_DEFTERI.html"
JSON_YOLU = os.path.join("hektor_kutuphane", "bist_mali_tablolar.json")

with open(JSON_YOLU, "r", encoding="utf-8") as f:
    vt = json.load(f).get("sirketler", {})

with open(HTML_YOLU, "r", encoding="utf-8") as f:
    html = f.read()

def satir_donustur(match):
    row = match.group(0)
    kod_m = re.search(r'<strong[^>]*>([A-Z0-9]+)</strong>', row)
    if not kod_m:
        return row
    kod = kod_m.group(1)
    
    gecti = "GEÇTİ" in row or 'data-gecti="1"' in row
    sirket = vt.get(kod, {})
    fk = float(sirket.get("fk", 0.0))
    pd = float(sirket.get("piyasa_degeri_milyar", 0.0))

    fk_metin = f'<strong style="color:#38bdf8;">{fk:.1f}x</strong>' if fk > 0 else '<span style="color:#64748b;">-</span>'
    
    # 9. sütundaki (Bölüm 1 Durumu) GEÇTİ / ELENDİ hücresini gerçek F/K ile değiştir
    row_new = re.sub(
        r'<td><span class="badge-[^"]+">(?:GEÇTİ|KIRMIZI ÇİZGİ ELENDİ)</span></td>',
        f'<td>{fk_metin}</td>',
        row
    )
    # tr etiketine JS motorunun aradığı dataset özelliklerini işle
    row_new = re.sub(
        r'<tr[^>]*>',
        f'<tr data-kod="{kod}" data-gecti="{1 if gecti else 0}" data-fk="{fk}" data-pd="{pd}">',
        row_new,
        count=1
    )
    return row_new

# 1. Tablo başlığını güncelle
html = html.replace('<th>Bölüm 1 Durumu</th>', '<th>Cari F/K</th>')

# 2. Tüm 630 satırı dönüştür
html = re.sub(r'<tr[^>]*>[\s\S]*?</tr>', satir_donustur, html)

with open(HTML_YOLU, "w", encoding="utf-8") as f:
    f.write(html)

print("ANA_KAPSAM_DEFTERI.html içinde 630 hissenin Cari F/K ve dataset değerleri başarıyla güncellendi!")