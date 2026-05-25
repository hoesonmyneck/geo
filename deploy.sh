#!/usr/bin/env bash
# deploy.sh — обновить систему на сервере одной командой
# Использование: ./deploy.sh
set -euo pipefail

echo "=== [1/5] Подтягиваем свежий код ==="
git pull

echo "=== [2/5] Бэкап БД перед деплоем ==="
BACKUP_FILE="backup_$(date +%Y%m%d_%H%M%S).sql.gz"
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-geo}" "${POSTGRES_DB:-geo}" \
  | gzip > "backups/${BACKUP_FILE}" && echo "Бэкап: backups/${BACKUP_FILE}" \
  || echo "WARN: бэкап не удался (БД возможно не запущена)"
# Оставляем только последние 5 бэкапов
ls -t backups/backup_*.sql.gz 2>/dev/null | tail -n +11 | xargs -r rm --
echo "Старые бэкапы удалены, оставлено последних 10."

echo "=== [3/5] Собираем образы ==="
docker compose build backend

echo "=== [4/5] Применяем миграции БД ==="
docker compose run --rm backend alembic upgrade head

echo "=== [5/5] Перезапускаем сервисы ==="
docker compose up -d backend caddy worker

echo ""
echo "=== Статус ==="
docker compose ps
echo ""
echo "Деплой завершён!"
