import requests

# Flask projenin çalıştığı lokal adres (Canlıya alınca burası gerçek domain olacak)
url = "http://127.0.0.1:5000/api/send"

# Dashboard'dan kopyaladığın kendi gizli anahtarın
api_key = "98915e026746f86ab907beee55e9c4d3c949011c6945f4fb"

headers = {
    "X-API-KEY": api_key,
    "Content-Type": "application/json"
}

# EstCrm'den geldiğini varsaydığımız fatura veya bilgilendirme verisi
payload = {
    "to": ["test_icin_bir_mail@gmail.com"], # Kendi test mailini yaz
    "subject": "EST CRM'den Otomatik Mesaj",
    "body": "<h1>Merhaba {isim}</h1><p>Bu e-posta EstCrm üzerinden REST API kullanılarak otomatik fırlatılmıştır!</p>"
}

# İsteği yolla!
print("API'ye istek gönderiliyor...")
response = requests.post(url, headers=headers, json=payload)

# Sonucu ekrana yazdır
print(f"Sunucu Yanıt Kodu: {response.status_code}")
print(f"Sunucu Mesajı: {response.json()}")