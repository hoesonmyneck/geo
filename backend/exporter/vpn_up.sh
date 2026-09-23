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

# Ждём, пока туннель станет РАБОЧИМ: у tun0 есть адрес, и маршрут до базы
# идёт через него. Одного появления tun0 мало — openconnect создаёт интерфейс
# раньше, чем vpnc-script пропишет на нём адрес и маршруты. Разрыв особенно
# заметен, когда DTLS не проходит и клиент откатывается на TLS: тогда настройка
# запаздывает секунд на пятнадцать, и запрос, отправленный сразу, уходит мимо
# туннеля через eth0 и падает по таймауту.
TARGET="${PROON_HOST:-}"
i=0
while [ $i -lt 45 ]; do
    if ip -4 addr show tun0 2>/dev/null | grep -q "inet "; then
        if [ -z "$TARGET" ] || ip route get "$TARGET" 2>/dev/null | grep -q "dev tun0"; then
            echo "[vpn] туннель поднят:"
            ip -4 addr show tun0 | sed 's/^/[vpn]   /'
            if [ -n "$TARGET" ]; then
                echo "[vpn] маршрут до базы: $(ip route get "$TARGET" | head -n 1)"
            fi
            exit 0
        fi
    fi
    i=$((i + 1))
    sleep 1
done

echo "[vpn] ОШИБКА: туннель не стал рабочим за 45 секунд" >&2
echo "[vpn] адрес tun0:" >&2
ip -4 addr show tun0 >&2 2>&1 || echo "[vpn]   интерфейса нет" >&2
if [ -n "$TARGET" ]; then
    echo "[vpn] маршрут до базы: $(ip route get "$TARGET" 2>&1 | head -n 1)" >&2
fi
tail -n 25 "$LOG" >&2
exit 1
