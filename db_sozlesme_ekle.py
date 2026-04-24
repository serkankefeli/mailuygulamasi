import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()
# Uygulamanın kullandığı gerçek DB yolunu extensions'tan al (instance/ içinde).
from extensions import DB_NAME

print(f"Veritabanı yolu: {DB_NAME}")
conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

try:
    # Sözleşme metinlerini tutacak tabloyu yaratıyoruz
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS legal_texts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE,
            baslik TEXT,
            icerik TEXT
        )
    """)

    # Sistemin çökmemesi için içine varsayılan (örnek) metinleri ekleyelim
    default_texts = [
        ('mesafeli_satis', 'Mesafeli Satış Sözleşmesi',
         '<p>Buraya admin panelinden mesafeli satış sözleşmesi metni eklenecek...</p>'),
        ('kullanim_kosullari', 'Kullanım Koşulları',
         '<p>Buraya admin panelinden kullanım koşulları metni eklenecek...</p>')
    ]

    # IGNORE komutu sayesinde kodu iki kere çalıştırsan bile hata vermez, olanı ezmez
    cursor.executemany("INSERT OR IGNORE INTO legal_texts (slug, baslik, icerik) VALUES (?, ?, ?)", default_texts)

    conn.commit()
    print("Harika! legal_texts tablosu başarıyla oluşturuldu ve içi dolduruldu.")

except Exception as e:
    print(f"Bir hata oluştu: {e}")

finally:
    conn.close()