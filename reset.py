import sqlite3
from werkzeug.security import generate_password_hash

conn = sqlite3.connect('web_mailer_v6.db')
cursor = conn.cursor()

email = 'kefeliserkan@gmail.com'
sifre = 'Admin12345!'
hashed_pw = generate_password_hash(sifre)

# Hesabı bul ve şifreyi/yetkiyi zorla güncelle
cursor.execute("SELECT id FROM users WHERE email=?", (email,))
user = cursor.fetchone()

if user:
    cursor.execute("UPDATE users SET password_hash=?, is_admin=1 WHERE email=?", (hashed_pw, email))
    print("✅ Admin şifresi başarıyla güncellendi!")
else:
    cursor.execute("INSERT INTO users (ad_soyad, email, password_hash, is_admin, plan_type) VALUES (?, ?, ?, 1, 'pro')", ("Serkan Kefeli", email, hashed_pw))
    print("✅ Admin hesabı sıfırdan oluşturuldu!")

conn.commit()
conn.close()