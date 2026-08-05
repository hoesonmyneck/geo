"""Извлекает из выгрузок РКА записи с испорченным адресом.

Порча — протухший кэш full_path_* в их индексе: обход связей зациклился
и намотал повтор ("город Есиль, район Есильский, город Есиль, ..."),
иногда не дойдя до верха (тогда строка ещё и начинается с запятой).

Признак порчи — ПОВТОР сегмента. Проверка на ведущую запятую не нужна:
множество "начинается с запятой" целиком вложено в "есть повтор"
(проверено на всех трёх файлах: comma == comma AND repeat).

Результат: broken_rca.txt — по одному РКА на строку (дедуплицировано,
последнее вхождение побеждает: reparse новее output).

Запуск:
    python backend/worker/extract_broken_rka.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
# Порядок важен: поздние файлы перекрывают ранние по одному и тому же РКА
PATHS = ["rka_egov_output.jsonl", "rka_egov_extra.jsonl", "rka_egov_reparse.jsonl"]
OUT = ROOT / "broken_rca.txt"

KEY_RCA = '"rca": "'
KEY_ADDR = '"full_path_rus": "'
# Хвостовые сегменты законно уникальны и в проверке на повтор не участвуют
TAIL = ("дом", "Квартира", "үй", "Пәтер", "строение", "здание")


def _field(line: str, key: str) -> str | None:
    i = line.find(key)
    if i < 0:
        return None
    i += len(key)
    j = line.find('", "', i)
    if j < 0:
        j = line.find('"}', i)
    return line[i:j] if j > 0 else None


def has_repeat(addr: str) -> bool:
    seen = set()
    for seg in addr.split(","):
        seg = seg.strip()
        if not seg or seg.startswith(TAIL):
            continue
        if seg in seen:
            return True
        seen.add(seg)
    return False


def main() -> None:
    broken: dict[str, str] = {}   # rca → адрес (для отчёта)
    seen_ok: set[str] = set()     # rca, у которых где-то есть ЦЕЛЫЙ адрес

    for name in PATHS:
        path = ROOT / name
        if not path.exists():
            print(f"пропуск (нет файла): {name}")
            continue
        n_ok = n_bad = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                if '"status": "ok"' not in line:
                    continue
                n_ok += 1
                rca = _field(line, KEY_RCA)
                addr = _field(line, KEY_ADDR)
                if not rca or not addr:
                    continue
                if has_repeat(addr):
                    n_bad += 1
                    broken[rca] = addr
                    seen_ok.discard(rca)
                else:
                    # более поздний файл дал целый адрес — чинить нечего
                    seen_ok.add(rca)
                    broken.pop(rca, None)
        print(f"{name}: ok={n_ok:,} битых={n_bad:,}")

    OUT.write_text("\n".join(sorted(broken)) + "\n", encoding="utf-8")
    print(f"\nУникальных битых РКА: {len(broken):,}")
    print(f"Записано: {OUT}")
    print("\nПримеры:")
    for rca in sorted(broken)[:5]:
        print(f"  {rca}  {broken[rca][:95]}")


if __name__ == "__main__":
    main()
