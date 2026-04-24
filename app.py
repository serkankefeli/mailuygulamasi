from flask import Flask, url_for as flask_url_for, session, request, redirect
import os
from datetime import timedelta
from dotenv import load_dotenv

# Eklentilerimiz
from extensions import csrf, login_manager, limiter

# Modüllerimiz (Blueprints)
from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.main import main_bp
from routes.mail import mail_bp

load_dotenv()

app = Flask(__name__)

# GÜVENLİK: Gizli Anahtar
app.secret_key = os.environ.get('MAILKAMP_SECRET_KEY',
                                '9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b')

# Dosya Yükleme Ayarı — absolute path, container'da /app/uploads volume'e denk gelir
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.config['UPLOAD_FOLDER'] = os.environ.get(
    'MAILKAMP_UPLOAD_FOLDER',
    '/app/uploads' if os.path.isdir('/app/uploads') else os.path.join(_BASE_DIR, 'uploads')
)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Mailler içindeki tracking/unsubscribe linklerinde kullanılacak public URL.
# Sunucuda mutlaka ayarla! Örnek: https://mailkamp.senindomain.com
# Ayarlanmazsa request.host_url kullanılır (reverse proxy arkasında yanlış olabilir).
app.config['PUBLIC_BASE_URL'] = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')

# Oturum Güvenliği
_ENV = os.environ.get('MAILKAMP_ENV', 'development').lower()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=(_ENV == 'production'),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
)

# Eklentileri Sisteme Bağla
csrf.init_app(app)
login_manager.init_app(app)
limiter.init_app(app)

# Blueprint Kayıtları
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(main_bp)
app.register_blueprint(mail_bp)


# --- 🛡️ GLOBAL GÜVENLİK DUVARI (2FA KAÇAKLARINI ÖNLER) ---
@app.before_request
def enforce_2fa():
    # Statik dosyalar, çıkış ve login her zaman erişilebilir olmalı
    if request.path.startswith('/static') or request.endpoint in ['auth.logout', 'auth.login', 'auth.verify_2fa']:
        return

    # Eğer kullanıcı 2FA bekleme odasındaysa, Dashboard veya diğer sayfalara erişimi engelle
    if 'temp_user_email' in session:
        return redirect(flask_url_for('auth.verify_2fa'))


# --- 🔗 SİHİRLİ JİNJA KÖPRÜSÜ (Tüm Hataları Önleyen Full Liste) ---
def custom_url_for(endpoint, **values):
    mapping = {
        # Auth (Kimlik) Modülü
        'index': 'main.index',
        'login': 'auth.login',
        'logout': 'auth.logout',
        'register': 'auth.register',
        'verify_2fa': 'auth.verify_2fa',
        'profile': 'auth.profile',
        'gizli-kapi': 'auth.admin_login',
        'forgot_password': 'auth.forgot_password',
        'reset_password': 'auth.reset_password',


        # Main (Panel) Modülü
        'dashboard': 'main.dashboard',
        'contacts': 'main.contacts',
        'settings_page': 'main.settings_page',
        'reports': 'main.reports',
        'upgrade': 'main.upgrade',
        'add_group': 'main.add_group',
        'delete_group': 'main.delete_group',
        'save_settings': 'main.save_settings',
        'add_blacklist': 'main.add_blacklist',
        'remove_blacklist': 'main.remove_blacklist',
        'upload_logo': 'main.upload_logo',
        'save_template': 'main.save_template',
        'get_template': 'main.get_template',
        'delete_template': 'main.delete_template',
        'serve_uploads': 'main.serve_uploads',


        # Mail (Gönderim ve Takip) Modülü
        'send_mail': 'mail.send_mail',
        'import_contacts': 'mail.import_contacts',
        'export_logs': 'mail.export_logs',
        'track': 'mail.track',
        'track_open': 'mail.track_open',
        'api_send': 'mail.api_send',
        'generate_api_key': 'mail.generate_api_key',
        'unsubscribe': 'mail.unsubscribe',

        # Admin Modülü (Eğer admin.py içinde varsa)
        'admin_users': 'admin.admin_users',
        'admin_site_settings': 'admin.admin_site_settings',
        'admin_legal_edit': 'admin.admin_legal_edit',
        'payment_management': 'admin.payment_management',
        'approve_upgrade': 'admin.approve_upgrade',
        'reject_upgrade': 'admin.reject_upgrade',
        'toggle_role': 'admin.toggle_role',
        'toggle_block': 'admin.toggle_block',
        'delete_user': 'admin.delete_user'
    }

    # Eğer aranan link listede varsa Blueprint versiyonunu döndür, yoksa olduğu gibi bırak
    target = mapping.get(endpoint, endpoint)
    return flask_url_for(target, **values)


# Köprüyü Jinja'ya Tanıt
app.jinja_env.globals['url_for'] = custom_url_for

# --- 🗄️ VERİTABANI İLKLENDİRME ---
def init_db():
    import sqlite3
    import os
    import shutil
    from werkzeug.security import generate_password_hash
    from extensions import DB_NAME

    # 🔄 ONE-TIME MIGRATION: Eski konumdaki (proje kökü) DB'yi yeni konumuna (instance/) taşı.
    # Böylece lokal geliştirme verisi kaybolmaz ve aynı kod hem lokalde hem sunucuda çalışır.
    _legacy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web_mailer_v6.db')
    if _legacy_path != DB_NAME and os.path.exists(_legacy_path) and not os.path.exists(DB_NAME):
        try:
            os.makedirs(os.path.dirname(DB_NAME), exist_ok=True)
            shutil.move(_legacy_path, DB_NAME)
            print(f"[init_db] Eski veritabanı {_legacy_path} -> {DB_NAME} konumuna taşındı.")
        except Exception as _e:
            print(f"[init_db] UYARI: Eski DB taşınamadı: {_e}")

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 🚀 WAL mode: Gunicorn'un birden fazla worker'ı aynı anda okuyup yazabilsin diye.
    # "database is locked" hatalarını çok azaltır. Tek seferlik ayardır (DB'ye yazılır).
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass

    # 1. TEMEL TABLOLARI OLUŞTUR (Sistem Çökmemesi İçin Zorunlu)
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, ad_soyad TEXT, email TEXT UNIQUE, password_hash TEXT, is_admin INTEGER DEFAULT 0, is_blocked INTEGER DEFAULT 0, plan_type TEXT DEFAULT 'free', auth_code TEXT, api_key TEXT, contract_accepted INTEGER DEFAULT 0, contract_accepted_date TEXT, sent_this_month INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, host TEXT, port TEXT, user_email TEXT, password TEXT, webhook_url TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, group_name TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, email TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS contact_group_rel (id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id INTEGER, group_id INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, tarih TEXT, alici TEXT, konu TEXT, durum TEXT, detay TEXT, okundu INTEGER DEFAULT 0, okunma_tarihi TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS templates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, template_name TEXT, subject TEXT, body TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS blacklist (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, email TEXT, UNIQUE(user_id, email))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS legal_texts (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE, baslik TEXT, icerik TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS landing_settings(id INTEGER PRIMARY KEY AUTOINCREMENT,hero_title TEXT, hero_subtitle TEXT, hero_image TEXT, f1_title TEXT, f1_desc TEXT, f2_title TEXT, f2_desc TEXT, f3_title TEXT, f3_desc TEXT, footer_text TEXT, ga_id TEXT, looker_url TEXT, promo_video TEXT)''')
    # payment_settings — route ve template'in beklediği tam şema.
    # Template pozisyonel index kullandığı için kolon SIRASI kritiktir:
    # [0]id [1]active_methods [2]iban_no [3]banka_adi [4]aylik_ucret(legacy)
    # [5]pro_price [6]paytr_id [7]paytr_key [8]iyzico_api_key [9]iyzico_secret_key [10]hesap_sahibi
    cursor.execute('''CREATE TABLE IF NOT EXISTS payment_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        active_methods TEXT DEFAULT 'havale',
        iban_no TEXT DEFAULT '',
        banka_adi TEXT DEFAULT '',
        aylik_ucret REAL DEFAULT 499.0,
        pro_price REAL DEFAULT 499.0,
        paytr_id TEXT DEFAULT '',
        paytr_key TEXT DEFAULT '',
        iyzico_api_key TEXT DEFAULT '',
        iyzico_secret_key TEXT DEFAULT '',
        hesap_sahibi TEXT DEFAULT ''
    )''')

    # Schema migration: eski tablo (banka_adi, iban, hesap_sahibi, aylik_ucret, yillik_ucret)
    # varsa route'un beklediği yeni şemayla değiştir. Eski tablo sadece 1 default satır
    # içeriyordu, kullanıcı verisi yok — DROP/RECREATE güvenli.
    try:
        cursor.execute("PRAGMA table_info(payment_settings)")
        _pay_cols = [row[1] for row in cursor.fetchall()]
        if 'active_methods' not in _pay_cols:
            cursor.execute("DROP TABLE payment_settings")
            cursor.execute('''CREATE TABLE payment_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                active_methods TEXT DEFAULT 'havale',
                iban_no TEXT DEFAULT '',
                banka_adi TEXT DEFAULT '',
                aylik_ucret REAL DEFAULT 499.0,
                pro_price REAL DEFAULT 499.0,
                paytr_id TEXT DEFAULT '',
                paytr_key TEXT DEFAULT '',
                iyzico_api_key TEXT DEFAULT '',
                iyzico_secret_key TEXT DEFAULT '',
                hesap_sahibi TEXT DEFAULT ''
            )''')
    except sqlite3.OperationalError:
        # Paralel worker migration'ı zaten yapmış olabilir — sessizce geç.
        pass
    cursor.execute('''CREATE TABLE IF NOT EXISTS upgrade_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, talep_tarihi TEXT, odeme_metodu TEXT, durum TEXT DEFAULT 'Bekliyor')''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS landing_settings (id INTEGER PRIMARY KEY AUTOINCREMENT, hero_title TEXT, hero_subtitle TEXT, features_json TEXT)''')

    # 2. ADMİN KULLANICISINI OLUŞTUR (Race-safe: INSERT OR IGNORE)
    # Gunicorn'un birden fazla worker'ı aynı anda init_db çalıştırdığında
    # eski check-then-insert kalıbı "UNIQUE constraint failed" hatası veriyordu.
    # users.email UNIQUE olduğu için INSERT OR IGNORE atomik olarak güvenli.
    try:
        ilk_sifre = os.environ.get('ADMIN_INITIAL_PASSWORD', 'Admin12345!')
        hashed_pw = generate_password_hash(ilk_sifre)
        cursor.execute(
            "INSERT OR IGNORE INTO users (ad_soyad, email, password_hash, is_admin, plan_type) VALUES (?, ?, ?, 1, 'pro')",
            ("Serkan Kefeli", "kefeliserkan@gmail.com", hashed_pw))
    except sqlite3.IntegrityError:
        pass

    # 3. YENİ EKLENEN SÖZLEŞMELERİ VE AYARLARI DOLDUR (Boşsa)
    # legal_texts.slug UNIQUE olduğu için INSERT OR IGNORE güvenli.
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO legal_texts (slug, baslik, icerik) VALUES (?, ?, ?)",
            ('kullanim-kosullari', 'Kullanım Koşulları', '<p>Sistem kullanım koşulları...</p>'))
        cursor.execute(
            "INSERT OR IGNORE INTO legal_texts (slug, baslik, icerik) VALUES (?, ?, ?)",
            ('mesafeli-satis-sozlesmesi', 'Mesafeli Satış Sözleşmesi', '<p>Satış sözleşmesi...</p>'))
    except sqlite3.IntegrityError:
        pass

    # payment_settings default satırı — route UPDATE ... WHERE id=1 yaptığı için
    # id=1'de bir satır bulunması şart. INSERT OR IGNORE atomik olarak race-safe.
    try:
        cursor.execute(
            """INSERT OR IGNORE INTO payment_settings
               (id, active_methods, iban_no, banka_adi, aylik_ucret, pro_price,
                paytr_id, paytr_key, iyzico_api_key, iyzico_secret_key, hesap_sahibi)
               VALUES (1, 'havale', '', '', 499.0, 499.0, '', '', '', '', '')""")
    except sqlite3.IntegrityError:
        pass

    try:
        conn.commit()
    except sqlite3.IntegrityError:
        # Paralel worker zaten commit ettiyse sessizce geç.
        pass
    finally:
        conn.close()


# Reverse proxy (nginx/traefik) arkasındayken HTTPS ve gerçek IP'yi doğru görmek için.
# Özellikle production'da SESSION_COOKIE_SECURE ve rate limiter'ın düzgün çalışması için kritik.
if _ENV == 'production':
    try:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    except Exception:
        pass


# app.py dosyasının sonu
with app.app_context():
    init_db()

if __name__ == '__main__':
    # Sadece lokal geliştirme için. Production'da Gunicorn kullan (Dockerfile'daki CMD).
    # MAILKAMP_ENV=production ayarlandıysa debug kapalı.
    _debug = (_ENV != 'production')
    app.run(debug=_debug, port=int(os.environ.get('PORT', 5000)))