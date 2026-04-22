"""
Geli mi Hata Yönetim Sistemi
"""
import logging
import traceback
from datetime import datetime
from functools import wraps
from flask import flash, current_app
import sqlite3
import os

# Loglama konfigürasyonu
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mailkamp_errors.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class MailKampError(Exception):
    """Mail Kamp özel exception s n f """
    def __init__(self, message, error_code=None, user_message=None):
        super().__init__(message)
        self.error_code = error_code
        self.user_message = user_message or "Bir hata olu tu. Lütfen daha sonra tekrar deneyin."
        self.timestamp = datetime.now()

def log_error_to_db(error_message, error_type, user_id=None, endpoint=None, traceback_info=None):
    """Hatalar veritaban na loglar"""
    try:
        db_name = os.environ.get('DB_NAME', 'web_mailer_v6.db')
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        
        # Error logs tablosu olu tur (yoksa)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                error_type TEXT,
                error_message TEXT,
                endpoint TEXT,
                traceback_info TEXT,
                timestamp TEXT,
                resolved INTEGER DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            INSERT INTO error_logs (user_id, error_type, error_message, endpoint, traceback_info, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            user_id,
            error_type,
            str(error_message)[:500],  # Max 500 karakter
            endpoint,
            str(traceback_info)[:2000] if traceback_info else None,  # Max 2000 karakter
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"Veritaban na loglama hatas : {str(e)}")

def handle_database_error(func):
    """Veritaban hatalar n yakalayan decorator"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except sqlite3.Error as e:
            error_msg = f"Veritaban hatas : {str(e)}"
            logger.error(error_msg)
            
            # Kullan c ya gösterülecek mesaj
            flash("Veritaban ilemi ba ar s zlad . Lütfen daha sonra tekrar deneyin.", "danger")
            
            # Hatay logla
            try:
                user_id = getattr(current_user, 'id', None) if 'current_user' in globals() else None
                log_error_to_db(error_msg, "DATABASE_ERROR", user_id, func.__name__, traceback.format_exc())
            except:
                pass
            
            return None
            
        except Exception as e:
            error_msg = f"Beklenmedik hata: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            
            flash("Beklenmedik bir hata olu tu. Sistem yöneticisi bilgilendirildi.", "danger")
            
            try:
                user_id = getattr(current_user, 'id', None) if 'current_user' in globals() else None
                log_error_to_db(error_msg, "UNEXPECTED_ERROR", user_id, func.__name__, traceback.format_exc())
            except:
                pass
            
            return None
    
    return wrapper

def handle_mail_error(func):
    """E-posta gönderim hatalar n yakalayan decorator"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_msg = f"E-posta gönderim hatas : {str(e)}"
            logger.error(error_msg)
            
            # SMTP hatalar için özel mesajlar
            if "SMTP" in str(e) or "smtplib" in str(e):
                flash("SMTP sunucusuna ba lan lamad . Lütfen SMTP ayarlar n z kontrol edin.", "danger")
            elif "timeout" in str(e).lower():
                flash("E-posta gönderim zaman a m na u rad . Lütfen daha sonra tekrar deneyin.", "warning")
            else:
                flash("E-posta gönderimi s ras nda bir hata olu tu.", "danger")
            
            try:
                user_id = getattr(current_user, 'id', None) if 'current_user' in globals() else None
                log_error_to_db(error_msg, "MAIL_ERROR", user_id, func.__name__, traceback.format_exc())
            except:
                pass
            
            return None
    
    return wrapper

def safe_execute_query(query, params=None, fetch_one=False, fetch_all=True):
    """Güvenli veritaban sorgusu çal t rma"""
    try:
        db_name = os.environ.get('DB_NAME', 'web_mailer_v6.db')
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row  # Dictionary-like access
        cursor = conn.cursor()
        
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        result = None
        if fetch_one:
            result = cursor.fetchone()
        elif fetch_all:
            result = cursor.fetchall()
        
        conn.commit()
        conn.close()
        
        return result
        
    except sqlite3.Error as e:
        logger.error(f"Safe query execution error: {str(e)}")
        logger.error(f"Query: {query}")
        if params:
            logger.error(f"Params: {params}")
        
        # Hatay logla
        try:
            log_error_to_db(f"Query execution failed: {str(e)}", "SAFE_QUERY_ERROR", None, "safe_execute_query", traceback.format_exc())
        except:
            pass
        
        return None

def validate_email_list(email_list):
    """E-posta listesini güvenli sekilde do rular"""
    if not email_list:
        return []
    
    validated_emails = []
    for email in email_list:
        email = str(email).strip().lower()
        if "@" in email and "." in email.split("@")[-1]:
            validated_emails.append(email)
    
    return validated_emails

def get_error_statistics():
    """Hata istatistiklerini getir"""
    try:
        result = safe_execute_query('''
            SELECT 
                error_type,
                COUNT(*) as count,
                DATE(timestamp) as date
            FROM error_logs 
            WHERE DATE(timestamp) >= DATE('now', '-7 days')
            GROUP BY error_type, DATE(timestamp)
            ORDER BY date DESC, count DESC
        ''')
        
        return result if result else []
        
    except Exception as e:
        logger.error(f"Error statistics failed: {str(e)}")
        return []

def cleanup_old_logs(days_to_keep=30):
    """Eski hata loglar n temizler"""
    try:
        db_name = os.environ.get('DB_NAME', 'web_mailer_v6.db')
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            DELETE FROM error_logs 
            WHERE DATE(timestamp) < DATE('now', '-{} days')
        '''.format(days_to_keep))
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        logger.info(f"Cleaned up {deleted_count} old error logs")
        return deleted_count
        
    except Exception as e:
        logger.error(f"Log cleanup failed: {str(e)}")
        return 0

# Flask uygulamas için hata yönetimi
def setup_error_handlers(app):
    """Flask uygulamas için hata yöneticilerini kurar"""
    
    @app.errorhandler(404)
    def not_found_error(error):
        logger.warning(f"404 Error: {error}")
        return "Sayfa bulunamad", 404
    
    @app.errorhandler(500)
    def internal_error(error):
        logger.error(f"500 Error: {error}")
        log_error_to_db(f"Internal server error: {str(error)}", "INTERNAL_ERROR", None, str(error), traceback.format_exc())
        return "Sunucu hatas olu tu", 500
    
    @app.errorhandler(MailKampError)
    def handle_mailkamp_error(error):
        logger.error(f"MailKamp Error: {error}")
        log_error_to_db(str(error), "MAILKAMP_ERROR", None, None, traceback.format_exc())
        flash(error.user_message, "danger")
        return None
