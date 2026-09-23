#!/bin/sh
# Гасит туннель. Вызывается после выгрузки — держать подключение постоянно
# не нужно и не стоит.
if [ -f /tmp/openconnect.pid ]; then
    PID=$(cat /tmp/openconnect.pid)
    if kill -TERM "$PID" 2>/dev/null; then
        echo "[vpn] отключаюсь (pid $PID) ..."
        i=0
        while [ $i -lt 10 ] && kill -0 "$PID" 2>/dev/null; do
            i=$((i + 1))
            sleep 1
        done
    fi
    rm -f /tmp/openconnect.pid
fi
echo "[vpn] туннель закрыт"
