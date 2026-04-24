from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from cryptography.fernet import Fernet, InvalidToken
import os

# --- ORTAK DEĞİŞKENLER VE EKLENTİLER ---
# Veritabanı yolu: Docker'da /app/instance mount edildiği için oraya yazıyoruz.
# Böylece container rebuild olsa bile veritabanı (kullanıcılar, SMTP ayarları vb.) silinmez.
# Lokalde ise proje köküne göre ./instance/ klasörüne yazar.
_DEFAULT_DB_DIR = os.environ.get('MAILKAMP_DB_DIR') or (
    '/app/instance' if os.path.isdir('/app/instance') else
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
)
os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)

# Kullanıcı özel bir yol belirttiyse onu kullan, yoksa instance klasörünü kullan.
DB_NAME = os.environ.get('MAILKAMP_DB_PATH') or os.path.join(_DEFAULT_DB_DIR, 'web_mailer_v6.db')

csrf = CSRFProtect()
login_manager = LoginManager()
# Blueprints kullandığımız için login fonksiyonunun yolunu belirtiyoruz:
login_manager.login_view = 'auth.login'

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["1000 per hour"],
    # Redis kullanmak istersen MAILKAMP_RATELIMIT_STORAGE="redis://host:6379/0" set et.
    # Bellek modu Gunicorn'da her worker için ayrı sayar; tek worker'da sorun değil.
    storage_uri=os.environ.get('MAILKAMP_RATELIMIT_STORAGE', 'memory://')
)

# --- ŞİFRELEME MOTORU KÖPRÜSÜ ---
def get_fernet():
    _FERNET_KEY = os.environ.get('SMTP_ENCRYPTION_KEY')
    if _FERNET_KEY:
        return Fernet(_FERNET_KEY.encode() if isinstance(_FERNET_KEY, str) else _FERNET_KEY)
    return None

def encrypt_smtp_password(plain):
    if plain is None or plain == '':
        return plain
    f = get_fernet()
    if not f:
        return plain
    return f.encrypt(plain.encode('utf-8')).decode('utf-8')

def decrypt_smtp_password(encrypted):
    if encrypted is None or encrypted == '':
        return encrypted
    f = get_fernet()
    if not f:
        return encrypted
    try:
        return f.decrypt(encrypted.encode('utf-8')).decode('utf-8')
    except (InvalidToken, ValueError, TypeError):
        return encrypted