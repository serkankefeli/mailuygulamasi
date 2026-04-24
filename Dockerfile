# 1. Hafif ve hızlı bir Python sürümü kullan
FROM python:3.11-slim

# Zaman dilimi için tzdata (datetime karşılaştırmalarında TRT için gerekli)
ENV TZ=Europe/Istanbul
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -sf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. Sunucu içinde çalışma klasörümüzü belirliyoruz
WORKDIR /app

# 3. Kütüphane listesini sunucuya kopyala ve kur
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Tüm dosyaları (app.py, static, templates vb.) kopyala
COPY . .

# 5. Kalıcı veriler için klasörleri hazırla (docker-compose volume bunların üstüne mount edecek)
RUN mkdir -p /app/instance /app/uploads /app/static/images

# 6. Flask'ın çalışacağı portu dışarıya bildir
EXPOSE 5000

# 7. Uygulamayı Gunicorn ile başlat
#    --workers 3: SQLite için 1-2 worker daha güvenli olur ama 3 de çalışır (WAL mode yoksa lock riski var).
#    Eğer çok sık "database is locked" hatası alırsan --workers 1 yap.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "3", "--timeout", "120", "--access-logfile", "-", "app:app"]
