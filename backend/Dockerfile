FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Зависимости
COPY backend/requirements.txt .
RUN pip install --no-cache-dir \
    --trusted-host pypi.org \
    --trusted-host pypi.python.org \
    --trusted-host files.pythonhosted.org \
    -r requirements.txt

# Код приложения
COPY backend/ .
# Миграции БД (из корня проекта)
COPY migrations/ /app/migrations/

# Папки для данных
RUN mkdir -p /app/data/cache /app/data/input

# Применяем миграции и запускаем сервер
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
