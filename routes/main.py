from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify, \
    send_from_directory
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import sqlite3
import os
import socket
import ipaddress
import urllib.parse
from datetime import datetime
from functools import wraps

from extensions import DB_NAME, encrypt_smtp_password

main_bp = Blueprint('main', __name__)


# --- GÜVENLİK YARDIMCILARI ---
def premium_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if getattr(current_user, 'is_admin', 0) != 1 and getattr(current_user, 'plan_type', 'free') == 'free':
            flash('🌟 Bu özellik PRO pakete özeldir! Lütfen planınızı yükseltin.', 'warning')
            return redirect(url_for('main.upgrade'))
        return f(*args, **kwargs)

    return decorated_function


def is_safe_webhook_url(url):
    if not url: return False
    try:
        parsed = urllib.parse.urlparse(url)
    except:
        return False
    if parsed.scheme not in ('http', 'https'): return False
    host = parsed.hostname
    if not host: return False
    if host.lower() in ('metadata.google.internal', 'metadata', 'instance-data'): return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (
                ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False
    return True


# --- ROTALAR ---
@main_bp.route('/')
def index():
    if current_user.is_authenticated: return redirect(url_for('main.dashboard'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM landing_settings WHERE id=1")
    landing = cursor.fetchone()
    conn.close()
    return render_template('index.html', landing=landing)


@main_bp.route('/dashboard')
@login_required
def dashboard():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, group_name FROM groups WHERE user_id=?", (current_user.id,))
    groups = cursor.fetchall()
    cursor.execute("SELECT id, template_name FROM templates WHERE user_id=? ORDER BY id DESC", (current_user.id,))
    templates = cursor.fetchall()

    cursor.execute("""
                   SELECT COUNT(*)                                                       as total,
                          SUM(CASE WHEN durum = 'İletildi (Okunmadı)' THEN 1 ELSE 0 END) as unread,
                          SUM(CASE WHEN durum = 'Okundu' THEN 1 ELSE 0 END)              as read_count,
                          SUM(CASE WHEN durum = 'Hata' THEN 1 ELSE 0 END)                as error_count,
                          SUM(CASE WHEN durum = 'Atlandı' THEN 1 ELSE 0 END)             as skipped_count,
                          sent_this_month
                   FROM (SELECT durum,
                                CASE
                                    WHEN strftime('%Y-%m', tarih) = strftime('%Y-%m', date('now')) THEN 1
                                    ELSE 0 END as sent_this_month
                         FROM logs
                         WHERE user_id = ?)
                   """, (current_user.id,))
    stats_row = cursor.fetchone()
    stats = {
        'total': stats_row[0] or 0, 'unread': stats_row[1] or 0, 'read': stats_row[2] or 0,
        'error': stats_row[3] or 0, 'skipped': stats_row[4] or 0, 'sent_this_month': stats_row[5] or 0,
    }

    cursor.execute(
        "SELECT substr(tarih, 1, 10) as day, COUNT(*) FROM logs WHERE user_id=? GROUP BY day ORDER BY day DESC LIMIT 7",
        (current_user.id,))
    trend_data_raw = cursor.fetchall()
    stats['trend_labels'] = [row[0] for row in reversed(trend_data_raw)]
    stats['trend_counts'] = [row[1] for row in reversed(trend_data_raw)]
    conn.close()
    return render_template('dashboard.html', groups=groups, stats=stats, templates=templates)


@main_bp.route('/reports')
@login_required
def reports():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM logs WHERE user_id=? ORDER BY id DESC LIMIT 100", (current_user.id,))
    logs = cursor.fetchall()
    conn.close()
    return render_template('reports.html', logs=logs)


@main_bp.route('/settings_page')
@login_required
def settings_page():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM settings WHERE user_id=?", (current_user.id,))
    settings = cursor.fetchone()
    cursor.execute("SELECT * FROM blacklist WHERE user_id=? ORDER BY id DESC", (current_user.id,))
    blacklist = cursor.fetchall()
    api_key = None
    try:
        cursor.execute("SELECT api_key FROM users WHERE id=?", (current_user.id,))
        res = cursor.fetchone()
        if res: api_key = res[0]
    except Exception:
        pass
    conn.close()
    return render_template('settings.html', settings=settings, blacklist=blacklist, api_key=api_key)


@main_bp.route('/contacts')
@login_required
def contacts():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, group_name FROM groups WHERE user_id=?", (current_user.id,))
    groups = cursor.fetchall()
    cursor.execute(
        "SELECT c.id, c.name, c.email, IFNULL(GROUP_CONCAT(g.group_name, ', '), '') as groups FROM contacts c LEFT JOIN contact_group_rel cgr ON c.id = cgr.contact_id LEFT JOIN groups g ON cgr.group_id = g.id WHERE c.user_id = ? GROUP BY c.id",
        (current_user.id,))
    contact_list = [{'id': row[0], 'name': row[1], 'email': row[2], 'groups': row[3]} for row in cursor.fetchall()]
    conn.close()
    return render_template('contacts.html', groups=groups, contacts=contact_list)


@main_bp.route('/add_group', methods=['POST'])
@login_required
def add_group():
    group_name = request.form.get('group_name')
    if group_name:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO groups (user_id, group_name) VALUES (?, ?)",
                           (current_user.id, group_name.strip()))
            conn.commit()
            flash(f'"{group_name}" başarıyla oluşturuldu!', 'success')
        except sqlite3.IntegrityError:
            flash('Bu isimde bir grubunuz zaten var!', 'warning')
        finally:
            conn.close()
    return redirect(request.referrer or url_for('main.contacts'))


@main_bp.route('/delete_group/<int:group_id>', methods=['POST'])
@login_required
def delete_group(group_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM groups WHERE id=? AND user_id=?", (group_id, current_user.id))
    cursor.execute("DELETE FROM contact_group_rel WHERE group_id=?", (group_id,))
    conn.commit()
    conn.close()
    flash('Grup başarıyla silindi.', 'success')
    return redirect(request.referrer or url_for('main.contacts'))


@main_bp.route('/save_settings', methods=['POST'])
@login_required
def save_settings():
    host, port = request.form['smtp_host'].strip(), request.form['smtp_port'].strip()
    user_email, password_plain = request.form['smtp_user'].strip(), request.form['smtp_pass'].strip()
    webhook_url = request.form.get('webhook_url', '').strip()

    if current_user.is_admin != 1 and current_user.plan_type == 'free': webhook_url = ""
    if webhook_url and not is_safe_webhook_url(webhook_url):
        flash('Webhook URL geçersiz ya da iç ağ adresi.', 'danger')
        webhook_url = ""

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, password FROM settings WHERE user_id=?", (current_user.id,))
    existing = cursor.fetchone()

    password_to_store = encrypt_smtp_password(password_plain) if password_plain else (existing[1] if existing else '')

    if existing:
        try:
            cursor.execute(
                "UPDATE settings SET host=?, port=?, user_email=?, password=?, webhook_url=? WHERE user_id=?",
                (host, port, user_email, password_to_store, webhook_url, current_user.id))
        except:
            cursor.execute("UPDATE settings SET host=?, port=?, user_email=?, password=? WHERE user_id=?",
                           (host, port, user_email, password_to_store, current_user.id))
    else:
        try:
            cursor.execute(
                "INSERT INTO settings (user_id, host, port, user_email, password, webhook_url) VALUES (?, ?, ?, ?, ?, ?)",
                (current_user.id, host, port, user_email, password_to_store, webhook_url))
        except:
            cursor.execute("INSERT INTO settings (user_id, host, port, user_email, password) VALUES (?, ?, ?, ?, ?)",
                           (current_user.id, host, port, user_email, password_to_store))
    conn.commit()
    conn.close()
    flash('Ayarlar başarıyla kaydedildi!', 'success')
    return redirect(url_for('main.settings_page'))


@main_bp.route('/add_blacklist', methods=['POST'])
@login_required
def add_blacklist():
    email = request.form.get('blacklist_emails')
    if email and "@" in email:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO blacklist (user_id, email) VALUES (?, ?)",
                           (current_user.id, email.strip().lower()))
            conn.commit()
            flash('Kara listeye eklendi.', 'success')
        except sqlite3.IntegrityError:
            flash('Bu e-posta zaten var.', 'warning')
        finally:
            conn.close()
    return redirect(url_for('main.settings_page'))


@main_bp.route('/remove_blacklist/<int:id>', methods=['POST'])
@login_required
def remove_blacklist(id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blacklist WHERE id=? AND user_id=?", (id, current_user.id))
    conn.commit()
    conn.close()
    return redirect(url_for('main.settings_page'))


@main_bp.route('/upload_logo', methods=['POST'])
@login_required
def upload_logo():
    if getattr(current_user, 'is_admin', 0) != 1: return redirect(url_for('main.dashboard'))
    file = request.files.get('logo_file')
    if not file or file.filename == '': return redirect(request.referrer)

    import mimetypes
    mime_type, _ = mimetypes.guess_type(file.filename)
    if mime_type not in {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}:
        flash('Sadece resim dosyaları!', 'danger')
        return redirect(request.referrer)

    save_path = os.path.join(current_app.root_path, 'static', 'images', 'logo.png')
    file.save(save_path)
    flash('Yeni logo yüklendi!', 'success')
    return redirect(request.referrer)


@main_bp.route('/save_template', methods=['POST'])
@login_required
@premium_required
def save_template():
    name, subject, body = request.form.get('template_name'), request.form.get('subject'), request.form.get('body')
    if name and subject and body:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO templates (user_id, template_name, subject, body) VALUES (?, ?, ?, ?)",
                       (current_user.id, name, subject, body))
        conn.commit()
        conn.close()
        flash('Şablon kaydedildi!', 'success')
    return redirect(url_for('main.dashboard'))


@main_bp.route('/api/get_template/<int:tpl_id>')
@login_required
def get_template(tpl_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT subject, body FROM templates WHERE id=? AND user_id=?", (tpl_id, current_user.id))
    tpl = cursor.fetchone()
    conn.close()
    if tpl: return jsonify({'subject': tpl[0], 'body': tpl[1]})
    return jsonify({'error': 'Bulunamadi'}), 404


@main_bp.route('/delete_template/<int:tpl_id>', methods=['POST'])
@login_required
def delete_template(tpl_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM templates WHERE id=? AND user_id=?", (tpl_id, current_user.id))
    conn.commit()
    conn.close()
    return redirect(url_for('main.dashboard'))


@main_bp.route('/upgrade', methods=['GET', 'POST'])
@login_required
def upgrade():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if request.method == 'POST':
        if request.form.get('contract_accepted'):
            cursor.execute("UPDATE users SET contract_accepted=1, contract_accepted_date=? WHERE id=?",
                           (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), current_user.id))
        cursor.execute("INSERT INTO upgrade_requests (user_id, talep_tarihi, odeme_metodu) VALUES (?, ?, ?)",
                       (current_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'Havale/EFT'))
        conn.commit()
        conn.close()
        flash('Ödeme bildiriminiz alındı!', 'success')
        return redirect(url_for('main.dashboard'))

    cursor.execute("SELECT slug, icerik FROM legal_texts")
    legal_data = {row[0]: row[1] for row in cursor.fetchall()}
    cursor.execute("SELECT * FROM payment_settings WHERE id=1")
    p_settings = cursor.fetchone()
    conn.close()
    return render_template('upgrade.html', settings=p_settings, legal=legal_data)


@main_bp.route('/uploads/<path:filename>')
def serve_uploads(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)