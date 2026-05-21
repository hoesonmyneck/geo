# Geo Platform

Геокодирование адресов Казахстана + интерактивная карта с демографической статистикой.

## Архитектура

```
geo/
├── backend/          # FastAPI + SQLAlchemy + Alembic
│   ├── app/
│   │   ├── api/routes/    # auth, users, places, imports
│   │   ├── core/          # config, security, deps
│   │   ├── db/            # models, session
│   │   └── geocoder/      # geocode.py, normalize.py, cache.py
│   ├── worker/            # geocoding worker (→ PostgreSQL)
│   └── migrate_parquet.py # разовый импорт из parquet
├── frontend/         # Чистый HTML/JS/Leaflet (Caddy статика)
├── migrations/       # Alembic миграции БД
├── src/              # Оригинальные скрипты пайплайна (parquet)
├── data/
│   ├── input/        # xlsx-файлы (не в git)
│   └── pbf/          # OSM PBF файлы (не в git)
├── docker-compose.yml
├── Caddyfile
├── deploy.sh
└── .env              # локальные секреты (не в git)
```

## Быстрый старт (dev)

```bash
# 1. Скопировать .env и настроить пароли
cp .env.example .env

# 2. Запустить PostgreSQL + Backend + Frontend
docker compose up postgres backend caddy -d

# 3. Применить миграции (автоматически при старте backend)

# 4. Открыть http://localhost
# Логин: admin / admin (меняется в .env → FIRST_ADMIN_PASSWORD)
```

## Импорт существующих данных (parquet → PostgreSQL)

```bash
# Скопировать parquet в data/
docker compose run --rm backend python migrate_parquet.py --parquet /app/data/ast_results.parquet
```

## Запуск геокодирования (новые xlsx файлы)

```bash
# Через API (загрузить файл в браузере как admin)
# Или напрямую:
docker compose run --rm worker python -m worker.run --snapshot-id 1 --concurrency 80
```

## Деплой на Linux-сервере

```bash
# Первый раз
git clone <repo> geo && cd geo
cp .env.example .env
# Настроить .env (реальный домен, сложные пароли, JWT_SECRET)
./deploy.sh

# Дальнейшие обновления — просто:
./deploy.sh
```

## Роли пользователей

| Роль   | Права |
|--------|-------|
| viewer | просмотр карты |
| editor | просмотр + редактирование координат |
| admin  | всё + управление пользователями + импорт данных |

## API

- `POST /api/auth/login` — авторизация
- `GET  /api/auth/me` — текущий пользователь
- `GET  /api/places?min_lat=&max_lat=&min_lon=&max_lon=` — места по bbox
- `GET  /api/places/list?confidence=miss` — список всех адресов
- `GET  /api/places/{id}` — детальная карточка
- `PATCH /api/places/{id}/coords` — изменить координату (editor+)
- `GET  /api/places/{id}/history` — история правок
- `POST /api/imports` — загрузить xlsx (admin)
- `GET  /api/users` — список пользователей (admin)
