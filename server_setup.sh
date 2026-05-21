#!/usr/bin/env bash
# =============================================================================
#  server_setup.sh — первичная установка на чистый Linux сервер
#  Запускать от root или sudo: bash server_setup.sh
# =============================================================================
set -euo pipefail

echo "=== [1/5] Обновление системы и установка зависимостей ==="
apt-get update -y
apt-get install -y curl git ca-certificates gnupg lsb-release

echo "=== [2/5] Установка Docker ==="
if ! command -v docker &>/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  echo "deb [arch=$(dpkg --print-architecture) \
    signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu \
    $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
else
  echo "Docker уже установлен: $(docker --version)"
fi

echo "=== [3/5] Включение Docker автозапуска ==="
systemctl enable docker
systemctl start docker

echo "=== [4/5] Создание папок для данных ==="
mkdir -p /opt/geo/data/pbf
mkdir -p /opt/geo/data/input
mkdir -p /opt/geo/data/static
echo "Папки созданы в /opt/geo/data/"
echo ""
echo ">>> Теперь скопируй PBF файл:"
echo "    cp /media/usb/kazakhstan-latest.osm.pbf /opt/geo/data/pbf/"

echo "=== [5/5] Готово! ==="
echo ""
echo "Следующие шаги:"
echo "  1. cd /opt/geo"
echo "  2. git clone <твой_репозиторий> ."
echo "  3. cp .env.example .env && nano .env   # задать пароли"
echo "  4. cp /media/usb/kazakhstan-latest.osm.pbf data/pbf/"
echo "  5. docker compose up -d postgres backend worker caddy"
echo "  6. docker compose up nominatim   # первый раз — долго (2-4 часа)"
echo "  7. docker compose up photon      # после nominatim"
