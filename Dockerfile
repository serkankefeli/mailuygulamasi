# 1. Hafif ve hızlı bir Python sürümü kullan
FROM python:3.11-slim

# 2. Sunucu içinde çalışma klasörümüzü belirliyoruz
WORKDIR /app

# 3. Kütüphane listesini sunucuya kopyala ve kur
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. KRİTİK DÜZELTME: Tüm dosyaları (app.py, static, templates vb.) kopyala
COPY . .

# 5. Flask'ın çalışacağı portu dışarıya bildir
EXPOSE 5000

# 6. Uygulamayı canlı ortam motoru olan Gunicorn ile başlat!
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "3", "--timeout", "120", "app:app"]
