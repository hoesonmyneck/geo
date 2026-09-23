# -*- coding: utf-8 -*-
"""
Сервис выгрузки ЦОССУ. Живёт в изолированном контейнере, наружу не смотрит —
слушает только внутреннюю docker-сеть, ходит к нему бэкенд.

По запросу: поднимает VPN → выполняет запрос к базе proon → пишет xlsx в общий
том (тот же, что видит бэкенд как /app/data/input) → гасит VPN. Туннель живёт
внутри сетевого неймспейса этого контейнера, маршруты хоста не затрагиваются.

Ручки:
    GET  /health  — жив ли сервис
    POST /export  — выгрузить; отдаёт JSON с именем файла и статистикой

Требуется заголовок X-Exporter-Token, совпадающий с EXPORTER_TOKEN. Сеть
внутренняя, но бэкенд смотрит в интернет, поэтому лишний барьер не помешает.

Запускается с --cap-add NET_ADMIN --device /dev/net/tun.
"""
import json
import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, "/app")

import export_cossu_from_proon as exporter  # noqa: E402

PORT = int(os.environ.get("EXPORTER_PORT", "8081"))
TOKEN = os.environ.get("EXPORTER_TOKEN", "")
OUT_DIR = Path(os.environ.get("COSSU_OUT_DIR", "/data/input"))
SCHEMA = os.environ.get("PROON_SCHEMA", "social_proon")

# Одновременно допускается одна выгрузка: и туннель, и база этого не любят,
# а два параллельных импорта в пайплайне подрались бы за таблицу.
_lock = threading.Lock()


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def vpn(action):
    """Поднимает или гасит туннель. Вывод не перехватываем: ушедший в фон
    openconnect наследовал бы pipe и мы ждали бы EOF вечно."""
    res = subprocess.run(["/usr/local/bin/vpn-%s" % action])
    return res.returncode == 0


def run_export(min_rows, max_drop):
    """Полный цикл выгрузки. Возвращает словарь с результатом."""
    started = datetime.now()
    if not vpn("up"):
        raise RuntimeError("не удалось поднять VPN — смотрите логи контейнера")
    try:
        columns, rows, elapsed = exporter.fetch_rows(SCHEMA, "600s")
        log("запрос: %.1f c, строк %d" % (elapsed, len(rows)))
        stats = exporter.check_sanity(columns, rows, min_rows, max_drop)

        name = "cossu_auto_%s.xlsx" % started.strftime("%Y_%m_%d_%H%M")
        out_path = OUT_DIR / name
        exporter.write_xlsx(columns, rows, out_path)
        log("файл записан: %s" % out_path)

        # Запоминаем удачную выгрузку — от неё считается порог просадки
        # в следующий раз.
        exporter.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        exporter.STATE_PATH.write_text(json.dumps({
            "rows": stats["rows"], "orgs": stats["orgs"], "file": name,
            "at": started.isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "ok": True,
            "file": name,
            "path": str(out_path),
            "query_seconds": round(elapsed, 1),
            "total_seconds": round((datetime.now() - started).total_seconds(), 1),
            "stats": stats,
        }
    finally:
        vpn("down")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not TOKEN:
            return True
        return self.headers.get("X-Exporter-Token") == TOKEN

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "service": "cossu-exporter"})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path != "/export":
            self._send(404, {"ok": False, "error": "not found"})
            return
        if not self._authorized():
            self._send(403, {"ok": False, "error": "неверный токен"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        min_rows = int(body.get("min_rows", exporter.DEFAULT_MIN_ROWS))
        max_drop = float(body.get("max_drop", exporter.DEFAULT_MAX_DROP))

        if not _lock.acquire(blocking=False):
            self._send(409, {"ok": False, "error": "выгрузка уже идёт"})
            return
        try:
            log("старт выгрузки (min_rows=%d, max_drop=%.2f)" % (min_rows, max_drop))
            self._send(200, run_export(min_rows, max_drop))
        except exporter.SanityError as exc:
            log("выгрузка отклонена проверкой: %s" % exc)
            self._send(422, {"ok": False, "error": str(exc), "kind": "sanity"})
        except Exception as exc:                  # noqa: BLE001
            log("ошибка: %s\n%s" % (exc, traceback.format_exc()))
            self._send(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
        finally:
            _lock.release()

    def log_message(self, fmt, *args):
        log(fmt % args)


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log("слушаю порт %d, выгрузка пишется в %s" % (PORT, OUT_DIR))
    if not TOKEN:
        log("ВНИМАНИЕ: EXPORTER_TOKEN не задан, ручка /export открыта всем "
            "внутри docker-сети")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
