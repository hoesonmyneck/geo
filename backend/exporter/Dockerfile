# Изолированный выгружатель ЦОССУ.
#
# Внутри контейнера поднимается туннель до корпоративного VPN и выполняется
# запрос к базе proon. Туннель живёт в сетевом неймспейсе ЭТОГО контейнера и
# не влияет на маршруты хоста — сайт, сертификаты Caddy и обращения к 2ГИС
# продолжают ходить обычным путём.
#
# Требует при запуске:  --cap-add NET_ADMIN --device /dev/net/tun
FROM python:3.11-slim

# vpnc-scripts нужен обязательно: без него openconnect поднимет туннель,
# но не пропишет маршруты (в Debian он идёт в Recommends, а мы ставим
# без рекомендованных пакетов).
RUN apt-get update && apt-get install -y --no-install-recommends \
        openconnect \
        vpnc-scripts \
        iproute2 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "psycopg[binary]==3.2.3" openpyxl==3.1.5

# Контекст сборки — корень репозитория:
#   docker build -t cossu-exporter -f backend/exporter/Dockerfile .
WORKDIR /app
COPY backend/exporter/vpn_up.sh /usr/local/bin/vpn-up
COPY backend/exporter/vpn_down.sh /usr/local/bin/vpn-down
COPY backend/exporter/vpn_check.py /app/vpn_check.py
COPY backend/exporter/server.py /app/server.py
COPY backend/worker/export_cossu_from_proon.py /app/export_cossu_from_proon.py
COPY backend/worker/sql/cossu_export.sql /app/cossu_export.sql
RUN chmod +x /usr/local/bin/vpn-up /usr/local/bin/vpn-down

# Раскладка внутри контейнера отличается от репозитория — указываем явно.
# /data/input — общий том с бэкендом (там же он видит его как /app/data/input).
ENV COSSU_SQL_PATH=/app/cossu_export.sql \
    COSSU_OUT_DIR=/data/input \
    COSSU_STATE_PATH=/data/input/cossu_export_state.json \
    EXPORTER_PORT=8081 \
    PYTHONUNBUFFERED=1

EXPOSE 8081

# Диагностику можно запустить вручную:
#   docker compose run --rm cossu-exporter python /app/vpn_check.py
CMD ["python", "/app/server.py"]
