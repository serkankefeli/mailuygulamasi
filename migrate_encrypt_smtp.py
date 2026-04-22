"""
Tek seferlik migrasyon scripti: settings.password alanındaki mevcut düz metin
SMTP şifrelerini Fernet ile şifreler.

Kullanım:
    1. .env dosyasında MAILKAMP_FERNET_KEY tanımlı olmalı.
    2. DB'nin bir yedeğini al:  cp web_mailer_v6.db web_mailer_v6.db.bak
    3. python migrate_encrypt_smtp.py
    4. Script idempotenttir — zaten şifrelenmiş kayıtlar atlanır.
"""
import os
import sqlite3
import sys

from dotenv import load_dotenv
from cryptography.fernet import Fernet, InvalidToken

load_dotenv()

DB_NAME = os.environ.get('DB_NAME', 'web_mailer_v6.db')
FERNET_KEY = os.environ.get('MAILKAMP_FERNET_KEY')

if not FERNET_KEY:
    print("HATA: MAILKAMP_FERNET_KEY .env'de tanımlı değil.")
    print("Üretmek için: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
    sys.exit(1)

fernet = Fernet(FERNET_KEY.encode() if isinstance(FERNET_KEY, str) else FERNET_KEY)


def is_already_encrypted(value: str) -> bool:
    """Fernet ciphertext geçerli mi? Geçerliyse zaten şifrelenmiş demektir."""
    if not value:
        return False
    try:
        fernet.decrypt(value.encode('utf-8'))
        return True
    except (InvalidToken, ValueError, TypeError):
        return False


def main():
    if not os.path.exists(DB_NAME):
        print(f"HATA: {DB_NAME} bulunamadı.")
        sys.exit(1)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT id, user_id, password FROM settings")
    rows = cursor.fetchall()

    migrated, already_enc, empty = 0, 0, 0
    for row_id, user_id, password in rows:
        if not password:
            empty += 1
            continue
        if is_already_encrypted(password):
            already_enc += 1
            continue
        encrypted = fernet.encrypt(password.encode('utf-8')).decode('utf-8')
        cursor.execute("UPDATE settings SET password=? WHERE id=?", (encrypted, row_id))
        migrated += 1
        print(f"  [+] user_id={user_id} şifrelendi")

    conn.commit()
    conn.close()

    print()
    print(f"=== Özet ===")
    print(f"  Şifrelenen kayıt : {migrated}")
    print(f"  Zaten şifreli    : {already_enc}")
    print(f"  Boş kayıt        : {empty}")
    print(f"  Toplam           : {len(rows)}")
    print()
    print("Migrasyon tamamlandı. Artık uygulamayı yeniden başlatabilirsiniz.")


if __name__ == '__main__':
    main()
