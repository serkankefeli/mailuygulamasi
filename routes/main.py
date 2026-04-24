from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify, \
    send_from_directory, session
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
    if 'temp_user_email' in session: return redirect(url_for('auth.verify_2fa'))
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
    if 'temp_user_email' in session: return redirect(url_for('auth.verify_2fa'))
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
    if 'temp_user_email' in session: return redirect(url_for('auth.verify_2fa'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM logs WHERE user_id=? ORDER BY id DESC LIMIT 100", (current_user.id,))
    logs = cursor.fetchall()
    conn.close()
    return render_template('reports.html', logs=logs)


@main_bp.route('/settings_page')
@login_required
def settings_page():
    if 'temp_user_email' in session: return redirect(url_for('auth.verify_2fa'))
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
    if 'temp_user_email' in session: return redirect(url_for('auth.verify_2fa'))

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 1. SAĞDAKİ TABLO İÇİN: Grup isimlerinin başına "KullanıcıID - " ekliyoruz
    cursor.execute("""
                   SELECT c.name,
                          c.email,
                          IFNULL(GROUP_CONCAT(c.user_id || ' - ' || g.group_name, ', '), 'Grup Yok'),
                          c.id
                   FROM contacts c
                            LEFT JOIN contact_group_rel cgr ON c.id = cgr.contact_id
                            LEFT JOIN groups g ON cgr.group_id = g.id
                   WHERE c.user_id = ?
                   GROUP BY c.id
                   ORDER BY c.id DESC
                   """, (current_user.id,))

    contact_list = [{'display_name': row[0], 'email': row[1], 'groups': row[2], 'id': row[3]} for row in
                    cursor.fetchall()]

    # 2. SOLDAKİ MENÜ İÇİN: Grup isimlerinin başına "KullanıcıID - " ekliyoruz
    cursor.execute("SELECT id, user_id || ' - ' || group_name FROM groups WHERE user_id=?", (current_user.id,))
    groups = cursor.fetchall()

    conn.close()
    return render_template('contacts.html', groups=groups, contacts=contact_list)


@main_bp.route('/add_group', methods=['POST'])
@login_required
def add_group():
    if 'temp_user_email' in session: return redirect(url_for('auth.verify_2fa'))
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
    if 'temp_user_email' in session: return redirect(url_for('auth.verify_2fa'))
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
    if 'temp_user_email' in session: return redirect(url_for('auth.verify_2fa'))
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


@main_bp.route('/test_smtp', methods=['POST'])
@login_required
def test_smtp():
    """
    Ayarlar sayfasındaki 'Test Et' butonu buraya POST eder.
    Formdaki (henüz kaydedilmemiş) SMTP bilgileri ile gerçek bir bağlantı
    kurup, login dener ve varsa gönderici adresine 1 test maili atar.
    Başarı/başarısızlık durumunu JSON olarak döndürür — UI bunu gösterir.
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    if 'temp_user_email' in session:
        return jsonify({'ok': False, 'error': '2FA doğrulaması bekleniyor.'}), 403

    host = (request.form.get('smtp_host') or '').strip()
    port_str = (request.form.get('smtp_port') or '').strip()
    user_email = (request.form.get('smtp_user') or '').strip()
    password = (request.form.get('smtp_pass') or '').strip()

    if not (host and port_str and user_email):
        return jsonify({'ok': False, 'error': 'Host, port ve e-posta zorunlu.'}), 400

    # Şifre boş ise kayıtlı (şifrelenmiş) şifreyi kullanmaya çalış.
    if not password:
        try:
            from extensions import decrypt_smtp_password
            conn = sqlite3.connect(DB_NAME)
            cur = conn.cursor()
            cur.execute("SELECT password FROM settings WHERE user_id=?", (current_user.id,))
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                password = decrypt_smtp_password(row[0]) or ''
        except Exception:
            password = ''

    if not password:
        # Son çare: adminse .env'deki ADMIN_SMTP_PASSWORD
        if getattr(current_user, 'is_admin', 0) == 1:
            password = os.environ.get('ADMIN_SMTP_PASSWORD', '') or ''

    if not password:
        return jsonify({'ok': False, 'error': 'Şifre boş — form alanına yazın ya da önce kaydedin.'}), 400

    try:
        port = int(port_str)
    except ValueError:
        return jsonify({'ok': False, 'error': f'Port geçersiz: {port_str!r}'}), 400

    # Adım adım test ediyoruz ki hangi aşamada patladığı net görünsün.
    stage = 'connect'
    try:
        server = smtplib.SMTP(host, port, timeout=10)
        stage = 'starttls'
        server.starttls()
        stage = 'login'
        server.login(user_email, password)
        stage = 'send'
        msg = MIMEMultipart()
        msg['From'] = user_email
        msg['To'] = user_email  # kendimize atalım — inbox'a düşerse tam yeşil ışık
        msg['Subject'] = "MailKamp SMTP Test"
        msg.attach(MIMEText(
            "Bu bir SMTP test mailidir. Bu mail geldiyse SMTP ayarlarınız doğru çalışıyor.",
            'plain', 'utf-8'))
        server.send_message(msg)
        server.quit()
        return jsonify({
            'ok': True,
            'message': f'✓ Başarılı — {user_email} adresine test maili gönderildi. Inbox/Spam klasörünü kontrol edin.'
        })
    except smtplib.SMTPAuthenticationError as e:
        return jsonify({
            'ok': False,
            'stage': stage,
            'error': f'Kimlik doğrulama hatası (SMTPAuthenticationError). Gmail için "App Password" kullanmalısınız (2FA açık olmalı). Detay: {e}'
        }), 200
    except smtplib.SMTPConnectError as e:
        return jsonify({'ok': False, 'stage': stage, 'error': f'Sunucuya bağlanılamadı: {e}'}), 200
    except smtplib.SMTPException as e:
        return jsonify({'ok': False, 'stage': stage, 'error': f'SMTP hatası ({type(e).__name__}): {e}'}), 200
    except (TimeoutError, socket.timeout) as e:
        return jsonify({'ok': False, 'stage': stage, 'error': f'Zaman aşımı ({stage}): port açık değil veya firewall engelliyor olabilir.'}), 200
    except Exception as e:
        return jsonify({'ok': False, 'stage': stage, 'error': f'{type(e).__name__}: {e}'}), 200


@main_bp.route('/add_blacklist', methods=['POST'])
@login_required
def add_blacklist():
    if 'temp_user_email' in session: return redirect(url_for('auth.verify_2fa'))
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
    if 'temp_user_email' in session: return redirect(url_for('auth.verify_2fa'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blacklist WHERE id=? AND user_id=?", (id, current_user.id))
    conn.commit()
    conn.close()
    return redirect(url_for('main.settings_page'))

@main_bp.route('/delete_contact/<int:contact_id>', methods=['POST'])
@login_required
def delete_contact(contact_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Önce güvenlik: Bu kişi gerçekten bu müşteriye mi ait?
    cursor.execute("SELECT id FROM contacts WHERE id = ? AND user_id = ?", (contact_id, current_user.id))
    if cursor.fetchone():
        # 1. Kişiyi gruptan kopar (İlişki tablosundan sil)
        cursor.execute("DELETE FROM contact_group_rel WHERE contact_id = ?", (contact_id,))
        # 2. Kişiyi tamamen rehberden sil
        cursor.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        conn.commit()
        flash('Kişi rehberden başarıyla silindi.', 'success')
    else:
        flash('Bu işlemi yapmaya yetkiniz yok veya kişi bulunamadı!', 'danger')

    conn.close()
    return redirect(url_for('main.contacts'))


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


@main_bp.route('/bulk_delete_contacts', methods=['POST'])
@login_required
def bulk_delete_contacts():
    # Formdan seçilen ID listesini al
    contact_ids = request.form.getlist('contact_ids')

    if not contact_ids:
        flash('Lütfen silinecek kişileri seçin.', 'warning')
        return redirect(url_for('main.contacts'))  # Eksik parantez eklendi

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    try:
        # SQL Injection'a karşı placeholder (?, ?, ?) oluşturuyoruz
        placeholders = ','.join('?' for _ in contact_ids)

        # Güvenlik: Kullanıcının sadece kendi rehberindeki ID'leri silebilmesi için current_user.id ekliyoruz
        query_params = tuple(contact_ids) + (current_user.id,)

        # 1. Önce bu kişileri bağlı oldukları gruplardan (ilişki tablosundan) kopar
        cursor.execute(f"""
            DELETE FROM contact_group_rel 
            WHERE contact_id IN ({placeholders}) 
            AND contact_id IN (SELECT id FROM contacts WHERE user_id = ?)
        """, query_params)

        # 2. Sonra kişileri rehberden (ana tablodan) tamamen sil
        cursor.execute(f"""
            DELETE FROM contacts 
            WHERE id IN ({placeholders}) AND user_id = ?
        """, query_params)

        # Silinen kişi sayısını al ve işlemi onayla
        deleted_count = cursor.rowcount
        conn.commit()
        flash(f'{deleted_count} kişi rehberden başarıyla silindi.', 'success')

    except Exception as e:
        conn.rollback()
        flash('Toplu silme işlemi sırasında bir hata oluştu.', 'danger')
    finally:
        conn.close()

    return redirect(url_for('main.contacts'))