from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from flask_login import login_required, current_user, login_user
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
import os

from extensions import DB_NAME, limiter, decrypt_smtp_password
from models import User

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/gizli-kapi', methods=['GET', 'POST'])
@limiter.limit("5 per minute; 20 per hour", methods=["POST"])
def admin_login():
    if current_user.is_authenticated and current_user.is_admin == 1: return redirect(url_for('admin.admin_users'))
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT id, ad_soyad, password_hash, is_admin, email, is_blocked, plan_type FROM users WHERE email = ? AND is_admin = 1",
                (email,))
        except Exception:
            cursor.execute(
                "SELECT id, ad_soyad, password_hash, is_admin, email FROM users WHERE email = ? AND is_admin = 1",
                (email,))
        admin_data = cursor.fetchone()

        if admin_data and check_password_hash(admin_data[2], password):
            cursor.execute("SELECT host, port, user_email, password FROM settings WHERE user_id = ?", (admin_data[0],))
            admin_settings = cursor.fetchone()
            is_blocked = admin_data[5] if len(admin_data) > 5 and admin_data[5] is not None else 0
            plan_type = admin_data[6] if len(admin_data) > 6 and admin_data[6] is not None else 'pro'

            if not admin_settings or not admin_settings[2]:
                login_user(User(id=admin_data[0], ad_soyad=admin_data[1], is_admin=admin_data[3], email=admin_data[4],
                                is_blocked=is_blocked, plan_type=plan_type))
                conn.close()
                return redirect(url_for('admin.admin_users'))

            auth_code = f"{secrets.randbelow(1000000):06d}"
            cursor.execute("UPDATE users SET auth_code=? WHERE id=?", (auth_code, admin_data[0]))
            conn.commit()
            conn.close()
            try:
                host, port, sender_email, sender_pass_stored = admin_settings

                # ÖNCE VERİTABANINA BAK, BOŞSA VEYA ÇÖZÜLEMEZSE .ENV'YE DÖN!
                sender_pass = decrypt_smtp_password(sender_pass_stored)
                if not sender_pass:
                    sender_pass = os.environ.get('ADMIN_SMTP_PASSWORD')

                # AKILLI PORT KONTROLÜ
                port_int = int(port)
                if port_int == 465:
                    server = smtplib.SMTP_SSL(host, port_int, timeout=5)
                else:
                    server = smtplib.SMTP(host, port_int, timeout=5)
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
                return redirect(url_for('admin.verify_2fa'))
            except Exception as e:
                try:
                    current_app.logger.error(f"[admin_login 2FA mail error] {type(e).__name__}: {e}")
                except Exception:
                    pass
                flash(f'Mail gönderilemedi: {type(e).__name__}: {e}', 'danger')
                return redirect(url_for('admin.admin_login'))
        else:
            conn.close()
            flash('Yetkisiz giriş!', 'danger')
    return render_template('admin_login.html')


@admin_bp.route('/verify', methods=['GET', 'POST'])
@limiter.limit("10 per minute; 30 per hour", methods=["POST"])
def verify_2fa():
    if 'pending_user_id' not in session: return redirect(url_for('auth.login'))
    if request.method == 'POST':
        user_code = request.form['code'].strip()
        user_id = session['pending_user_id']
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT id, ad_soyad, is_admin, email, auth_code, is_blocked, plan_type FROM users WHERE id=?",
                (user_id,))
        except Exception:
            cursor.execute("SELECT id, ad_soyad, is_admin, email, auth_code FROM users WHERE id=?", (user_id,))
        user = cursor.fetchone()
        if user and str(user[4]) == str(user_code):
            cursor.execute("UPDATE users SET auth_code=NULL WHERE id=?", (user_id,))
            conn.commit()
            conn.close()
            session.pop('pending_user_id', None)
            is_blocked = user[5] if len(user) > 5 and user[5] is not None else 0
            plan_type = user[6] if len(user) > 6 and user[6] is not None else 'free'
            login_user(User(id=user[0], ad_soyad=user[1], is_admin=user[2], email=user[3], is_blocked=is_blocked,
                            plan_type=plan_type))
            if user[2] == 1: return redirect(url_for('admin.admin_users'))
            return redirect(url_for('main.dashboard'))
        else:
            conn.close()
            flash('Hatalı kod!', 'danger')
    return render_template('verify.html')


@admin_bp.route('/admin/users')
@login_required
def admin_users():
    if getattr(current_user, 'is_admin', 0) != 1: return redirect(url_for('main.dashboard'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    query = """
            SELECT u.id, \
                   u.ad_soyad, \
                   u.is_admin, \
                   u.email, \
                   u.is_blocked, \
                   u.plan_type,
                   COUNT(l.id) as total_mails, \
                   u.contract_accepted, \
                   u.contract_accepted_date
            FROM users u
                     LEFT JOIN logs l ON u.id = l.user_id
            GROUP BY u.id
            ORDER BY u.id DESC
            """
    cursor.execute(query)
    all_users = cursor.fetchall()
    user_stats = []
    for user in all_users:
        user_stats.append({
            'id': user[0], 'ad_soyad': user[1], 'is_admin': user[2], 'email': user[3],
            'is_blocked': user[4] if user[4] is not None else 0,
            'plan_type': user[5] if user[5] is not None else 'free',
            'total_mails': user[6],
            'contract': user[7],
            'contract_date': user[8]
        })
    conn.close()
    return render_template('admin_users.html', users=user_stats)


@admin_bp.route('/admin/toggle_role/<int:id>', methods=['POST'])
@login_required
def toggle_role(id):
    if current_user.is_admin != 1: return redirect(url_for('main.dashboard'))
    if id == current_user.id: return redirect(url_for('admin.admin_users'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id=?", (id,))
    user = cursor.fetchone()
    yeni_yetki = 0 if user[0] == 1 else 1
    cursor.execute("UPDATE users SET is_admin=? WHERE id=?", (yeni_yetki, id))
    conn.commit()
    conn.close()
    return redirect(url_for('admin.admin_users'))


@admin_bp.route('/admin/toggle_block/<int:id>', methods=['POST'])
@login_required
def toggle_block(id):
    if current_user.is_admin != 1: return redirect(url_for('main.dashboard'))
    if id == current_user.id: return redirect(url_for('admin.admin_users'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT is_blocked FROM users WHERE id=?", (id,))
    except Exception:
        return redirect(url_for('admin.admin_users'))
    user = cursor.fetchone()
    mevcut_durum = user[0] if user[0] is not None else 0
    yeni_durum = 1 if mevcut_durum == 0 else 0
    cursor.execute("UPDATE users SET is_blocked=? WHERE id=?", (yeni_durum, id))
    conn.commit()
    conn.close()
    return redirect(url_for('admin.admin_users'))


@admin_bp.route('/admin/delete_user/<int:id>', methods=['POST'])
@login_required
def delete_user(id):
    if current_user.is_admin != 1: return redirect(url_for('main.dashboard'))
    if id == current_user.id: return redirect(url_for('admin.admin_users'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id=?", (id,))
    cursor.execute("DELETE FROM logs WHERE user_id=?", (id,))
    cursor.execute("DELETE FROM blacklist WHERE user_id=?", (id,))
    cursor.execute("DELETE FROM settings WHERE user_id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin.admin_users'))


@admin_bp.route('/admin/site_settings', methods=['GET', 'POST'])
@login_required
def admin_site_settings():
    if current_user.is_admin != 1: return redirect(url_for('main.dashboard'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # --- 🛡️ HAYAT KURTARAN KOD BURASI ---
    # Eğer tabloda 1 numaralı satır (çekmece) yoksa, önce onu boş olarak yarat!
    cursor.execute("SELECT id FROM landing_settings WHERE id=1")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO landing_settings (id) VALUES (1)")
        conn.commit()
    # -------------------------------------

    if request.method == 'POST':
        ht = request.form.get('hero_title', '')
        hs = request.form.get('hero_subtitle', '')
        f1t, f1d = request.form.get('f1_title', ''), request.form.get('f1_desc', '')
        f2t, f2d = request.form.get('f2_title', ''), request.form.get('f2_desc', '')
        f3t, f3d = request.form.get('f3_title', ''), request.form.get('f3_desc', '')
        ft = request.form.get('footer_text', '')
        ga = request.form.get('ga_id', '').strip()
        lu = request.form.get('looker_url', '').strip()
        promo_video = request.form.get('promo_video', '').strip()

        cursor.execute("SELECT hero_image FROM landing_settings WHERE id=1")
        mevcut_resim = cursor.fetchone()
        hero_image_path = mevcut_resim[0] if mevcut_resim and mevcut_resim[0] else ""

        image_file = request.files.get('hero_image')

        if image_file and image_file.filename:
            ext = image_file.filename.rsplit('.', 1)[1].lower() if '.' in image_file.filename else 'png'
            filename = f"vitrin_gorseli.{ext}"

            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            image_file.save(filepath)
            hero_image_path = filename

        cursor.execute("""UPDATE landing_settings
                          SET hero_title=?,
                              hero_subtitle=?,
                              f1_title=?,
                              f1_desc=?,
                              f2_title=?,
                              f2_desc=?,
                              f3_title=?,
                              f3_desc=?,
                              footer_text=?,
                              ga_id=?,
                              looker_url=?,
                              hero_image=?,
                              promo_video=?
                          WHERE id = 1""",
                       (ht, hs, f1t, f1d, f2t, f2d, f3t, f3d, ft, ga, lu, hero_image_path, promo_video))
        conn.commit()
        flash('Site, Medya ve SEO ayarları başarıyla güncellendi!', 'success')

    cursor.execute("SELECT * FROM landing_settings WHERE id=1")
    landing = cursor.fetchone()
    conn.close()
    return render_template('admin_site.html', landing=landing)


@admin_bp.route('/legal-settings', methods=['GET', 'POST'])
@login_required
def admin_legal_edit():
    if getattr(current_user, 'is_admin', 0) != 1: return redirect(url_for('main.dashboard'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if request.method == 'POST':
        slug = request.form.get('slug')
        baslik = request.form.get('baslik')
        icerik = request.form.get('icerik')
        cursor.execute("UPDATE legal_texts SET baslik = ?, icerik = ? WHERE slug = ?", (baslik, icerik, slug))
        conn.commit()
        flash(f'"{baslik}" başarıyla güncellendi!', 'success')
        return redirect(url_for('admin.admin_legal_edit'))
    cursor.execute("SELECT id, slug, baslik, icerik FROM legal_texts")
    texts = cursor.fetchall()
    conn.close()
    return render_template('admin_legal_edit.html', texts=texts)


@admin_bp.route('/admin/payment_management', methods=['GET', 'POST'])
@login_required
def payment_management():
    if current_user.is_admin != 1: return redirect(url_for('main.dashboard'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if request.method == 'POST':
        metotlar = ",".join(request.form.getlist('methods'))
        iban = request.form['iban']
        banka = request.form.get('banka', '').strip()
        hesap_sahibi = request.form.get('hesap_sahibi', '').strip()
        price = request.form['price']
        paytr_id = request.form.get('paytr_id', '').strip()
        paytr_key = request.form.get('paytr_key', '').strip()
        iyzico_key = request.form.get('iyzico_key', '').strip()
        iyzico_secret = request.form.get('iyzico_secret', '').strip()

        cursor.execute("""UPDATE payment_settings
                          SET active_methods=?,
                              iban_no=?,
                              banka_adi=?,
                              hesap_sahibi=?,
                              pro_price=?,
                              paytr_id=?,
                              paytr_key=?,
                              iyzico_api_key=?,
                              iyzico_secret_key=?
                          WHERE id = 1""",
                       (metotlar, iban, banka, hesap_sahibi, price, paytr_id, paytr_key, iyzico_key, iyzico_secret))
        conn.commit()
        flash('Ödeme ve entegrasyon ayarları başarıyla güncellendi!', 'success')

    cursor.execute(
        "SELECT ur.id, u.ad_soyad, ur.talep_tarihi, ur.durum, ur.odeme_metodu FROM upgrade_requests ur JOIN users u ON ur.user_id = u.id ORDER BY ur.id DESC")
    talepler = cursor.fetchall()
    cursor.execute("SELECT * FROM payment_settings WHERE id=1")
    settings = cursor.fetchone()
    conn.close()
    return render_template('admin_payments.html', talepler=talepler, settings=settings)


@admin_bp.route('/admin/tum-rehberler')
@login_required
def admin_rehberler():
    if getattr(current_user, 'is_admin', 0) != 1: return redirect(url_for('main.dashboard'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
                   SELECT u.id,
                          g.group_name,
                          u.ad_soyad,
                          (SELECT COUNT(*) FROM contact_group_rel WHERE group_id = g.id)
                   FROM groups g
                            JOIN users u ON g.user_id = u.id
                   ORDER BY u.id ASC
                   """)
    gruplar = cursor.fetchall()
    conn.close()
    return render_template('admin_rehberler.html', gruplar=gruplar)


@admin_bp.route('/admin/reject_upgrade/<int:req_id>', methods=['GET', 'POST'])
@login_required
def reject_upgrade(req_id):
    if current_user.is_admin != 1: return redirect(url_for('main.dashboard'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE upgrade_requests SET durum='reddedildi' WHERE id=?", (req_id,))
    conn.commit()
    conn.close()
    flash('Ödeme talebi reddedildi.', 'warning')
    return redirect(url_for('admin.payment_management'))