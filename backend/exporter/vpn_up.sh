#!/bin/sh
# Поднимает туннель до корпоративного VPN внутри контейнера.
# Пароль передаётся через stdin, а не аргументом — иначе он виден в ps.
#
# Переменные: VPN_GATEWAY, VPN_GROUP, VPN_USER, VPN_PASSWORD
#             VPN_SERVERCERT — отпечаток сертификата шлюза (pin), если задан.
set -e

LOG=/tmp/openconnect.log
: >"$LOG"

: "${VPN_GATEWAY:?не задан VPN_GATEWAY}"
: "${VPN_USER:?не задан VPN_USER}"
: "${VPN_PASSWORD:?не задан VPN_PASSWORD}"

CERT_ARG=""
if [ -n "$VPN_SERVERCERT" ]; then
    CERT_ARG="--servercert=$VPN_SERVERCERT"
fi

GROUP_ARG=""
if [ -n "$VPN_GROUP" ]; then
    GROUP_ARG="--authgroup=$VPN_GROUP"
fi

echo "[vpn] подключаюсь к $VPN_GATEWAY (группа: ${VPN_GROUP:-по умолчанию}) ..."
# Вывод уходит в файл, а не наследуется от вызывающего процесса. Иначе
# ушедший в фон openconnect держит открытым pipe, и вызывающий скрипт,
# читающий наш stdout, ждёт EOF бесконечно.
printf '%s\n' "$VPN_PASSWORD" | openconnect \
    --protocol=anyconnect \
    --user="$VPN_USER" \
    $GROUP_ARG \
    $CERT_ARG \
    --passwd-on-stdin \
    --background \
    --pid-file=/tmp/openconnect.pid \
    --script=/usr/share/vpnc-scripts/vpnc-script \
    --timestamp \
    "$VPN_GATEWAY" >>"$LOG" 2>&1 || {
        echo "[vpn] openconnect завершился с ошибкой, лог:" >&2
        tail -n 25 "$LOG" >&2
        exit 1
    }

# Ждём появления туннельного интерфейса — openconnect уходит в фон раньше,
# чем маршруты реально прописаны.
i=0
while [ $i -lt 30 ]; do
    if ip link show tun0 >/dev/null 2>&1; then
        echo "[vpn] туннель поднят:"
        ip -4 addr show tun0 | sed 's/^/[vpn]   /'
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done

echo "[vpn] ОШИБКА: интерфейс tun0 не появился за 30 секунд" >&2
tail -n 25 "$LOG" >&2
exit 1
