import sqlite3

# Veritabanına bağlanıyoruz
conn = sqlite3.connect('web_mailer_v6.db')
cursor = conn.cursor()

try:
    # Sözleşme tablosunu oluştur
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS legal_texts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE,
            baslik TEXT,
            icerik TEXT
        )
    """)

    # Varsayılan metinleri ekle
    default_texts = [
        ('mesafeli_satis', 'Mesafeli Satış Sözleşmesi', '<p>Buraya panelden sözleşme eklenecek...</p>'),
        ('kullanim_kosullari', 'Kullanım Koşulları', '<p>Buraya panelden koşullar eklenecek...</p>')
    ]
    cursor.executemany("INSERT OR IGNORE INTO legal_texts (slug, baslik, icerik) VALUES (?, ?, ?)", default_texts)

    conn.commit()
    print("Mükemmel! legal_texts tablosu veritabanına eklendi.")

except Exception as e:
    print(f"Hata oluştu: {e}")

finally:
    conn.close()