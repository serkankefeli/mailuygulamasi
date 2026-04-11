from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import threading
import time
import smtplib
import random
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email import encoders

app = Flask(__name__)
app.secret_key = "cok_gizli_bir_anahtar_buraya"
DB_NAME = 'web_mailer_v6.db'
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


class User(UserMixin):
    def __init__(self, id, ad_soyad, is_admin, email, is_blocked=0):
        self.id = id
        self.ad_soyad = ad_soyad
        self.is_admin = is_admin
        self.email = email
        self.is_blocked = is_blocked


@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # YENİ: is_blocked sütunu eklendi
    try:
        cursor.execute("SELECT id, ad_soyad, is_admin, email, is_blocked FROM users WHERE id = ?", (user_id,))
    except sqlite3.OperationalError:
        cursor.execute("SELECT id, ad_soyad, is_admin, email FROM users WHERE id = ?", (user_id,))

    u = cursor.fetchone()
    conn.close()
    if u:
        is_blocked = u[4] if len(u) > 4 and u[4] is not None else 0
        return User(id=u[0], ad_soyad=u[1], is_admin=u[2], email=u[3], is_blocked=is_blocked)
    return None


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        '''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, ad_soyad TEXT, email TEXT UNIQUE, password_hash TEXT, is_admin INTEGER DEFAULT 0, auth_code TEXT)''')
    cursor.execute(
        '''CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, tarih TEXT, alici TEXT, konu TEXT, durum TEXT, detay TEXT, okundu INTEGER DEFAULT 0, okunma_tarihi TEXT)''')
    cursor.execute(
        '''CREATE TABLE IF NOT EXISTS blacklist (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, email TEXT, UNIQUE(user_id, email))''')
    cursor.execute(
        '''CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE, host TEXT, port TEXT, user_email TEXT, password TEXT)''')

    # YENİ: Eski veritabanını bozmadan engelleme sütunu ekliyoruz
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # Sütun zaten varsa hata verme

    cursor.execute("SELECT * FROM users WHERE email = 'admin@sistem.com'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (ad_soyad, email, password_hash, is_admin) VALUES (?, ?, ?, 1)",
                       ('Sistem Yöneticisi', 'admin@sistem.com', generate_password_hash("123456")))
    conn.commit()
    conn.close()


def background_mailer(user_id, email_list, subject, body, attachment_paths, video_link, cover_path, settings):
    host, port, sender_email, sender_pass = settings[2], settings[3], settings[4], settings[5]
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT email FROM blacklist WHERE user_id=?", (user_id,))
    blacklist = [row[0] for row in cursor.fetchall()]
    conn.close()

    try:
        server = smtplib.SMTP(host, int(port), timeout=15)
        server.starttls()
        server.login(sender_email, sender_pass)

        for alici in email_list:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            tarih = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if alici in blacklist:
                cursor.execute("INSERT INTO logs (user_id, tarih, alici, konu, durum, detay) VALUES (?, ?, ?, ?, ?, ?)",
                               (user_id, tarih, alici, subject, "Atlandı", "Kara Listede"))
                conn.commit()
                conn.close()
                continue
            cursor.execute("INSERT INTO logs (user_id, tarih, alici, konu, durum, detay) VALUES (?, ?, ?, ?, ?, ?)",
                           (user_id, tarih, alici, subject, "Gönderiliyor...", "İşlem kuyruğunda"))
            log_id = cursor.lastrowid
            conn.commit()
            conn.close()

            try:
                html_body = body.replace('\n', '<br>')
                if video_link and cover_path:
                    html_body += f'''<br><br><div style="text-align: center; margin: 20px 0;"><a href="{video_link}" target="_blank"><img src="cid:video_cover" alt="Videoyu İzle" style="max-width: 100%; border-radius: 8px;"></a><br><br><a href="{video_link}" target="_blank" style="background-color: #e74c3c; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; font-family: Arial, sans-serif; display: inline-block;">▶ Videoyu İzlemek İçin Tıklayın</a></div>'''
                html_body += '<br><br><hr><p style="font-size:11px; color:gray; font-family:Arial;">Bu e-postaları almak istemiyorsanız <b>İPTAL</b> yazarak yanıtlayınız.</p>'

                msg = MIMEMultipart('related')
                msg['From'] = sender_email
                msg['To'] = alici
                msg['Subject'] = subject
                msg.attach(MIMEText(html_body, 'html'))

                if video_link and cover_path:
                    try:
                        with open(cover_path, 'rb') as img_file:
                            cover_img = MIMEImage(img_file.read())
                            cover_img.add_header('Content-ID', '<video_cover>')
                            cover_img.add_header('Content-Disposition', 'inline')
                            msg.attach(cover_img)
                    except:
                        pass

                for path in attachment_paths:
                    part = MIMEBase('application', "octet-stream")
                    with open(path, 'rb') as file: part.set_payload(file.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f'attachment; filename={os.path.basename(path)}')
                    msg.attach(part)

                server.send_message(msg)
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("UPDATE logs SET durum=?, detay=? WHERE id=?",
                               ("Başarılı", "Sorunsuz iletildi.", log_id))
                conn.commit()
                conn.close()
                time.sleep(1)
            except Exception as e:
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("UPDATE logs SET durum=?, detay=? WHERE id=?",
                               ("Hata", f"İletilemedi: {str(e)[:50]}", log_id))
                conn.commit()
                conn.close()
        server.quit()
    except Exception as e:
        print("Sunucu Hatası:", e)


@app.route('/', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.is_admin == 1: return redirect(url_for('admin_users'))
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT id, ad_soyad, password_hash, is_admin, email, is_blocked FROM users WHERE email = ?",
                           (email,))
        except sqlite3.OperationalError:
            cursor.execute("SELECT id, ad_soyad, password_hash, is_admin, email FROM users WHERE email = ?", (email,))

        user_data = cursor.fetchone()

        if user_data and check_password_hash(user_data[2], password):
            is_blocked = user_data[5] if len(user_data) > 5 and user_data[5] is not None else 0

            # YENİ: Engelli kullanıcı kontrolü
            if is_blocked == 1:
                flash('Hesabınız sistem yöneticisi tarafından geçici olarak dondurulmuştur.', 'danger')
                conn.close()
                return redirect(url_for('login'))

            if user_data[3] == 1:
                flash('Güvenlik İhlali: Yönetici hesapları standart sayfadan giriş yapamaz!', 'danger')
                conn.close()
                return redirect(url_for('login'))

            cursor.execute(
                "SELECT host, port, user_email, password FROM settings WHERE user_id = (SELECT id FROM users WHERE is_admin = 1 LIMIT 1)")
            admin_settings = cursor.fetchone()

            if not admin_settings or not admin_settings[2]:
                login_user(User(id=user_data[0], ad_soyad=user_data[1], is_admin=user_data[3], email=user_data[4],
                                is_blocked=is_blocked))
                conn.close()
                return redirect(url_for('dashboard'))

            auth_code = str(random.randint(100000, 999999))
            cursor.execute("UPDATE users SET auth_code=? WHERE id=?", (auth_code, user_data[0]))
            conn.commit()
            conn.close()

            try:
                host, port, sender_email, sender_pass = admin_settings
                server = smtplib.SMTP(host, int(port), timeout=5)
                server.starttls()
                server.login(sender_email, sender_pass)
                msg = MIMEMultipart()
                msg['From'] = sender_email
                msg['To'] = email
                msg['Subject'] = "Sisteme Giriş Doğrulama Kodu"
                msg.attach(MIMEText(f"Doğrulama kodunuz: {auth_code}", 'plain'))
                server.send_message(msg)
                server.quit()
                session['pending_user_id'] = user_data[0]
                return redirect(url_for('verify_2fa'))
            except Exception:
                flash('Doğrulama e-postası gönderilemedi.', 'danger')
                return redirect(url_for('login'))
        else:
            conn.close()
            flash('E-Posta veya şifre hatalı!', 'danger')

    return render_template('login.html')


# --- YENİ: ŞİFREMİ UNUTTUM ROTALARI ---
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, ad_soyad FROM users WHERE email=?", (email,))
        user = cursor.fetchone()

        if user:
            cursor.execute(
                "SELECT host, port, user_email, password FROM settings WHERE user_id = (SELECT id FROM users WHERE is_admin = 1 LIMIT 1)")
            admin_settings = cursor.fetchone()

            if admin_settings and admin_settings[2]:
                reset_code = str(random.randint(100000, 999999))
                cursor.execute("UPDATE users SET auth_code=? WHERE id=?", (reset_code, user[0]))
                conn.commit()

                try:
                    host, port, sender_email, sender_pass = admin_settings
                    server = smtplib.SMTP(host, int(port), timeout=5)
                    server.starttls()
                    server.login(sender_email, sender_pass)

                    msg = MIMEMultipart()
                    msg['From'] = sender_email
                    msg['To'] = email
                    msg['Subject'] = "Şifre Sıfırlama Kodu"
                    msg.attach(MIMEText(f"Şifrenizi sıfırlamak için 6 haneli kodunuz: {reset_code}", 'plain'))

                    server.send_message(msg)
                    server.quit()

                    session['reset_email'] = email
                    conn.close()
                    flash('Sıfırlama kodu e-posta adresinize gönderildi.', 'success')
                    return redirect(url_for('reset_password'))
                except Exception:
                    flash('Mail gönderilemedi, SMTP ayarlarında sorun var.', 'danger')
            else:
                flash('Sistem e-posta ayarları yapılmadığı için şifre sıfırlama kullanılamaz.', 'danger')
        else:
            flash('Bu e-posta adresine kayıtlı bir hesap bulunamadı.', 'danger')
        conn.close()
    return render_template('forgot_password.html')


# --- ŞİFRE YENİLEME ROTASI ---
@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if 'reset_email' not in session: return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        code = request.form['code'].strip()
        new_password = request.form['new_password']
        email = session['reset_email']

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, auth_code FROM users WHERE email=?", (email,))
        user = cursor.fetchone()

        # MUCİZE BURADA: İki tarafı da str() ile zorla metne çevirip eşleştiriyoruz!
        if user and str(user[1]) == str(code):
            hashed_pw = generate_password_hash(new_password)
            cursor.execute("UPDATE users SET password_hash=?, auth_code=NULL WHERE id=?", (hashed_pw, user[0]))
            conn.commit()
            conn.close()
            session.pop('reset_email', None)
            flash('Şifreniz başarıyla güncellendi! Yeni şifrenizle giriş yapabilirsiniz.', 'success')
            return redirect(url_for('login'))
        else:
            conn.close()
            # Olası bir hatada terminale kırmızı uyarı fırlatacak ajan:
            print(f"--- ŞİFRE SIFIRLAMA HATASI --- Beklenen: {user[1]} | Girilen: {code}")
            flash('Geçersiz doğrulama kodu.', 'danger')

    return render_template('reset_password.html')

# --- YENİ: KULLANICI ENGELLEME ROTASI ---
@app.route('/admin/toggle_block/<int:id>')
@login_required
def toggle_block(id):
    if current_user.is_admin != 1: return redirect(url_for('dashboard'))
    if id == current_user.id:
        flash('Kendinizi engelleyemezsiniz!', 'danger')
        return redirect(url_for('admin_users'))

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT is_blocked, ad_soyad FROM users WHERE id=?", (id,))
    except sqlite3.OperationalError:
        flash('Lütfen sistemi yeniden başlatın (Veritabanı güncelleniyor).', 'warning')
        return redirect(url_for('admin_users'))

    user = cursor.fetchone()
    mevcut_durum = user[0] if user[0] is not None else 0
    yeni_durum = 1 if mevcut_durum == 0 else 0

    cursor.execute("UPDATE users SET is_blocked=? WHERE id=?", (yeni_durum, id))
    conn.commit()
    conn.close()

    mesaj = "engellendi, sisteme giriş yapamayacak." if yeni_durum == 1 else "engeli kaldırıldı."
    flash(f'{user[1]} adlı kullanıcının {mesaj}', 'warning')
    return redirect(url_for('admin_users'))


# --- DİĞER ROTALAR (Admin_login, Profile, Admin_users vb.) ---
@app.route('/gizli-kapi', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated and current_user.is_admin == 1: return redirect(url_for('admin_users'))
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        try:
            cursor.execute(
                "SELECT id, ad_soyad, password_hash, is_admin, email, is_blocked FROM users WHERE email = ? AND is_admin = 1",
                (email,))
        except sqlite3.OperationalError:
            cursor.execute(
                "SELECT id, ad_soyad, password_hash, is_admin, email FROM users WHERE email = ? AND is_admin = 1",
                (email,))

        admin_data = cursor.fetchone()

        if admin_data and check_password_hash(admin_data[2], password):
            cursor.execute("SELECT host, port, user_email, password FROM settings WHERE user_id = ?", (admin_data[0],))
            admin_settings = cursor.fetchone()
            is_blocked = admin_data[5] if len(admin_data) > 5 and admin_data[5] is not None else 0

            if not admin_settings or not admin_settings[2]:
                login_user(User(id=admin_data[0], ad_soyad=admin_data[1], is_admin=admin_data[3], email=admin_data[4],
                                is_blocked=is_blocked))
                conn.close()
                return redirect(url_for('admin_users'))

            auth_code = str(random.randint(100000, 999999))
            cursor.execute("UPDATE users SET auth_code=? WHERE id=?", (auth_code, admin_data[0]))
            conn.commit()
            conn.close()

            try:
                host, port, sender_email, sender_pass = admin_settings
                server = smtplib.SMTP(host, int(port), timeout=5)
                server.starttls()
                server.login(sender_email, sender_pass)
                msg = MIMEMultipart()
                msg['From'] = sender_email
                msg['To'] = email
                msg['Subject'] = "Yönetici Paneli Doğrulama Kodu"
                msg.attach(MIMEText(f"Süper Yönetici Giriş Kodunuz: {auth_code}", 'plain'))
                server.send_message(msg)
                server.quit()
                session['pending_user_id'] = admin_data[0]
                return redirect(url_for('verify_2fa'))
            except Exception:
                flash('Doğrulama e-postası gönderilemedi!', 'danger')
                return redirect(url_for('admin_login'))
        else:
            conn.close()
            flash('Yetkisiz giriş denemesi!', 'danger')
    return render_template('admin_login.html')


# --- 3. 2 ADIMLI DOĞRULAMA (2FA) EKRANI ---
@app.route('/verify', methods=['GET', 'POST'])
def verify_2fa():
    if 'pending_user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        user_code = request.form['code'].strip()  # Gelen koddaki boşlukları temizle
        user_id = session['pending_user_id']
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT id, ad_soyad, is_admin, email, auth_code, is_blocked FROM users WHERE id=?",
                           (user_id,))
        except sqlite3.OperationalError:
            cursor.execute("SELECT id, ad_soyad, is_admin, email, auth_code FROM users WHERE id=?", (user_id,))

        user = cursor.fetchone()

        # MUCİZE BURADA: İki tarafı da str() ile zorla metne çevirip eşleştiriyoruz!
        if user and str(user[4]) == str(user_code):
            cursor.execute("UPDATE users SET auth_code=NULL WHERE id=?", (user_id,))
            conn.commit()
            conn.close()
            session.pop('pending_user_id', None)

            is_blocked = user[5] if len(user) > 5 and user[5] is not None else 0
            login_user(User(id=user[0], ad_soyad=user[1], is_admin=user[2], email=user[3], is_blocked=is_blocked))

            if user[2] == 1:
                flash('Süper Yönetici Paneline Hoş Geldiniz.', 'success')
                return redirect(url_for('admin_users'))
            return redirect(url_for('dashboard'))
        else:
            conn.close()
            # Hata ajanı: Eğer yine olmazsa PyCharm terminaline beklenen ve girilen kodu yazdıracak!
            print(f"--- HATA TESPİTİ --- Beklenen Kod: {user[4]} | Senin Girdiğin: {user_code}")
            flash('Hatalı doğrulama kodu girdiniz!', 'danger')

    return render_template('verify.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        ad_soyad = request.form['ad_soyad'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']
        if password != request.form['confirm_password']:
            flash('Şifreler eşleşmiyor!', 'danger')
            return redirect(url_for('register'))
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            flash('Bu e-posta adresi zaten kullanılıyor.', 'danger')
            conn.close()
            return redirect(url_for('register'))
        hashed_pw = generate_password_hash(password)
        cursor.execute("INSERT INTO users (ad_soyad, email, password_hash, is_admin) VALUES (?, ?, ?, 0)",
                       (ad_soyad, email, hashed_pw))
        conn.commit()
        conn.close()
        flash('Hesabınız oluşturuldu! Şimdi giriş yapabilirsiniz.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        yeni_ad = request.form['ad_soyad'].strip()
        yeni_sifre = request.form['yeni_sifre']
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        if yeni_sifre:
            hashed_pw = generate_password_hash(yeni_sifre)
            cursor.execute("UPDATE users SET ad_soyad=?, password_hash=? WHERE id=?",
                           (yeni_ad, hashed_pw, current_user.id))
            flash('Profil bilgileriniz ve şifreniz güncellendi.', 'success')
        else:
            cursor.execute("UPDATE users SET ad_soyad=? WHERE id=?", (yeni_ad, current_user.id))
            flash('Profil bilgileriniz başarıyla güncellendi.', 'success')
        conn.commit()
        conn.close()
        current_user.ad_soyad = yeni_ad
        return redirect(url_for('profile'))
    return render_template('profile.html')


@app.route('/admin/users')
@login_required
def admin_users():
    if current_user.is_admin != 1: return redirect(url_for('dashboard'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id, ad_soyad, is_admin, email, is_blocked FROM users ORDER BY id DESC")
    except sqlite3.OperationalError:
        cursor.execute("SELECT id, ad_soyad, is_admin, email FROM users ORDER BY id DESC")

    all_users = cursor.fetchall()
    user_stats = []
    for user in all_users:
        u_id = user[0]
        cursor.execute("SELECT COUNT(*) FROM logs WHERE user_id=?", (u_id,))
        total_mails = cursor.fetchone()[0]
        is_blocked = user[4] if len(user) > 4 and user[4] is not None else 0
        user_stats.append(
            {'id': u_id, 'ad_soyad': user[1], 'is_admin': user[2], 'email': user[3], 'is_blocked': is_blocked,
             'total_mails': total_mails})
    conn.close()
    return render_template('admin_users.html', users=user_stats)


@app.route('/admin/toggle_role/<int:id>')
@login_required
def toggle_role(id):
    if current_user.is_admin != 1: return redirect(url_for('dashboard'))
    if id == current_user.id: return redirect(url_for('admin_users'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT is_admin, ad_soyad FROM users WHERE id=?", (id,))
    user = cursor.fetchone()
    yeni_yetki = 0 if user[0] == 1 else 1
    cursor.execute("UPDATE users SET is_admin=? WHERE id=?", (yeni_yetki, id))
    conn.commit()
    conn.close()
    flash(f'{user[1]} yetkisi güncellendi.', 'info')
    return redirect(url_for('admin_users'))


@app.route('/admin/delete_user/<int:id>')
@login_required
def delete_user(id):
    if current_user.is_admin != 1: return redirect(url_for('dashboard'))
    if id == current_user.id: return redirect(url_for('admin_users'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id=?", (id,))
    cursor.execute("DELETE FROM logs WHERE user_id=?", (id,))
    cursor.execute("DELETE FROM blacklist WHERE user_id=?", (id,))
    cursor.execute("DELETE FROM settings WHERE user_id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_users'))


@app.route('/dashboard')
@login_required
def dashboard():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM logs WHERE user_id=? ORDER BY id DESC LIMIT 50", (current_user.id,))
    logs = cursor.fetchall()
    cursor.execute("SELECT * FROM blacklist WHERE user_id=? ORDER BY id DESC", (current_user.id,))
    blacklist = cursor.fetchall()
    cursor.execute("SELECT * FROM settings WHERE user_id=?", (current_user.id,))
    settings = cursor.fetchone()
    conn.close()
    return render_template('dashboard.html', logs=logs, blacklist=blacklist, settings=settings)


@app.route('/save_settings', methods=['POST'])
@login_required
def save_settings():
    host, port = request.form['smtp_host'].strip(), request.form['smtp_port'].strip()
    user_email, password = request.form['smtp_user'].strip(), request.form['smtp_pass'].strip()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM settings WHERE user_id=?", (current_user.id,))
    if cursor.fetchone():
        cursor.execute("UPDATE settings SET host=?, port=?, user_email=?, password=? WHERE user_id=?",
                       (host, port, user_email, password, current_user.id))
    else:
        cursor.execute("INSERT INTO settings (user_id, host, port, user_email, password) VALUES (?, ?, ?, ?, ?)",
                       (current_user.id, host, port, user_email, password))
    conn.commit()
    conn.close()
    flash('Ayarlar başarıyla kaydedildi!', 'success')
    return redirect(url_for('dashboard'))


@app.route('/add_blacklist', methods=['POST'])
@login_required
def add_blacklist():
    raw_emails = request.form['blacklist_emails'].replace(",", "\n").split("\n")
    emails = [e.strip().lower() for e in raw_emails if "@" in e]
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    for email in emails: cursor.execute("INSERT OR IGNORE INTO blacklist (user_id, email) VALUES (?, ?)",
                                        (current_user.id, email))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))


@app.route('/remove_blacklist/<int:id>')
@login_required
def remove_blacklist(id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blacklist WHERE id=? AND user_id=?", (id, current_user.id))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))


@app.route('/send_mail', methods=['POST'])
@login_required
def send_mail():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM settings WHERE user_id=?", (current_user.id,))
    settings = cursor.fetchone()
    conn.close()
    if not settings or not settings[4]:
        flash('Lütfen önce Ayarlar sekmesinden bilgilerinizi kaydedin!', 'danger')
        return redirect(url_for('dashboard'))
    host, port, sender_email, sender_pass = settings[2], settings[3], settings[4], settings[5]
    try:
        test_server = smtplib.SMTP(host, int(port), timeout=5)
        test_server.starttls()
        test_server.login(sender_email, sender_pass)
        test_server.quit()
    except Exception:
        flash('Sunucu veya Şifre hatası. Lütfen ayarlarınızı kontrol edin.', 'danger')
        return redirect(url_for('dashboard'))

    raw_recipients = request.form['recipients'].replace(",", "\n").split("\n")
    email_list = [e.strip().lower() for e in raw_recipients if "@" in e]
    if not email_list: return redirect(url_for('dashboard'))

    subject = request.form['subject']
    body = request.form['body']
    video_link = request.form.get('video_link', '').strip()
    cover_path = None
    cover_file = request.files.get('video_cover')
    if cover_file and cover_file.filename:
        filename = secure_filename(cover_file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        cover_file.save(filepath)
        cover_path = filepath

    attachment_paths = []
    files = request.files.getlist('attachment')
    for file in files:
        if file.filename:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            attachment_paths.append(filepath)

    thread = threading.Thread(target=background_mailer,
                              args=(current_user.id, email_list, subject, body, attachment_paths, video_link,
                                    cover_path, settings))
    thread.daemon = True
    thread.start()
    flash('Kampanya başlatıldı. Mailler arka planda gönderiliyor.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


if __name__ == '__main__':
    init_db()
    app.run(debug=True)