from flask_login import UserMixin
import sqlite3
from extensions import DB_NAME, login_manager


# --- 👤 KULLANICI MODELİ ---
class User(UserMixin):
    def __init__(self, id, ad_soyad, is_admin, email, is_blocked=0, plan_type='free'):
        self.id = id
        self.ad_soyad = ad_soyad
        self.is_admin = is_admin
        self.email = email
        self.is_blocked = is_blocked
        self.plan_type = plan_type

    def get_id(self):
        return str(self.id)


@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        # En güncel tablo yapısına göre çekmeyi dene
        cursor.execute("SELECT id, ad_soyad, is_admin, email, is_blocked, plan_type FROM users WHERE id = ?",
                       (user_id,))
        u = cursor.fetchone()
    except Exception:
        # Eğer tablo henüz güncellenmemişse eski yapıyla çek
        cursor.execute("SELECT id, ad_soyad, is_admin, email FROM users WHERE id = ?", (user_id,))
        u = cursor.fetchone()

    conn.close()

    if u:
        # Veri setinin uzunluğuna göre güvenli atama yap
        is_blocked = u[4] if len(u) > 4 and u[4] is not None else 0
        plan_type = u[5] if len(u) > 5 and u[5] is not None else 'free'
        return User(id=u[0], ad_soyad=u[1], is_admin=u[2], email=u[3], is_blocked=is_blocked, plan_type=plan_type)
    return None


# --- 💰 ÖDEME AYARLARI MODELİ ---
class PaymentSettings:
    def __init__(self, id, banka_adi, iban_no, hesap_sahibi):  # 'iban' yerine 'iban_no'
        self.id = id
        self.banka_adi = banka_adi
        self.iban_no = iban_no
        self.hesap_sahibi = hesap_sahibi


def get_payment_settings():
    """Veritabanından ödeme ayarlarını çeker."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        # DİKKAT: app.py'de 'iban_no' olarak tanımladık, o yüzden buradan 'iban'ı 'iban_no' yaptık
        cursor.execute("SELECT id, banka_adi, iban_no, hesap_sahibi FROM payment_settings LIMIT 1")
        row = cursor.fetchone()
    except Exception as e:
        print(f"Sistem Uyarısı: PaymentSettings çekilirken hata (Muhtemelen sütun eksik): {e}")
        # Hata durumunda sistemin çökmemesi için fallback (boş veri) döndür
        cursor.execute("SELECT id, banka_adi FROM payment_settings LIMIT 1")
        row_small = cursor.fetchone()
        if row_small:
            row = (row_small[0], row_small[1], "", "Bilinmiyor")
        else:
            row = None

    conn.close()
    if row:
        return PaymentSettings(id=row[0], banka_adi=row[1], iban_no=row[2], hesap_sahibi=row[3])
    return None