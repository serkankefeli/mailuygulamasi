from flask import Flask, url_for as flask_url_for
import os
from datetime import timedelta
from dotenv import load_dotenv

# Köprülerimiz
from extensions import csrf, login_manager, limiter

# Parçaladığımız Modüller (Blueprints)
from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.main import main_bp
from routes.mail import mail_bp

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('MAILKAMP_SECRET_KEY')
if not app.secret_key:
    raise RuntimeError("HATA: MAILKAMP_SECRET_KEY .env dosyasında bulunamadı!")

app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Güvenlik Ayarları
_ENV = os.environ.get('MAILKAMP_ENV', os.environ.get('FLASK_ENV', 'development')).lower()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=(_ENV == 'production'),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE='Lax',
    REMEMBER_COOKIE_SECURE=(_ENV == 'production'),
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
)

# Eklentileri Başlat
csrf.init_app(app)
login_manager.init_app(app)
limiter.init_app(app)

# Modülleri Sisteme Tanıt
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(main_bp)
app.register_blueprint(mail_bp)


# --- SİHİRLİ JİNJA KÖPRÜSÜ (HTML dosyalarının çökmesini engeller) ---
def custom_url_for(endpoint, **values):
    mapping = {
        'index': 'main.index', 'login': 'auth.login', 'logout': 'auth.logout',
        'register': 'auth.register', 'forgot_password': 'auth.forgot_password',
        'reset_password': 'auth.reset_password', 'profile': 'auth.profile',
        'admin_login': 'auth.admin_login', 'verify_2fa': 'auth.verify_2fa',

        'dashboard': 'main.dashboard', 'reports': 'main.reports', 'settings_page': 'main.settings_page',
        'contacts': 'main.contacts', 'add_group': 'main.add_group', 'delete_group': 'main.delete_group',
        'save_settings': 'main.save_settings', 'add_blacklist': 'main.add_blacklist',
        'remove_blacklist': 'main.remove_blacklist', 'upload_logo': 'main.upload_logo',
        'upgrade': 'main.upgrade', 'save_template': 'main.save_template',
        'get_template': 'main.get_template', 'delete_template': 'main.delete_template',
        'serve_uploads': 'main.serve_uploads',

        'admin_users': 'admin.admin_users', 'admin_site_settings': 'admin.admin_site_settings',
        'admin_legal_edit': 'admin.admin_legal_edit', 'payment_management': 'admin.payment_management',
        'approve_upgrade': 'admin.approve_upgrade', 'reject_upgrade': 'admin.reject_upgrade',
        'toggle_role': 'admin.toggle_role', 'toggle_block': 'admin.toggle_block', 'delete_user': 'admin.delete_user',

        'track': 'mail.track', 'track_open': 'mail.track_open', 'export_logs': 'mail.export_logs',
        'import_contacts': 'mail.import_contacts', 'api_send': 'mail.api_send',
        'generate_api_key': 'mail.generate_api_key', 'unsubscribe': 'mail.unsubscribe', 'send_mail': 'mail.send_mail'
    }
    return flask_url_for(mapping.get(endpoint, endpoint), **values)


app.jinja_env.globals['url_for'] = custom_url_for


# ------------------------------------------------------------------

def init_db():
    import sqlite3
    from werkzeug.security import generate_password_hash
    import secrets
    from extensions import DB_NAME

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''CREATE TABLE IF NOT EXISTS users
                      (
                          id
                          INTEGER
                          PRIMARY
                          KEY
                          AUTOINCREMENT,
                          ad_soyad
                          TEXT,
                          email
                          TEXT
                          UNIQUE,
                          password_hash
                          TEXT,
                          is_admin
                          INTEGER
                          DEFAULT
                          0,
                          auth_code
                          TEXT,
                          is_blocked
                          INTEGER
                          DEFAULT
                          0,
                          api_key
                          TEXT,
                          plan_type
                          TEXT
                          DEFAULT
                          'free',
                          sent_this_month
                          INTEGER
                          DEFAULT
                          0,
                          contract_accepted
                          INTEGER
                          DEFAULT
                          0,
                          contract_accepted_date
                          TEXT
                      )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS logs
                      (
                          id
                          INTEGER
                          PRIMARY
                          KEY
                          AUTOINCREMENT,
                          user_id
                          INTEGER,
                          tarih
                          TEXT,
                          alici
                          TEXT,
                          konu
                          TEXT,
                          durum
                          TEXT,
                          detay
                          TEXT,
                          okundu
                          INTEGER
                          DEFAULT
                          0,
                          okunma_tarihi
                          TEXT
                      )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS blacklist
    (
        id
        INTEGER
        PRIMARY
        KEY
        AUTOINCREMENT,
        user_id
        INTEGER,
        email
        TEXT,
        UNIQUE
                      (
        user_id,
        email
                      ))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings
                      (
                          id
                          INTEGER
                          PRIMARY
                          KEY
                          AUTOINCREMENT,
                          user_id
                          INTEGER
                          UNIQUE,
                          host
                          TEXT,
                          port
                          TEXT,
                          user_email
                          TEXT,
                          password
                          TEXT,
                          webhook_url
                          TEXT
                      )''')
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, email TEXT, UNIQUE(user_id, email))")
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, group_name TEXT, UNIQUE(user_id, group_name))")
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS contact_group_rel (contact_id INTEGER, group_id INTEGER, UNIQUE(contact_id, group_id))")
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS templates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, template_name TEXT, subject TEXT, body TEXT)")
    cursor.execute('''CREATE TABLE IF NOT EXISTS payment_settings
                      (
                          id
                          INTEGER
                          PRIMARY
                          KEY
                          AUTOINCREMENT,
                          active_methods
                          TEXT
                          DEFAULT
                          'havale',
                          iban_no
                          TEXT,
                          banka_adi
                          TEXT,
                          hesap_sahibi
                          TEXT,
                          pro_price
                          REAL
                          DEFAULT
                          150.00,
                          paytr_id
                          TEXT,
                          paytr_key
                          TEXT,
                          iyzico_api_key
                          TEXT,
                          iyzico_secret_key
                          TEXT
                      )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS upgrade_requests
                      (
                          id
                          INTEGER
                          PRIMARY
                          KEY
                          AUTOINCREMENT,
                          user_id
                          INTEGER,
                          talep_tarihi
                          TEXT,
                          odeme_metodu
                          TEXT,
                          durum
                          TEXT
                          DEFAULT
                          'beklemede',
                          notlar
                          TEXT
                      )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS landing_settings
                      (
                          id
                          INTEGER
                          PRIMARY
                          KEY
                          AUTOINCREMENT,
                          hero_title
                          TEXT,
                          hero_subtitle
                          TEXT,
                          f1_title
                          TEXT,
                          f1_desc
                          TEXT,
                          f2_title
                          TEXT,
                          f2_desc
                          TEXT,
                          f3_title
                          TEXT,
                          f3_desc
                          TEXT,
                          footer_text
                          TEXT,
                          ga_id
                          TEXT,
                          looker_url
                          TEXT,
                          hero_image
                          TEXT,
                          promo_video
                          TEXT
                      )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS legal_texts
                      (
                          id
                          INTEGER
                          PRIMARY
                          KEY
                          AUTOINCREMENT,
                          slug
                          TEXT
                          UNIQUE,
                          baslik
                          TEXT,
                          icerik
                          TEXT
                      )''')

    cursor.execute("SELECT id FROM landing_settings")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO landing_settings (hero_title, hero_subtitle, f1_title, f1_desc, f2_title, f2_desc, f3_title, f3_desc, footer_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("Müşterilerinize Ulaşmanın En Akıllı Yolu",
             "Mailkamp güvencesiyle e-posta kampanyalarınızı saniyeler içinde tasarlayın, gönderin ve sonuçları analiz edin.",
             "Detaylı Analitik", "Hangi müşterinizin maili açtığını, raporlayın.", "Akıllı Şablonlar",
             "En iyi tasarımlarınızı şablon olarak kaydedin.", "Güvenli Altyapı",
             "Spam filtrelerine takılmadan hızlı teslimat.", "© 2026 Mailkamp."))

    cursor.execute("SELECT id FROM legal_texts")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO legal_texts (slug, baslik, icerik) VALUES (?, ?, ?)",
                       ('satis-sozlesmesi', 'Mesafeli Satış Sözleşmesi', 'Sözleşme içeriği buraya eklenecektir.'))
        cursor.execute("INSERT INTO legal_texts (slug, baslik, icerik) VALUES (?, ?, ?)",
                       ('kullanim-kosullari', 'Kullanım Koşulları', 'Kullanım koşulları buraya eklenecektir.'))

    cursor.execute("SELECT id FROM payment_settings")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO payment_settings (iban_no, banka_adi, hesap_sahibi) VALUES (?, ?, ?)",
                       ("TR00 0000 0000 0000 0000 0000 00", "Mailkamp Bank", "Serkan Kefeli"))

    cursor.execute("SELECT id FROM users WHERE email = ?", ("kefeliserkan@gmail.com",))
    if not cursor.fetchone():
        ilk_sifre = os.environ.get('ADMIN_INITIAL_PASSWORD')
        if not ilk_sifre:
            ilk_sifre = secrets.token_hex(8)
        hashed_pw = generate_password_hash(ilk_sifre)
        api_key = secrets.token_hex(24)
        cursor.execute(
            "INSERT INTO users (ad_soyad, email, password_hash, is_admin, plan_type, api_key) VALUES (?, ?, ?, 1, 'pro', ?)",
            ("Serkan Kefeli", "kefeliserkan@gmail.com", hashed_pw, api_key))

    cursor.execute("DELETE FROM users WHERE email = 'admin@sistem.com'")
    conn.commit()
    conn.close()


if __name__ == '__main__':
    init_db()
    app.run(debug=False)