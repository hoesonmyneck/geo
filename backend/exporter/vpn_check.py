# -*- coding: utf-8 -*-
"""
Проверка: может ли изолированный контейнер подняться в корпоративный VPN
и достучаться до базы proon.

Ничего не выгружает и не пишет — только диагностика. Запуск:

    docker run --rm --cap-add NET_ADMIN --device /dev/net/tun \
        --env-file vpn.env --env-file proon.env cossu-exporter

Печатает по шагам: виден ли шлюз, поднялся ли туннель, открылся ли доступ
к базе, отвечает ли PostgreSQL.
"""
import os
import socket
import subprocess
import sys


def step(title):
    print("\n=== %s ===" % title, flush=True)


def tcp_check(host, port, timeout=6):
    try:
        with socket.create_connection((host, int(port)), timeout):
            return True, "порт открыт"
    except Exception as exc:                      # noqa: BLE001 — нужен любой сбой
        return False, "%s: %s" % (type(exc).__name__, exc)


def main():
    gateway = os.environ.get("VPN_GATEWAY", "")
    db_host = os.environ.get("PROON_HOST", "")
    db_port = os.environ.get("PROON_PORT", "5432")

    step("1. Виден ли VPN-шлюз из контейнера")
    ok, msg = tcp_check(gateway, 443)
    print("%s:443 — %s" % (gateway, msg))
    if not ok:
        print("\nИТОГ: контейнер не видит шлюз. Туннель поднять неоткуда.")
        return 1

    step("2. База до подключения (ожидаем недоступность)")
    ok_before, msg_before = tcp_check(db_host, db_port, timeout=4)
    print("%s:%s — %s" % (db_host, db_port, msg_before))

    step("3. Поднимаю туннель")
    # Без перехвата вывода: ушедший в фон openconnect унаследовал бы pipe и
    # subprocess.run ждал бы EOF бесконечно, хотя сам скрипт уже завершился.
    up = subprocess.run(["/usr/local/bin/vpn-up"])
    if up.returncode != 0:
        print("\nИТОГ: туннель не поднялся (код %d). Смотрите сообщение выше — "
              "чаще всего это отказ аутентификации, требование сертификата "
              "устройства или проверка постуры." % up.returncode)
        return 1

    try:
        step("4. База после подключения")
        ok_after, msg_after = tcp_check(db_host, db_port, timeout=8)
        print("%s:%s — %s" % (db_host, db_port, msg_after))
        if not ok_after:
            print("\nИТОГ: туннель есть, но до базы не достучаться. "
                  "Возможно, эта группа VPN не даёт доступ в нужный сегмент.")
            return 1

        step("5. Отвечает ли PostgreSQL")
        try:
            import psycopg
            dsn = dict(
                host=db_host, port=int(db_port),
                dbname=os.environ["PROON_DB"],
                user=os.environ["PROON_USER"],
                password=os.environ["PROON_PASSWORD"],
                connect_timeout=15,
            )
            conn = psycopg.connect(**dsn)
            conn.read_only = True
            with conn, conn.cursor() as cur:
                cur.execute("select current_user, current_database(), version()")
                user, db, ver = cur.fetchone()
                print("подключение есть: %s@%s" % (user, db))
                print(ver.split(",")[0])
                cur.execute("SET search_path TO %s, public"
                            % os.environ.get("PROON_SCHEMA", "social_proon"))

                step("6. Боевой запрос выгрузки ЦОССУ")
                sql_path = "/app/cossu_export.sql"
                if not os.path.exists(sql_path):
                    print("файл запроса не найден, пропускаю")
                else:
                    import io
                    import time
                    started = time.time()
                    cur.execute(io.open(sql_path, encoding="utf-8").read())
                    cols = [d.name for d in cur.description]
                    rows = cur.fetchall()
                    print("отработал за %.1f c" % (time.time() - started))
                    print("строк: %d, колонок: %d" % (len(rows), len(cols)))
                    print("учреждений: %d" % len({r[8] for r in rows}))
            conn.close()
        except Exception as exc:                  # noqa: BLE001
            print("ОШИБКА подключения к базе: %s: %s" % (type(exc).__name__, exc))
            return 1

        print("\nИТОГ: работает. Контейнер поднимает VPN сам и видит базу.")
        return 0
    finally:
        step("7. Гашу туннель")
        subprocess.run(["/usr/local/bin/vpn-down"])


if __name__ == "__main__":
    sys.exit(main())
