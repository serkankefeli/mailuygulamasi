from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import threading
import time
import smtplib
import random
import urllib.parse
import secrets
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

    cursor.execute(
        "CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, email TEXT, UNIQUE(user_id, email))")
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, group_name TEXT, UNIQUE(user_id, group_name))")
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS contact_group_rel (contact_id INTEGER, group_id INTEGER, UNIQUE(contact_id, group_id))")

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # YENİ: API ANAHTARI İÇİN SÜTUN EKLENDİ
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN api_key TEXT")
    except sqlite3.OperationalError:
        pass

    cursor.execute("SELECT * FROM users WHERE email = 'admin@sistem.com'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (ad_soyad, email, password_hash, is_admin) VALUES (?, ?, ?, 1)",
                       ('Sistem Yöneticisi', 'admin@sistem.com', generate_password_hash("123456")))
    conn.commit()
    conn.close()


# --- YENİ: DIŞ SİSTEMLER (CRM/WEB) İÇİN REST API BAĞLANTI NOKTASI ---
@app.route('/api/send', methods=['POST'])
def api_send():
    api_key = request.headers.get('X-API-KEY')
    if not api_key:
        return jsonify({'error': 'X-API-KEY basligi eksik!'}), 401

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE api_key=?", (api_key,))
    user = cursor.fetchone()

    if not user:
        conn.close()
        return jsonify({'error': 'Gecersiz API Anahtari!'}), 401

    user_id = user[0]
    cursor.execute("SELECT * FROM settings WHERE user_id=?", (user_id,))
    settings = cursor.fetchone()
    conn.close()

    if not settings or not settings[4]:
        return jsonify({'error': 'SMTP ayarlariniz henuz yapilmamis.'}), 400

    data = request.get_json()
    if not data or not data.get('to') or not data.get('subject') or not data.get('body'):
        return jsonify({'error': 'Gerekli alanlar eksik: to (liste), subject, body'}), 400

    email_list = [e.strip().lower() for e in data['to'] if "@" in e]
    if not email_list:
        return jsonify({'error': 'Gecerli bir e-posta adresi bulunamadi.'}), 400

    # API'den gelen isteği doğrudan arka plan motoruna aktar!
    base_url = request.host_url
    thread = threading.Thread(target=background_mailer, args=(
        user_id, email_list, data['subject'], data['body'],
        [], None, None, settings, base_url
    ))
    thread.daemon = True
    thread.start()

    return jsonify({'success': True, 'message': f'{len(email_list)} alici isleme alindi ve gonderiliyor.'}), 200


# --- YENİ: API ANAHTARI OLUŞTURUCU ---
@app.route('/generate_api_key', methods=['POST'])
@login_required
def generate_api_key():
    new_key = secrets.token_hex(24)  # 48 karakterlik kırılmaz bir şifre üretir
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET api_key=? WHERE id=?", (new_key, current_user.id))
    conn.commit()
    conn.close()
    flash('Yeni Güvenlik API Anahtarınız başarıyla oluşturuldu!', 'success')
    return redirect(url_for('dashboard'))


@app.route('/unsubscribe')
def unsubscribe():
    user_id = request.args.get('u')
    email = request.args.get('e')
    if user_id and email:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO blacklist (user_id, email) VALUES (?, ?)", (user_id, email.lower()))
        conn.commit()
        conn.close()
        return f'''<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8"><title>Abonelikten Ayrıl</title><style>body {{ background-color: #f4f7f6; font-family: 'Segoe UI', Tahoma, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }} .card {{ background: #ffffff; padding: 40px; border-radius: 12px; box-shadow: 0 5px 15px rgba(0,0,0,0.05); text-align: center; max-width: 450px; border-top: 5px solid #e74c3c; }} h2 {{ color: #2c3e50; margin-top: 0; }} p {{ color: #7f8c8d; font-size: 16px; line-height: 1.6; }} .email-badge {{ background-color: #f8f9fa; padding: 8px 15px; border-radius: 50px; color: #e74c3c; font-weight: bold; border: 1px solid #fee; display: inline-block; margin-top: 10px; }}</style></head><body><div class="card"><h2>Abonelikten Ayrıldınız</h2><p>Talebiniz alınmış ve sistemimize işlenmiştir. Bundan sonraki süreçte aşağıdaki adrese bülten veya kampanya e-postası gönderilmeyecektir:</p><div class="email-badge">{email}</div></div></body></html>'''
    return "Geçersiz veya eksik link."


@app.route('/track')
def track():
    log_id = request.args.get('l')
    target_url = request.args.get('u')
    if log_id:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        tarih = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("UPDATE logs SET okundu=1, okunma_tarihi=?, durum=?, detay=? WHERE id=?",
                       (tarih, "Okundu", "Müşteri linke tıkladı ve detayları inceledi!", log_id))
        conn.commit()
        conn.close()
    if target_url: return redirect(target_url)
    return redirect(url_for('login'))


def background_mailer(user_id, email_list, subject, body, attachment_paths, video_link, cover_path, settings, base_url):
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
            if alici in blacklist:
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                tarih = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute("INSERT INTO logs (user_id, tarih, alici, konu, durum, detay) VALUES (?, ?, ?, ?, ?, ?)",
                               (user_id, tarih, alici, subject, "Atlandı", "Kullanıcı Kara Listede"))
                conn.commit()
                conn.close()
                continue

            kisisel_body = body
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM contacts WHERE user_id=? AND email=?", (user_id, alici))
            kisi_kaydi = cursor.fetchone()

            if kisi_kaydi and kisi_kaydi[0]:
                kisisel_body = kisisel_body.replace("{isim}", kisi_kaydi[0])
            else:
                kisisel_body = kisisel_body.replace("{isim}", "Değerli Müşterimiz")

            tarih = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("INSERT INTO logs (user_id, tarih, alici, konu, durum, detay) VALUES (?, ?, ?, ?, ?, ?)",
                           (user_id, tarih, alici, subject, "Gönderiliyor...", "İşlem kuyruğunda"))
            log_id = cursor.lastrowid
            conn.commit()
            conn.close()

            try:
                video_html = ""
                if video_link:
                    safe_url = urllib.parse.quote(video_link, safe='')
                    tracking_link = f"{base_url}track?l={log_id}&u={safe_url}"
                    if cover_path:
                        video_html = f'''<div style="text-align: center; margin: 30px 0; padding: 20px; background-color: #f8f9fa; border-radius: 8px;"><a href="{tracking_link}" target="_blank" style="display: block; text-decoration: none;"><img src="cid:video_cover" alt="Görseli İncele" style="max-width: 100%; height: auto; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);"></a><br><a href="{tracking_link}" target="_blank" style="background-color: #e74c3c; color: #ffffff; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: bold; font-family: Arial, sans-serif; display: inline-block; margin-top: 15px; font-size: 16px;">▶ Detayları İncele / İzle</a></div>'''
                    else:
                        video_html = f'''<div style="text-align: center; margin: 30px 0;"><a href="{tracking_link}" target="_blank" style="background-color: #2980b9; color: #ffffff; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: bold; font-family: Arial, sans-serif; display: inline-block; font-size: 16px;">Detayları Görüntülemek İçin Tıklayın</a></div>'''

                safe_alici = urllib.parse.quote(alici, safe='')
                unsubscribe_link = f"{base_url}unsubscribe?u={user_id}&e={safe_alici}"

                kurumsal_html = f'''<!DOCTYPE html><html><head><meta charset="utf-8"></head><body style="margin: 0; padding: 0; background-color: #f4f7f6; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;"><table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f4f7f6; padding: 40px 20px;"><tr><td align="center"><table width="600" border="0" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 10px; overflow: hidden; box-shadow: 0 5px 15px rgba(0,0,0,0.05);"><tr><td style="background-color: #1a2b3c; padding: 30px; text-align: center;"><h1 style="color: #ffffff; margin: 0; font-size: 24px; font-weight: 600; letter-spacing: 1px;">EST Yazılım ve Bilişim</h1></td></tr><tr><td style="padding: 40px 30px; color: #333333; line-height: 1.8; font-size: 16px;">{kisisel_body}{video_html}</td></tr><tr><td style="background-color: #ecf0f1; padding: 20px 30px; text-align: center; border-top: 1px solid #dee2e6;"><p style="margin: 0; font-size: 13px; color: #7f8c8d; font-weight: bold;">© {datetime.now().year} EST Yazılım ve Bilişim Teknolojileri Limited Şirketi</p><p style="margin: 10px 0 0 0; font-size: 12px; color: #95a5a6;">Bu e-postaları bir daha almak istemiyorsanız <a href="{unsubscribe_link}" style="color: #e74c3c; text-decoration: underline; font-weight: bold;">buraya tıklayarak</a> abonelikten güvenle ayrılabilirsiniz.</p></td></tr></table></td></tr></table></body></html>'''

                msg = MIMEMultipart('related')
                msg['From'] = sender_email
                msg['To'] = alici
                msg['Subject'] = subject
                msg.attach(MIMEText(kurumsal_html, 'html'))

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
                               ("İletildi (Okunmadı)", "Mail kutuya ulaştı, tıklama bekleniyor.", log_id))
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

    for path in attachment_paths:
        if os.path.exists(path): os.remove(path)
    if cover_path and os.path.exists(cover_path):
        os.remove(cover_path)


@app.route('/contacts')
@login_required
def contacts():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, group_name FROM groups WHERE user_id=?", (current_user.id,))
    groups = cursor.fetchall()
    cursor.execute("SELECT id, name, email FROM contacts WHERE user_id=?", (current_user.id,))
    raw_contacts = cursor.fetchall()
    contact_list = []
    for c in raw_contacts:
        cursor.execute(
            "SELECT g.group_name FROM groups g JOIN contact_group_rel cgr ON g.id = cgr.group_id WHERE cgr.contact_id=?",
            (c[0],))
        c_groups = [row[0] for row in cursor.fetchall()]
        contact_list.append({'id': c[0], 'name': c[1], 'email': c[2], 'groups': ", ".join(c_groups)})
    conn.close()
    return render_template('contacts.html', groups=groups, contacts=contact_list)


@app.route('/add_group', methods=['POST'])
@login_required
def add_group():
    group_name = request.form.get('group_name').strip()
    if group_name:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO groups (user_id, group_name) VALUES (?, ?)",
                       (current_user.id, group_name))
        conn.commit()
        conn.close()
        flash(f'"{group_name}" grubu oluşturuldu.', 'success')
    return redirect(url_for('contacts'))


@app.route('/add_contact', methods=['POST'])
@login_required
def add_contact():
    name = request.form.get('name').strip()
    email = request.form.get('email').strip().lower()
    group_ids = request.form.getlist('group_ids')
    if name and email:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO contacts (user_id, name, email) VALUES (?, ?, ?)",
                       (current_user.id, name, email))
        cursor.execute("SELECT id FROM contacts WHERE user_id=? AND email=?", (current_user.id, email))
        contact_id = cursor.fetchone()[0]
        cursor.execute("DELETE FROM contact_group_rel WHERE contact_id=?", (contact_id,))
        for gid in group_ids:
            cursor.execute("INSERT INTO contact_group_rel (contact_id, group_id) VALUES (?, ?)", (contact_id, gid))
        conn.commit()
        conn.close()
        flash('Kişi rehbere kaydedildi.', 'success')
    return redirect(url_for('contacts'))


@app.route('/api/get_group_emails/<int:group_id>')
@login_required
def get_group_emails(group_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT c.email FROM contacts c JOIN contact_group_rel cgr ON c.id = cgr.contact_id WHERE cgr.group_id=? AND c.user_id=?",
        (group_id, current_user.id))
    emails = [row[0] for row in cursor.fetchall()]
    conn.close()
    return jsonify({'emails': emails})


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
            if is_blocked == 1:
                flash('Hesabınız dondurulmuştur.', 'danger')
                conn.close()
                return redirect(url_for('login'))
            if user_data[3] == 1:
                flash('Güvenlik İhlali: Yönetici hesabı.', 'danger')
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
                msg['Subject'] = "Giriş Kodu"
                msg.attach(MIMEText(f"Doğrulama kodunuz: {auth_code}", 'plain'))
                server.send_message(msg)
                server.quit()
                session['pending_user_id'] = user_data[0]
                return redirect(url_for('verify_2fa'))
            except Exception:
                flash('Mail gönderilemedi.', 'danger')
                return redirect(url_for('login'))
        else:
            conn.close()
            flash('Hatalı giriş!', 'danger')
    return render_template('login.html')


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
                    msg.attach(MIMEText(f"Kodunuz: {reset_code}", 'plain'))
                    server.send_message(msg)
                    server.quit()
                    session['reset_email'] = email
                    conn.close()
                    flash('Sıfırlama kodu gönderildi.', 'success')
                    return redirect(url_for('reset_password'))
                except Exception:
                    flash('Mail ayarlarında sorun var.', 'danger')
            else:
                flash('E-posta ayarları yapılmamış.', 'danger')
        else:
            flash('Hesap bulunamadı.', 'danger')
        conn.close()
    return render_template('forgot_password.html')


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
        if user and str(user[1]) == str(code):
            hashed_pw = generate_password_hash(new_password)
            cursor.execute("UPDATE users SET password_hash=?, auth_code=NULL WHERE id=?", (hashed_pw, user[0]))
            conn.commit()
            conn.close()
            session.pop('reset_email', None)
            flash('Şifreniz güncellendi.', 'success')
            return redirect(url_for('login'))
        else:
            conn.close()
            flash('Geçersiz kod.', 'danger')
    return render_template('reset_password.html')


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
                msg['Subject'] = "Yönetici Girişi"
                msg.attach(MIMEText(f"Kodunuz: {auth_code}", 'plain'))
                server.send_message(msg)
                server.quit()
                session['pending_user_id'] = admin_data[0]
                return redirect(url_for('verify_2fa'))
            except Exception:
                flash('Mail gönderilemedi!', 'danger')
                return redirect(url_for('admin_login'))
        else:
            conn.close()
            flash('Yetkisiz giriş!', 'danger')
    return render_template('admin_login.html')


@app.route('/verify', methods=['GET', 'POST'])
def verify_2fa():
    if 'pending_user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        user_code = request.form['code'].strip()
        user_id = session['pending_user_id']
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, ad_soyad, is_admin, email, auth_code, is_blocked FROM users WHERE id=?",
                           (user_id,))
        except sqlite3.OperationalError:
            cursor.execute("SELECT id, ad_soyad, is_admin, email, auth_code FROM users WHERE id=?", (user_id,))
        user = cursor.fetchone()
        if user and str(user[4]) == str(user_code):
            cursor.execute("UPDATE users SET auth_code=NULL WHERE id=?", (user_id,))
            conn.commit()
            conn.close()
            session.pop('pending_user_id', None)
            is_blocked = user[5] if len(user) > 5 and user[5] is not None else 0
            login_user(User(id=user[0], ad_soyad=user[1], is_admin=user[2], email=user[3], is_blocked=is_blocked))
            if user[2] == 1: return redirect(url_for('admin_users'))
            return redirect(url_for('dashboard'))
        else:
            conn.close()
            flash('Hatalı kod!', 'danger')
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
            flash('Bu mail zaten kayıtlı.', 'danger')
            conn.close()
            return redirect(url_for('register'))
        hashed_pw = generate_password_hash(password)
        cursor.execute("INSERT INTO users (ad_soyad, email, password_hash, is_admin) VALUES (?, ?, ?, 0)",
                       (ad_soyad, email, hashed_pw))
        conn.commit()
        conn.close()
        flash('Kayıt başarılı!', 'success')
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
        else:
            cursor.execute("UPDATE users SET ad_soyad=? WHERE id=?", (yeni_ad, current_user.id))
        conn.commit()
        conn.close()
        current_user.ad_soyad = yeni_ad
        flash('Profil güncellendi.', 'success')
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
    return redirect(url_for('admin_users'))


@app.route('/admin/toggle_block/<int:id>')
@login_required
def toggle_block(id):
    if current_user.is_admin != 1: return redirect(url_for('dashboard'))
    if id == current_user.id: return redirect(url_for('admin_users'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT is_blocked, ad_soyad FROM users WHERE id=?", (id,))
    except sqlite3.OperationalError:
        return redirect(url_for('admin_users'))
    user = cursor.fetchone()
    mevcut_durum = user[0] if user[0] is not None else 0
    yeni_durum = 1 if mevcut_durum == 0 else 0
    cursor.execute("UPDATE users SET is_blocked=? WHERE id=?", (yeni_durum, id))
    conn.commit()
    conn.close()
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
    cursor.execute("SELECT id, group_name FROM groups WHERE user_id=?", (current_user.id,))
    groups = cursor.fetchall()

    # API Anahtarını Çek
    api_key = None
    try:
        cursor.execute("SELECT api_key FROM users WHERE id=?", (current_user.id,))
        result = cursor.fetchone()
        if result: api_key = result[0]
    except sqlite3.OperationalError:
        pass

    cursor.execute("SELECT COUNT(*) FROM logs WHERE user_id=?", (current_user.id,))
    total_mails = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM logs WHERE user_id=? AND durum='İletildi (Okunmadı)'", (current_user.id,))
    unread_mails = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM logs WHERE user_id=? AND durum='Okundu'", (current_user.id,))
    read_mails = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM logs WHERE user_id=? AND durum='Hata'", (current_user.id,))
    error_mails = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM logs WHERE user_id=? AND durum='Atlandı'", (current_user.id,))
    skipped_mails = cursor.fetchone()[0]

    cursor.execute(
        "SELECT substr(tarih, 1, 10) as day, COUNT(*) FROM logs WHERE user_id=? GROUP BY day ORDER BY day DESC LIMIT 7",
        (current_user.id,))
    trend_data_raw = cursor.fetchall()

    trend_labels = [row[0] for row in reversed(trend_data_raw)]
    trend_counts = [row[1] for row in reversed(trend_data_raw)]

    stats = {
        'total': total_mails, 'unread': unread_mails, 'read': read_mails,
        'error': error_mails, 'skipped': skipped_mails,
        'trend_labels': trend_labels, 'trend_counts': trend_counts
    }

    conn.close()
    return render_template('dashboard.html', logs=logs, blacklist=blacklist, settings=settings, groups=groups,
                           stats=stats, api_key=api_key)


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

    base_url = request.host_url

    send_time_str = request.form.get('send_time')
    delay = 0
    if send_time_str:
        try:
            if len(send_time_str) == 16:
                send_time = datetime.strptime(send_time_str, '%Y-%m-%dT%H:%M')
            else:
                send_time = datetime.strptime(send_time_str, '%Y-%m-%dT%H:%M:%S')
            now = datetime.now()
            if send_time > now: delay = (send_time - now).total_seconds()
        except Exception as e:
            pass

    if delay > 0:
        thread = threading.Timer(delay, background_mailer,
                                 args=(current_user.id, email_list, subject, body, attachment_paths, video_link,
                                       cover_path, settings, base_url))
        thread.daemon = True
        thread.start()
        hedef_saat = send_time.strftime('%d.%m.%Y %H:%M')
        flash(f'Kampanya zamanlandı! {hedef_saat} tarihinde otomatik olarak gönderilecek.', 'success')
    else:
        thread = threading.Thread(target=background_mailer,
                                  args=(current_user.id, email_list, subject, body, attachment_paths, video_link,
                                        cover_path, settings, base_url))
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