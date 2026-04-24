from flask_login import UserMixin
import sqlite3
from extensions import DB_NAME, login_manager

class User(UserMixin):
    def __init__(self, id, ad_soyad, is_admin, email, is_blocked=0, plan_type='free'):
        self.id = id
        self.ad_soyad = ad_soyad
        self.is_admin = is_admin
        self.email = email
        self.is_blocked = is_blocked
        self.plan_type = plan_type

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, ad_soyad, is_admin, email, is_blocked, plan_type FROM users WHERE id = ?", (user_id,))
    except Exception:
        cursor.execute("SELECT id, ad_soyad, is_admin, email FROM users WHERE id = ?", (user_id,))
    u = cursor.fetchone()
    conn.close()
    if u:
        is_blocked = u[4] if len(u) > 4 and u[4] is not None else 0
        plan_type = u[5] if len(u) > 5 and u[5] is not None else 'free'
        return User(id=u[0], ad_soyad=u[1], is_admin=u[2], email=u[3], is_blocked=is_blocked, plan_type=plan_type)
    return None

class PaymentSettings:
    def __init__(self, id, banka_adi, iban, hesap_sahibi):
        self.id = id
        self.banka_adi = banka_adi
        self.iban = iban
        self.hesap_sahibi = hesap_sahibi


def get_payment_settings():
    """Veritabanından ödeme ayarlarını çeker."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        # Tabloda hesap_sahibi kolonu olduğunu varsayıyoruz
        cursor.execute("SELECT id, banka_adi, iban, hesap_sahibi FROM payment_settings LIMIT 1")
        row = cursor.fetchone()
    except Exception:
        # Eğer henüz kolon eklenmemişse hata vermemesi için eski haliyle çek
        cursor.execute("SELECT id, banka_adi, iban FROM payment_settings LIMIT 1")
        row = cursor.fetchone()
        if row:
            # hesap_sahibi'ni geçici olarak boş döndür
            row = (row[0], row[1], row[2], "Bilinmiyor")

    conn.close()
    if row:
        return PaymentSettings(id=row[0], banka_adi=row[1], iban=row[2], hesap_sahibi=row[3])
    return None