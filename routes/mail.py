from flask import Blueprint, request, redirect, url_for, flash, current_app, jsonify, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import sqlite3
import os
import threading
import time
import smtplib
import urllib.parse
import secrets
import io
import hmac
import hashlib
import logging
import pandas as pd
import requests
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email import encoders
from pathlib import Path

from extensions import DB_NAME, decrypt_smtp_password, csrf, limiter
from routes.main import premium_required, is_safe_webhook_url

mail_bp = Blueprint('mail', __name__)


def generate_unsubscribe_token(secret_key, user_id, email):
    msg = f"{user_id}:{email.lower()}".encode('utf-8')
    return hmac.new(secret_key.encode('utf-8'), msg, hashlib.sha256).hexdigest()[:32]


def verify_unsubscribe_token(secret_key, user_id, email, token):
    if not token: return False
    return hmac.compare_digest(generate_unsubscribe_token(secret_key, user_id, email), token)


def is_safe_redirect_url(target_url, host_url):
    if not target_url: return False
    try:
        parsed = urllib.parse.urlparse(target_url)
    except:
        return False
    if parsed.scheme not in ('http', 'https'): return False
    host_host = urllib.parse.urlparse(host_url).netloc.lower()
    return parsed.netloc.lower() in {host_host}


# --- ARKA PLAN GÖNDERİM MOTORU ---
def background_mailer(user_id, email_list, subject, body, attachment_paths, video_link, cover_path, settings, base_url,
                      is_free_plan, secret_key):
    host, port, sender_email = settings[2], settings[3], settings[4]
    sender_pass = os.environ.get('ADMIN_SMTP_PASSWORD') if user_id == 1 else decrypt_smtp_password(settings[5])

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM blacklist WHERE user_id=?", (user_id,))
        blacklist = set(row[0] for row in cursor.fetchall())
        cursor.execute("SELECT email, name FROM contacts WHERE user_id=?", (user_id,))
        contacts_dict = {row[0]: row[1] for row in cursor.fetchall()}

    try:
        server = smtplib.SMTP(host, int(port), timeout=15)
        server.starttls()
        server.login(sender_email, sender_pass)

        for alici in email_list:
            if alici in blacklist:
                with sqlite3.connect(DB_NAME) as conn:
                    conn.execute(
                        "INSERT INTO logs (user_id, tarih, alici, konu, durum, detay) VALUES (?, ?, ?, ?, ?, ?)",
                        (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), alici, subject, "Atlandı",
                         "Kara Listede"))
                continue

            kisisel_body = body.replace("{isim}", contacts_dict.get(alici,
                                                                    "Değerli Müşterimiz") if not is_free_plan else "Değerli Müşterimiz")

            with sqlite3.connect(DB_NAME) as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO logs (user_id, tarih, alici, konu, durum, detay) VALUES (?, ?, ?, ?, ?, ?)",
                               (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), alici, subject,
                                "Gönderiliyor...", "Kuyrukta"))
                log_id = cursor.lastrowid

            try:
                video_html = ""
                if video_link:
                    tracking_link = video_link if is_free_plan else f"{base_url}track?l={log_id}&u={urllib.parse.quote(video_link, safe='')}"
                    if cover_path:
                        video_html = f'''<div style="text-align: center; margin: 30px 0;"><a href="{tracking_link}"><img src="cid:video_cover" style="max-width: 100%; border-radius: 8px;"></a><br><a href="{tracking_link}" style="background-color: #e74c3c; color: #fff; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block; margin-top: 15px;">▶ İncele</a></div>'''
                    else:
                        video_html = f'''<div style="text-align: center; margin: 30px 0;"><a href="{tracking_link}" style="background-color: #2980b9; color: #fff; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">İncele</a></div>'''

                unsub_token = generate_unsubscribe_token(secret_key, user_id, alici)
                unsubscribe_link = f"{base_url}unsubscribe?u={user_id}&e={urllib.parse.quote(alici, safe='')}&t={unsub_token}"

                reklam_html = f'''<div style="text-align: center; margin-top: 20px;"><p style="font-size: 11px; color: #95a5a6;">⚡ <a href="{base_url}" style="color: #2980b9; text-decoration: none;">MailKamp</a> ile gönderilmiştir.</p></div>''' if is_free_plan else ""
                tracking_pixel = f'<img src="{base_url.rstrip("/")}/track_open/{log_id}" width="1" height="1" style="border:0; opacity:0.01;" />'

                kurumsal_html = f'''<!DOCTYPE html><html><body style="background-color: #f4f7f6; font-family: sans-serif;"><table width="100%" cellpadding="40"><tr><td align="center"><table width="600" style="background-color: #ffffff; border-radius: 10px;"><tr><td style="background-color: #1a2b3c; padding: 30px; text-align: center;"><h1 style="color: #ffffff; margin: 0;">MailKamp</h1></td></tr><tr><td style="padding: 40px 30px; color: #333333;">{kisisel_body}{video_html}</td></tr><tr><td style="background-color: #ecf0f1; padding: 20px 30px; text-align: center;"><p style="font-size: 12px; color: #95a5a6;">Abonelikten ayrılmak için <a href="{unsubscribe_link}">tıklayınız</a>.</p>{reklam_html}{tracking_pixel}</td></tr></table></td></tr></table></body></html>'''

                msg = MIMEMultipart('related')
                msg['From'], msg['To'], msg['Subject'] = sender_email, alici, subject
                msg.attach(MIMEText(kurumsal_html, 'html'))

                if video_link and cover_path:
                    try:
                        with open(cover_path, 'rb') as img_file:
                            cover_img = MIMEImage(img_file.read())
                            cover_img.add_header('Content-ID', '<video_cover>')
                            msg.attach(cover_img)
                    except Exception:
                        pass

                for path in attachment_paths:
                    part = MIMEBase('application', "octet-stream")
                    with open(path, 'rb') as _af: part.set_payload(_af.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f'attachment; filename={os.path.basename(path)}')
                    msg.attach(part)

                server.send_message(msg)
                with sqlite3.connect(DB_NAME) as conn:
                    conn.execute("UPDATE logs SET durum=?, detay=? WHERE id=?",
                                 ("İletildi (Okunmadı)", "Kutuya ulaştı.", log_id))
                time.sleep(1)

            except Exception as e:
                with sqlite3.connect(DB_NAME) as conn:
                    conn.execute("UPDATE logs SET durum=?, detay=? WHERE id=?",
                                 ("Hata", f"İletilemedi: {str(e)[:50]}", log_id))
        server.quit()
    except Exception as e:
        logging.exception("SMTP Hatası: %s", e)

    for path in attachment_paths + ([cover_path] if cover_path else []):
        if Path(path).exists(): Path(path).unlink()


@mail_bp.route('/send_mail', methods=['POST'])
@login_required
def send_mail():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM settings WHERE user_id=?", (current_user.id,))
    settings = cursor.fetchone()
    conn.close()

    if not settings or not settings[4]:
        flash('Lütfen önce SMTP ayarlarınızı yapılandırın!', 'danger')
        return redirect(url_for('main.settings_page'))

    email_list = [e.strip().lower() for e in request.form.get('emails', '').replace(",", "\n").split("\n") if "@" in e]

    if selected_group_id := request.form.get('target_group'):
        conn_group = sqlite3.connect(DB_NAME)
        cursor_group = conn_group.cursor()
        cursor_group.execute('''SELECT c.email
                                FROM contacts c
                                         JOIN contact_group_rel cgr ON c.id = cgr.contact_id
                                WHERE cgr.group_id = ?
                                  AND c.user_id = ?''', (selected_group_id, current_user.id))
        email_list.extend([row[0] for row in cursor_group.fetchall()])
        conn_group.close()

    email_list = list(set(email_list))
    with sqlite3.connect(DB_NAME) as conn_bl:
        blacklisted = [row[0].strip().lower() for row in
                       conn_bl.execute("SELECT email FROM blacklist WHERE user_id = ?", (current_user.id,)).fetchall()]

    email_list = [e for e in email_list if e not in blacklisted]
    if not email_list:
        flash('Gönderilecek geçerli e-posta kalmadı.', 'warning')
        return redirect(url_for('main.dashboard'))

    is_free_plan = (getattr(current_user, 'is_admin', 0) != 1 and getattr(current_user, 'plan_type', 'free') == 'free')

    if is_free_plan:
        with sqlite3.connect(DB_NAME) as conn:
            sent_count = conn.execute("SELECT sent_this_month FROM users WHERE id=?", (current_user.id,)).fetchone()[
                             0] or 0
            if sent_count + len(email_list) > 3000:
                flash(f'Aylık 3.000 limitini aşıyorsunuz!', 'danger')
                return redirect(url_for('main.dashboard'))
            conn.execute("UPDATE users SET sent_this_month = sent_this_month + ? WHERE id=?",
                         (len(email_list), current_user.id))

    test_password = os.environ.get('ADMIN_SMTP_PASSWORD') if current_user.id == 1 else decrypt_smtp_password(
        settings[5])
    try:
        test_server = smtplib.SMTP(settings[2], int(settings[3]), timeout=5)
        test_server.starttls()
        test_server.login(settings[4], test_password)
        test_server.quit()
    except Exception:
        flash('SMTP bağlantı hatası! Bilgilerinizi kontrol edin.', 'danger')
        return redirect(url_for('main.settings_page'))

    cover_path = None
    if cover_file := request.files.get('video_cover'):
        if cover_file.filename:
            cover_path = os.path.join(current_app.config['UPLOAD_FOLDER'], secure_filename(cover_file.filename))
            cover_file.save(cover_path)

    attachment_paths = []
    for file in request.files.getlist('attachment'):
        if file.filename:
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(filepath)
            attachment_paths.append(filepath)

    delay = 0
    if send_time_str := request.form.get('send_time'):
        try:
            send_time = datetime.strptime(send_time_str,
                                          '%Y-%m-%dT%H:%M' if len(send_time_str) == 16 else '%Y-%m-%dT%H:%M:%S')
            # Kullanıcı Türkiye saatiyle girer. Sunucu UTC'deyse 3 saat ekleyerek karşılaştır.
            # TZ env değişkeni ayarlıysa sunucu saatine güvenir, yoksa +3 (TRT) varsayılır.
            try:
                from zoneinfo import ZoneInfo
                tz_name = os.environ.get('TZ', 'Europe/Istanbul')
                now_local = datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
            except Exception:
                # Eski Python veya tzdata yoksa: manuel +3 offset
                now_local = datetime.utcnow() + timedelta(hours=3)
            if send_time > now_local:
                delay = (send_time - now_local).total_seconds()
        except Exception:
            pass

    # Tracking/unsubscribe linkleri için public URL. PUBLIC_BASE_URL env ayarlanmışsa onu kullan,
    # yoksa request.host_url (reverse proxy arkasında yanlış olabilir — ProxyFix bunu düzeltir).
    _configured_base = current_app.config.get('PUBLIC_BASE_URL')
    _base_url = (_configured_base + '/') if _configured_base else request.host_url

    args = (current_user.id, email_list, request.form['subject'], request.form['body'], attachment_paths,
            request.form.get('video_link', '').strip(), cover_path, settings, _base_url, is_free_plan,
            current_app.secret_key)

    if delay > 0:
        threading.Timer(delay, background_mailer, args=args).start()
    else:
        threading.Thread(target=background_mailer, args=args).start()

    flash('Kampanya başarıyla başlatıldı/zamanlandı!', 'success')
    return redirect(url_for('main.dashboard'))


@mail_bp.route('/track')
def track():
    log_id, target_url = request.args.get('l'), request.args.get('u')
    if log_id:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("UPDATE logs SET okundu=1, okunma_tarihi=?, durum=?, detay=? WHERE id=?",
                         (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Okundu", "Linke tıklandı!", log_id))
            log_data = conn.execute("SELECT user_id, alici, konu FROM logs WHERE id=?", (log_id,)).fetchone()
            if log_data:
                user_id, alici, konu = log_data
                u_data = conn.execute("SELECT plan_type, is_admin FROM users WHERE id=?", (user_id,)).fetchone()
                if u_data and (u_data[1] == 1 or u_data[0] == 'pro'):
                    settings_row = conn.execute("SELECT webhook_url FROM settings WHERE user_id=?",
                                                (user_id,)).fetchone()
                    if settings_row and settings_row[0] and is_safe_webhook_url(settings_row[0]):
                        try:
                            requests.post(settings_row[0],
                                          json={"event": "email_opened", "email": alici, "subject": konu,
                                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, timeout=3)
                        except:
                            pass
    if target_url and is_safe_redirect_url(target_url, request.host_url): return redirect(target_url)
    return redirect(url_for('auth.login'))


@mail_bp.route('/track_open/<int:log_id>')
@csrf.exempt
def track_open(log_id):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute(
                "UPDATE logs SET okundu = 1, okunma_tarihi = ?, durum = 'Okundu', detay = 'Kullanıcı e-postayı açtı.' WHERE id = ? AND durum != 'Okundu'",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), log_id))
    except Exception:
        pass
    response = send_file(io.BytesIO(
        b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'),
                         mimetype='image/gif')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    return response


@mail_bp.route('/export_logs')
@login_required
@premium_required
def export_logs():
    with sqlite3.connect(DB_NAME) as conn:
        df = pd.read_sql_query(
            "SELECT tarih as Tarih, alici as 'E-Posta Adresi', konu as Konu, durum as Durum, detay as Detay, okunma_tarihi as 'Tıklanma Zamanı' FROM logs WHERE user_id=? ORDER BY id DESC",
            conn, params=(current_user.id,))
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False,
                                                                          sheet_name='Gonderim_Raporu')
    output.seek(0)
    return send_file(output, download_name=f"MailKamp_Raporu_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                     as_attachment=True)


@mail_bp.route('/import_contacts', methods=['POST'])
@login_required
@premium_required
def import_contacts():
    file = request.files.get('excel_file')
    if not file or not file.filename.endswith(('.xlsx', '.xls')):
        flash('Geçerli bir Excel dosyası yükleyin!', 'danger')
        return redirect(url_for('main.contacts'))
    try:
        df = pd.read_excel(file)
        df.columns = df.columns.str.strip().str.lower()
        if not all(col in df.columns for col in ['ad', 'email', 'grup']):
            flash('Excel dosyasında "Ad", "Email" ve "Grup" sütunları olmalı.', 'danger')
            return redirect(url_for('main.contacts'))

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        basarili_kayit = 0
        for _, row in df.iterrows():
            ad, email, grup_adi = str(row['ad']).strip(), str(row['email']).strip().lower(), str(row['grup']).strip()
            if email and '@' in email and ad != 'nan':
                cursor.execute("INSERT OR IGNORE INTO groups (user_id, group_name) VALUES (?, ?)",
                               (current_user.id, grup_adi))
                group_id = cursor.execute("SELECT id FROM groups WHERE user_id=? AND group_name=?",
                                          (current_user.id, grup_adi)).fetchone()[0]
                cursor.execute("INSERT OR IGNORE INTO contacts (user_id, name, email) VALUES (?, ?, ?)",
                               (current_user.id, ad, email))
                contact_id = cursor.execute("SELECT id FROM contacts WHERE user_id=? AND email=?",
                                            (current_user.id, email)).fetchone()[0]
                cursor.execute("INSERT OR IGNORE INTO contact_group_rel (contact_id, group_id) VALUES (?, ?)",
                               (contact_id, group_id))
                basarili_kayit += 1
        conn.commit()
        conn.close()
        flash(f'{basarili_kayit} kişi içeri aktarıldı.', 'success')
    except Exception as e:
        flash(f'Hata: {str(e)}', 'danger')
    return redirect(url_for('main.contacts'))


@mail_bp.route('/api/send', methods=['POST'])
@csrf.exempt
@limiter.limit("30 per minute")
def api_send():
    api_key = request.headers.get('X-API-KEY')
    if not api_key: return jsonify({'error': 'X-API-KEY basligi eksik!'}), 401

    conn = sqlite3.connect(DB_NAME)
    user = conn.execute("SELECT id, plan_type, is_admin FROM users WHERE api_key=?", (api_key,)).fetchone()
    if not user: return jsonify({'error': 'Gecersiz API Anahtari!'}), 401
    if user[2] != 1 and user[1] == 'free': return jsonify({'error': 'API Kullanimi yalnizca PRO pakete ozeldir.'}), 403

    settings = conn.execute("SELECT * FROM settings WHERE user_id=?", (user[0],)).fetchone()
    conn.close()
    if not settings or not settings[4]: return jsonify({'error': 'SMTP ayarlariz eksik.'}), 400

    data = request.get_json()
    if not data or not all(k in data for k in ('to', 'subject', 'body')): return jsonify(
        {'error': 'Gerekli alanlar eksik'}), 400
    email_list = [e.strip().lower() for e in data['to'] if "@" in e]
    if not email_list: return jsonify({'error': 'Gecerli e-posta bulunamadi.'}), 400

    _configured_base = current_app.config.get('PUBLIC_BASE_URL')
    _base_url = (_configured_base + '/') if _configured_base else request.host_url

    threading.Thread(target=background_mailer,
                     args=(user[0], email_list, data['subject'], data['body'], [], None, None, settings,
                           _base_url, False, current_app.secret_key)).start()
    return jsonify({'success': True, 'message': f'{len(email_list)} alici isleme alindi.'}), 200


@mail_bp.route('/generate_api_key', methods=['POST'])
@login_required
@premium_required
def generate_api_key():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE users SET api_key=? WHERE id=?", (secrets.token_hex(24), current_user.id))
    flash('Yeni Güvenlik API Anahtarınız oluşturuldu!', 'success')
    return redirect(url_for('main.settings_page'))


@mail_bp.route('/unsubscribe')
def unsubscribe():
    user_id, email, token = request.args.get('u'), request.args.get('e'), request.args.get('t')
    if not (user_id and email): return "Geçersiz link.", 400
    try:
        user_id_int = int(user_id)
    except ValueError:
        return "Geçersiz link.", 400

    if not verify_unsubscribe_token(current_app.secret_key, user_id_int, email, token):
        return "Geçersiz veya süresi dolmuş abonelikten çıkış bağlantısı.", 403

    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT OR IGNORE INTO blacklist (user_id, email) VALUES (?, ?)",
                     (user_id_int, email.strip().lower()))
    return f'''<!DOCTYPE html><html><body><div style="text-align:center; padding:50px; font-family:sans-serif;"><h2>Abonelikten Ayrıldınız</h2><p>{email} başarıyla listemizden çıkarıldı.</p></div></body></html>'''