import os
import secrets
import smtplib
import sqlite3
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import DB_NAME, limiter, decrypt_smtp_password
from models import User

# Blueprint'i tanımlıyoruz
auth_bp = Blueprint('auth', __name__)

# --- 🛡️ LOGIN (GİRİŞ) ---
@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("60 per minute; 10 per hour", methods=["POST"])
def login():
    if current_user.is_authenticated:
        if getattr(current_user, 'is_admin', 0) == 1:
            return redirect(url_for('admin.admin_users'))
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        # --- 🛡️ CLOUDFLARE TURNSTILE KONTROLÜ ---
        turnstile_response = request.form.get('cf-turnstile-response')
        secret_key = os.environ.get('TURNSTILE_SECRET_KEY')

        if secret_key and turnstile_response:
            verify_url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
            verify_data = {'secret': secret_key, 'response': turnstile_response}
            try:
                cf_res = requests.post(verify_url, data=verify_data, timeout=5).json()
                if not cf_res.get('success'):
                    flash("Güvenlik doğrulamasından geçemediniz. Lütfen tekrar deneyin.", "danger")
                    return redirect(url_for('auth.login'))
            except Exception as e:
                print(f"Turnstile Hatası: {e}")

        email = request.form['email'].strip().lower()
        password = request.form['password']

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        try:
            cursor.execute(
                "SELECT id, ad_soyad, password_hash, is_admin, email, is_blocked, plan_type FROM users WHERE email = ?",
                (email,))
            user_data = cursor.fetchone()
        except Exception:
            cursor.execute("SELECT id, ad_soyad, password_hash, is_admin, email FROM users WHERE email = ?", (email,))
            user_data = cursor.fetchone()

        if user_data and check_password_hash(user_data[2], password):
            is_admin = user_data[3]
            if is_admin == 1:
                conn.close()
                flash('Güvenlik İhlali: Yönetici hesapları buradan giriş yapamaz!', 'danger')
                return redirect(url_for('auth.login'))

            is_blocked = user_data[5] if len(user_data) > 5 and user_data[5] is not None else 0
            if is_blocked == 1:
                conn.close()
                flash('Hesabınız engellenmiştir.', 'danger')
                return redirect(url_for('auth.login'))

            plan_type = user_data[6] if len(user_data) > 6 and user_data[6] is not None else 'free'
            login_user(User(id=user_data[0], ad_soyad=user_data[1], is_admin=is_admin, email=user_data[4],
                            is_blocked=is_blocked, plan_type=plan_type))
            conn.close()
            session.pop('temp_user_email', None)
            return redirect(url_for('main.dashboard'))
        else:
            conn.close()
            flash('Hatalı e-posta veya şifre!', 'danger')

    return render_template('login.html')

# --- 🛡️ LOGOUT (ÇIKIŞ) ---
@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

# --- 🛡️ REGISTER (KAYIT) ---
@auth_bp.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per minute; 20 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated: return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        ad_soyad = request.form['ad_soyad'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']

        if len(password) < 8 or not any(not c.isalnum() for c in password):
            flash('Şifreniz en az 8 karakter ve 1 özel karakter içermelidir!', 'warning')
            return redirect(url_for('auth.register'))

        if password != request.form['confirm_password']:
            flash('Şifreler eşleşmiyor!', 'danger')
            return redirect(url_for('auth.register'))

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            flash('Bu mail zaten kayıtlı.', 'danger')
            conn.close()
            return redirect(url_for('auth.register'))

        hashed_pw = generate_password_hash(password)
        cursor.execute("INSERT INTO users (ad_soyad, email, password_hash, is_admin, plan_type) VALUES (?, ?, ?, 0, 'free')", (ad_soyad, email, hashed_pw))
        conn.commit()
        conn.close()
        flash('Kayıt başarılı! Giriş yapabilirsiniz.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('register.html')

# --- 🔑 SİFREMİ UNUTTUM ---
@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit("5 per minute; 20 per hour", methods=["POST"])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, ad_soyad FROM users WHERE email=?", (email,))
        user = cursor.fetchone()
        if user:
            cursor.execute("SELECT host, port, user_email, password FROM settings WHERE user_id = (SELECT id FROM users WHERE is_admin = 1 LIMIT 1)")
            admin_settings = cursor.fetchone()
            if admin_settings and admin_settings[2]:
                reset_code = f"{secrets.randbelow(1000000):06d}"
                cursor.execute("UPDATE users SET auth_code=? WHERE id=?", (reset_code, user[0]))
                conn.commit()
                try:
                    host, port, sender_email, sender_pass_stored = admin_settings
                    admin_env_pass = os.environ.get('ADMIN_SMTP_PASSWORD')
                    sender_pass = admin_env_pass or decrypt_smtp_password(sender_pass_stored)
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
                    return redirect(url_for('auth.reset_password'))
                except Exception:
                    flash('Mail ayarlarında sorun var.', 'danger')
            else:
                flash('E-posta ayarları yapılmamış.', 'danger')
        else:
            flash('Hesap bulunamadı.', 'danger')
        conn.close()
    return render_template('forgot_password.html')

# --- 🔑 SİFRE SIFIRLA ---
@auth_bp.route('/reset-password', methods=['GET', 'POST'])
@limiter.limit("10 per minute; 50 per hour", methods=["POST"])
def reset_password():
    if 'reset_email' not in session: return redirect(url_for('auth.forgot_password'))
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
            return redirect(url_for('auth.login'))
        else:
            conn.close()
            flash('Geçersiz kod.', 'danger')
    return render_template('reset_password.html')

# --- 👤 PROFİL ---
@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        yeni_ad = request.form['ad_soyad'].strip()
        yeni_sifre = request.form['yeni_sifre']
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        if yeni_sifre:
            hashed_pw = generate_password_hash(yeni_sifre)
            cursor.execute("UPDATE users SET ad_soyad=?, password_hash=? WHERE id=?", (yeni_ad, hashed_pw, current_user.id))
        else:
            cursor.execute("UPDATE users SET ad_soyad=? WHERE id=?", (yeni_ad, current_user.id))
        conn.commit()
        conn.close()
        current_user.ad_soyad = yeni_ad
        flash('Profil güncellendi.', 'success')
        return redirect(url_for('auth.profile'))
    return render_template('profile.html')

# --- 🛡️ 2FA DOĞRULAMA ---
@auth_bp.route('/verify-2fa', methods=['GET', 'POST'])
def verify_2fa():
    email = session.get('temp_user_email')
    if not email: return redirect(url_for('auth.login'))
    if request.method == 'POST':
        code = request.form['code'].strip()
        conn = sqlite3.connect(DB_NAME)
        u = conn.execute(
            "SELECT id, ad_soyad, is_admin, email, is_blocked, plan_type, auth_code FROM users WHERE email = ?",
            (email,)).fetchone()
        if u and str(u[6]) == str(code):
            conn.execute("UPDATE users SET auth_code = NULL WHERE id = ?", (u[0],))
            conn.commit()
            conn.close()
            plan_type = u[5] if u[5] is not None else 'free'
            login_user(User(id=u[0], ad_soyad=u[1], is_admin=u[2], email=u[3], is_blocked=u[4], plan_type=plan_type))
            session.pop('temp_user_email', None)
            return redirect(url_for('main.dashboard'))
        conn.close()
        flash('Geçersiz kod!', 'danger')
    return render_template('verify_2fa.html')