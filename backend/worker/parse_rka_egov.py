"""
Прогон РКА-кодов через гос API data.egov.kz → адрес + s_building_id.

Standalone (без БД и докера): читает все xlsx из папки rka/, собирает уникальные
RCA, дёргает гос API многопоточно и пишет результат в jsonl.
Резюмируемый: при перезапуске пропускает уже обработанные коды.

Запуск:
    python backend/worker/parse_rka_egov.py [--workers 30] [--limit N] [--out FILE]

Выход (jsonl, по строке на код):
    {"rca": "...", "status": "ok|empty|error",
     "full_path_rus": "...", "full_path_kaz": "...",
     "s_building_id": "...", "index": "s_pb|s_buildings"}
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import urllib.request
import urllib.error

import openpyxl


ROOT     = Path(__file__).resolve().parents[2]
RKA_DIR  = ROOT / "rka"
OUT_PATH = ROOT / "rka_egov_output.jsonl"
API_TMPL = 'http://data.egov.kz/api/detailed/s_buildings,s_grounds_new,s_pb?source={}'

_write_lock = threading.Lock()
_counter    = {"ok": 0, "empty": 0, "error": 0, "done": 0}
_count_lock = threading.Lock()


class RateLimiter:
    """Глобальный ограничитель: не чаще rate запросов в секунду по всем потокам.
    rate<=0 — без лимита. Держим минимальный интервал между стартами запросов."""

    def __init__(self, rate: float):
        self.interval = (1.0 / rate) if rate and rate > 0 else 0.0
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        if self.interval <= 0:
            return
        with self.lock:
            now = time.time()
            wait_for = self.next_at - now
            if wait_for > 0:
                time.sleep(wait_for)
                self.next_at += self.interval
            else:
                self.next_at = time.time() + self.interval


_rate = RateLimiter(0.0)  # переустанавливается в main() по --rate


def read_all_codes(files: list[Path] | None = None) -> list[str]:
    """Собирает уникальные RCA из xlsx (колонка RCA, первая).

    files=None → все xlsx в rka/. Иначе — только переданные файлы.
    """
    if files is None:
        files = sorted(RKA_DIR.glob("*.xlsx"))
    if not files:
        print(f"ERROR: нет xlsx в {RKA_DIR}", file=sys.stderr)
        sys.exit(1)
    seen: set[str] = set()
    for f in files:
        print(f"  читаю {f.name} ...", file=sys.stderr)
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        ws = wb.active
        for r, row in enumerate(ws.iter_rows(values_only=True)):
            if r == 0:
                continue  # заголовок RCA/IDS
            v = row[0]
            if v is None:
                continue
            s = str(v).strip()
            if "." in s:
                try:
                    s = str(int(float(s)))
                except ValueError:
                    pass
            s = s.split(".")[0]
            if s:
                seen.add(s)
        wb.close()
    return list(seen)


def read_codes_txt(path: Path) -> list[str]:
    """Читает коды из txt (по одному РКА на строку). Убирает BOM/пробелы,
    оставляет только 16-значные, дедуплицирует с сохранением порядка."""
    seen: dict[str, None] = {}
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            s = line.strip()
            if len(s) == 16 and s.isdigit():
                seen.setdefault(s, None)
    return list(seen)


def load_done(out_path: Path) -> set[str]:
    """Читает успешно обработанные RCA из jsonl (резюмирование).

    В done попадают только status ok/empty — это окончательный результат.
    status=error (обрыв сети / API лёг) НЕ считается готовым, чтобы при
    резюме такие коды перезапрашивались.
    """
    done: set[str] = set()
    if not out_path.exists():
        return done
    with out_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("status") in ("ok", "empty"):
                done.add(rec.get("rca"))
    return done


def lookup(rca: str, timeout: float = 12.0, retries: int = 2) -> dict:
    src = json.dumps({"query": {"term": {"rca": rca}}}, ensure_ascii=False)
    url = API_TMPL.format(quote(src, safe=""))
    last_err = None
    for attempt in range(retries + 1):
        try:
            _rate.wait()
            req = urllib.request.Request(url, headers={"User-Agent": "geo-rka/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            items = data.get("data") if isinstance(data, dict) else (data if isinstance(data, list) else [])
            if not items:
                return {"rca": rca, "status": "empty"}
            it = items[0]
            return {
                "rca":           rca,
                "status":        "ok",
                "full_path_rus": it.get("full_path_rus"),
                "full_path_kaz": it.get("full_path_kaz"),
                "s_building_id": it.get("s_building_id"),
                "index":         it.get("_index"),
            }
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(0.5 * (attempt + 1))
    return {"rca": rca, "status": "error", "error": str(last_err)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=30)
    ap.add_argument("--limit",   type=int, default=0, help="0 = все коды")
    ap.add_argument("--out",     type=str, default=str(OUT_PATH))
    ap.add_argument("--input",   type=str, default=None, help="конкретный xlsx (по умолчанию вся папка rka/)")
    ap.add_argument("--codes-txt", type=str, default=None, help="txt с РКА (по одному на строку); имеет приоритет над xlsx")
    ap.add_argument("--rate", type=float, default=0.0, help="макс. запросов/сек по всем потокам (0 = без лимита)")
    args = ap.parse_args()

    global _rate
    _rate = RateLimiter(args.rate)

    out_path = Path(args.out)
    in_files = [Path(args.input)] if args.input else None

    if args.codes_txt:
        print(f"Читаю коды из {args.codes_txt} ...", file=sys.stderr)
        codes = read_codes_txt(Path(args.codes_txt))
    else:
        print("Собираю коды из xlsx ...", file=sys.stderr)
        codes = read_all_codes(in_files)
    print(f"Уникальных RCA: {len(codes):,}", file=sys.stderr)

    done = load_done(out_path)
    if done:
        print(f"Уже обработано (резюм): {len(done):,}", file=sys.stderr)
    todo = [c for c in codes if c not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"К обработке: {len(todo):,}  (потоков: {args.workers})", file=sys.stderr)
    if not todo:
        print("Нечего делать.", file=sys.stderr)
        return

    t0 = time.time()
    fout = out_path.open("a", encoding="utf-8")

    def handle(res: dict):
        with _write_lock:
            fout.write(json.dumps(res, ensure_ascii=False) + "\n")
        with _count_lock:
            _counter[res["status"]] = _counter.get(res["status"], 0) + 1
            _counter["done"] += 1
            n = _counter["done"]
        if n % 2000 == 0:
            with _write_lock:
                fout.flush()
            rate = n / max(time.time() - t0, 1e-6)
            eta  = (len(todo) - n) / max(rate, 1e-6)
            print(f"  {n:,}/{len(todo):,}  ok={_counter['ok']:,} "
                  f"empty={_counter['empty']:,} err={_counter['error']:,}  "
                  f"{rate:.0f}/s  ETA {eta/3600:.1f}ч", file=sys.stderr)

    # Обрабатываем чанками: не сабмитим все 8.2M futures разом (память/старт-задержка).
    CHUNK = 20_000
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for i in range(0, len(todo), CHUNK):
                batch = todo[i:i + CHUNK]
                futures = [ex.submit(lookup, c) for c in batch]
                for fut in as_completed(futures):
                    handle(fut.result())
                with _write_lock:
                    fout.flush()
    finally:
        fout.flush()
        fout.close()

    dt = time.time() - t0
    print(f"\nГотово за {dt/3600:.2f}ч. ok={_counter['ok']:,} "
          f"empty={_counter['empty']:,} error={_counter['error']:,}", file=sys.stderr)


if __name__ == "__main__":
    main()
