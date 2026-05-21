"""
Main geocoding pipeline CLI.

Usage:
    python -m src.pipeline --input data/input/nura.xlsx --output data/output/results.parquet

For large files (20M rows), pass a CSV instead of xlsx:
    python -m src.pipeline --input data/input/full.csv --output data/output/results.parquet --concurrency 100
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterator

import click
import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from tqdm.asyncio import tqdm

from .geocode import GeoResult, geocode
from .normalize import normalize_row, AddressRecord

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "run.log", encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PyArrow schema
# ---------------------------------------------------------------------------

# Атрибуты персон (новый формат файлов)
ATTR_FIELDS = [
    ("corpus",      pa.string()),   # корпус здания
    ("rainame",     pa.string()),   # название района (KATO_RAINAME)
    ("gender_id",   pa.int32()),    # 1=муж, 2=жен
    ("vozrast",     pa.int32()),    # возраст
    ("trud_vozrast",pa.int32()),    # трудоспособный возраст 0/1
    ("deti_do18",   pa.int32()),    # дети до 18 лет 0/1
    ("working",     pa.int32()),    # работающий 0/1
    ("lsi",         pa.int32()),    # ЛСИ 0/1
    ("asp",         pa.int32()),    # АСП 0/1
    ("student",     pa.int32()),    # студент 0/1
    ("pensioners",  pa.int32()),    # пенсионер 0/1
    ("ip",          pa.int32()),    # ИП 0/1
    ("kandas",      pa.int32()),    # КАНДАС 0/1
]

SCHEMA = pa.schema([
    pa.field("sicid",           pa.int64()),
    pa.field("lat",             pa.float64()),
    pa.field("lon",             pa.float64()),
    pa.field("confidence",      pa.string()),
    pa.field("source",          pa.string()),
    pa.field("name_remapped",   pa.bool_()),
    pa.field("original_street", pa.string()),
    pa.field("street_used",     pa.string()),
    pa.field("house_used",      pa.string()),
    pa.field("city",            pa.string()),
    pa.field("raw_osm_id",      pa.string()),
    *[pa.field(name, dtype) for name, dtype in ATTR_FIELDS],
])


# ---------------------------------------------------------------------------
# Input readers
# ---------------------------------------------------------------------------

def _iter_xlsx(path: Path) -> Iterator[dict]:
    """Stream rows from xlsx as dicts {column_name: value}."""
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    header: list[str] = []
    for raw_row in ws.iter_rows(values_only=True):
        if not header:
            header = [str(c).strip() if c is not None else "" for c in raw_row]
            continue
        yield dict(zip(header, raw_row))
    wb.close()


def _iter_csv(path: Path) -> Iterator[dict]:
    """Stream rows from CSV file as dicts."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield dict(row)


def _iter_input(path: Path) -> Iterator[dict]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return _iter_xlsx(path)
    elif suffix == ".csv":
        return _iter_csv(path)
    else:
        raise ValueError(f"Unsupported input format: {suffix}")


# ---------------------------------------------------------------------------
# Checkpoint (simple text file: one sicid per line = processed)
# ---------------------------------------------------------------------------

def _load_checkpoint(checkpoint_path: Path) -> set[int]:
    if not checkpoint_path.exists():
        return set()
    with checkpoint_path.open(encoding="utf-8") as f:
        return {int(line.strip()) for line in f if line.strip().isdigit()}


def _save_checkpoint(checkpoint_path: Path, sicid: int) -> None:
    with checkpoint_path.open("a", encoding="utf-8") as f:
        f.write(f"{sicid}\n")


# ---------------------------------------------------------------------------
# Result buffer → parquet
# ---------------------------------------------------------------------------

def _results_to_batch(
    results: list[GeoResult],
    attrs_list: list[dict] | None = None,
) -> pa.RecordBatch:
    data: dict = {
        "sicid":           [r.sicid        for r in results],
        "lat":             [r.lat          for r in results],
        "lon":             [r.lon          for r in results],
        "confidence":      [r.confidence   for r in results],
        "source":          [r.source       for r in results],
        "name_remapped":   [r.name_remapped for r in results],
        "original_street": [r.original_street for r in results],
        "street_used":     [r.street_used  for r in results],
        "house_used":      [r.house_used   for r in results],
        "city":            [r.city         for r in results],
        "raw_osm_id":      [r.raw_osm_id or "" for r in results],
    }
    if attrs_list:
        for name, dtype in ATTR_FIELDS:
            default = "" if pa.types.is_string(dtype) else 0
            data[name] = [a.get(name, default) for a in attrs_list]
    else:
        # Заполняем атрибуты значениями по умолчанию
        for name, dtype in ATTR_FIELDS:
            default = "" if pa.types.is_string(dtype) else 0
            data[name] = [default] * len(results)
    return pa.record_batch(data, schema=SCHEMA)


# ---------------------------------------------------------------------------
# Async worker pool
# ---------------------------------------------------------------------------

async def _run_pipeline(
    records: list[AddressRecord],
    concurrency: int,
    checkpoint_path: Path,
    writer: pq.ParquetWriter,
    batch_size: int,
    all_rows: list[tuple[int, "AddressRecord"]] | None = None,
    sid_to_key: dict[int, str] | None = None,
) -> dict[str, "GeoResult"]:
    """
    Геокодирует уникальные адреса (records), пишет в parquet все строки
    включая дубликаты (all_rows), возвращает словарь addr_key → GeoResult.

    Ключ geo_results — addr_key, вычисленный из ВХОДНОГО record (не из result),
    чтобы гарантировать совпадение с sid_to_key.
    """
    from .cache import make_hash as _make_hash  # noqa: PLC0415

    semaphore = asyncio.Semaphore(concurrency)
    stats: dict[str, int] = defaultdict(int)
    # addr_key → GeoResult  (ключ вычислен из входного rec, а не из result)
    geo_results: dict[str, GeoResult] = {}
    total = len(records)

    # Строим маппинг sicid → addr_key для представителей групп
    rep_sicid_to_key: dict[int, str] = {
        rec.sicid: _make_hash(rec.street_name, rec.house, rec.city)
        for rec in records
    }

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        limits=httpx.Limits(max_connections=concurrency + 10, max_keepalive_connections=concurrency),
    ) as client:

        async def process_one(rec: AddressRecord) -> tuple[str, GeoResult]:
            async with semaphore:
                result = await geocode(rec, client)
            # Ключ — из входного rec (надёжно), не из result
            key = rep_sicid_to_key[rec.sicid]
            return key, result

        pbar = tqdm(total=total, desc="Геокодинг уник. адресов", unit="addr", dynamic_ncols=True)

        tasks = [asyncio.create_task(process_one(r)) for r in records]
        for coro in asyncio.as_completed(tasks):
            try:
                key, result = await coro
            except Exception as exc:
                logger.warning("Task failed (skipped): %s", exc)
                pbar.update(1)
                continue
            geo_results[key] = result
            stats[result.confidence] += 1
            _save_checkpoint(checkpoint_path, result.sicid)
            pbar.update(1)
            pbar.set_postfix({
                "high": stats["high"],
                "med":  stats["medium"],
                "low":  stats["low"],
                "miss": stats["miss"],
            })

        pbar.close()

    # Записываем в parquet: каждую строку из all_rows с результатом её адреса
    if all_rows and sid_to_key:
        logger.info("Writing %d rows to parquet (expanding dedup results)...", len(all_rows))
        buffer: list[GeoResult] = []
        attrs_buf: list[dict] = []
        matched = 0
        # sid_attrs передаётся снаружи через _run_pipeline kwargs, но можно и пустым
        _sid_attrs: dict[int, dict] = getattr(_run_pipeline, "_sid_attrs", {})
        for sid, rec in all_rows:
            key = sid_to_key[sid]
            base = geo_results.get(key)
            if base is None:
                continue
            matched += 1
            row_result = GeoResult(
                sicid=sid,
                lat=base.lat, lon=base.lon,
                confidence=base.confidence, source=base.source,
                name_remapped=base.name_remapped,
                original_street=base.original_street,
                street_used=base.street_used, house_used=base.house_used,
                city=base.city, raw_osm_id=base.raw_osm_id,
            )
            buffer.append(row_result)
            attrs_buf.append(_sid_attrs.get(sid, {}))
            if len(buffer) >= batch_size:
                writer.write_batch(_results_to_batch(buffer, attrs_buf))
                buffer.clear()
                attrs_buf.clear()
        if buffer:
            writer.write_batch(_results_to_batch(buffer, attrs_buf))
        logger.info("Parquet write done: %d rows written", matched)
    else:
        buf = list(geo_results.values())
        for i in range(0, len(buf), batch_size):
            writer.write_batch(_results_to_batch(buf[i:i + batch_size]))

    return geo_results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option("--input", "input_path", required=True, type=click.Path(exists=True, path_type=Path),
              help="Input file (xlsx or csv)")
@click.option("--output", "output_path", required=True, type=click.Path(path_type=Path),
              help="Output parquet file")
@click.option("--concurrency", default=100, show_default=True,
              help="Number of parallel geocoding requests")
@click.option("--batch-size", default=10_000, show_default=True,
              help="Rows per parquet batch flush")
@click.option("--resume/--no-resume", default=True, show_default=True,
              help="Resume from checkpoint if exists")
def main(
    input_path: Path,
    output_path: Path,
    concurrency: int,
    batch_size: int,
    resume: bool,
) -> None:
    """Geocode addresses from xlsx/csv to parquet using local Nominatim + Photon."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_path.with_suffix(".checkpoint.txt")

    # Load checkpoint
    done_ids: set[int] = set()
    if resume:
        done_ids = _load_checkpoint(checkpoint_path)
        if done_ids:
            logger.info("Resuming: %d records already processed", len(done_ids))

    # Read and normalize input
    logger.info("Reading input: %s", input_path)
    t0 = time.monotonic()

    # all_rows: все строки в порядке файла (sicid → AddressRecord)
    all_rows: list[tuple[int, AddressRecord]] = []
    # sid_attrs: атрибуты персоны для каждой строки (новый формат)
    sid_attrs: dict[int, dict] = {}
    skipped = 0

    for i, row in enumerate(_iter_input(input_path)):
        # ── Определяем формат по наличию ключей ───────────────────────────────
        if "KATO_REGNAME" in row:
            # Новый формат: нет SICID, всё по именам колонок
            sid = i + 1
            regname  = str(row.get("KATO_REGNAME") or "")
            rainame  = str(row.get("KATO_RAINAME") or "")
            street   = str(row.get("REG_ADDRESS_STREET") or "")
            building = str(row.get("REG_ADDRESS_BUILDING") or "")
            corpus   = str(row.get("REG_ADDRESS_CORPUS") or "").strip()
            # Корпус добавляем к номеру дома если указан
            if corpus:
                building = f"{building} {corpus}".strip()
            # Собираем атрибуты
            def _int(v: object) -> int:
                try:
                    return int(float(str(v))) if v is not None and str(v).strip() not in ("", "nan") else 0
                except (ValueError, TypeError):
                    return 0
            sid_attrs[sid] = {
                "corpus":       corpus,
                "rainame":      rainame,
                "gender_id":    _int(row.get("GENDER_ID")),
                "vozrast":      _int(row.get("VOZRAST")),
                "trud_vozrast": _int(row.get("TRUD_VOZRAST")),
                "deti_do18":    _int(row.get("DETI_DO18")),
                "working":      _int(row.get("WORKING")),
                "lsi":          _int(row.get("LSI")),
                "asp":          _int(row.get("ASP")),
                "student":      _int(row.get("STUDENT")),
                "pensioners":   _int(row.get("PENSIONERS")),
                "ip":           _int(row.get("IP")),
                "kandas":       _int(row.get("KANDAS")),
            }
        elif "REGNAME" in row or "REG_ADDRESS_STREET" in row:
            # Старый формат без SICID (4 колонки по имени)
            sid = i + 1
            regname  = str(row.get("REGNAME") or "")
            rainame  = str(row.get("RAINAME") or "")
            street   = str(row.get("REG_ADDRESS_STREET") or "")
            building = str(row.get("REG_ADDRESS_BUILDING") or "")
        else:
            # Совсем старый формат: словарь из _iter_csv с числовыми ключами
            # (маловероятно после перехода на dict, но на всякий случай)
            logger.warning("Row %d: unrecognised format, skipping", i)
            continue

        if sid in done_ids:
            skipped += 1
            continue
        try:
            rec = normalize_row(
                sid, regname, rainame, street, building,
            )
            all_rows.append((sid, rec))
        except Exception as e:
            logger.warning("Normalization error row %d: %s", i, e)

    # Сохраняем sid_attrs как атрибут функции чтобы _run_pipeline мог его использовать
    _run_pipeline._sid_attrs = sid_attrs  # type: ignore[attr-defined]

    total_rows = len(all_rows)
    logger.info(
        "Loaded %d records in %.1fs (skipped %d already done)",
        total_rows, time.monotonic() - t0, skipped,
    )

    if not all_rows:
        logger.info("Nothing to geocode. Done.")
        return

    # ── Дедупликация ──────────────────────────────────────────────────────────
    # Геокодируем только уникальные адреса (city + street + house).
    # Для одного дома с N жильцами делаем ровно 1 запрос, а не N.
    from .cache import make_hash as _make_hash  # noqa: PLC0415

    # addr_key → первый AddressRecord (как представитель группы)
    unique_map: dict[str, AddressRecord] = {}
    # sicid → addr_key  (для обратного маппинга)
    sid_to_key: dict[int, str] = {}

    for sid, rec in all_rows:
        key = _make_hash(rec.street_name, rec.house, rec.city)
        sid_to_key[sid] = key
        if key not in unique_map:
            unique_map[key] = rec

    unique_records = list(unique_map.values())
    logger.info(
        "Unique addresses to geocode: %d (dedup ratio %.1fx)",
        len(unique_records), total_rows / max(len(unique_records), 1),
    )

    # Windows requires SelectorEventLoop for psycopg async and httpx
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # Геокодируем уникальные адреса → словарь addr_key → GeoResult
    append = resume and output_path.exists()
    writer = pq.ParquetWriter(
        str(output_path) if not append else str(output_path) + ".part",
        schema=SCHEMA,
        compression="snappy",
    )

    try:
        geo_cache: dict[str, GeoResult] = asyncio.run(
            _run_pipeline(unique_records, concurrency, checkpoint_path, writer, batch_size,
                          all_rows=all_rows, sid_to_key=sid_to_key)
        )
        stats = defaultdict(int)
        for r in geo_cache.values():
            stats[r.confidence] += 1
    finally:
        writer.close()

    # Merge parquet files if appending
    if append:
        part_path = Path(str(output_path) + ".part")
        existing = pq.read_table(str(output_path), schema=SCHEMA)
        new_part = pq.read_table(str(part_path), schema=SCHEMA)
        merged = pa.concat_tables([existing, new_part])
        pq.write_table(merged, str(output_path), compression="snappy")
        part_path.unlink()

    total = sum(stats.values())
    logger.info("Done. Total: %d | high: %d (%.1f%%) | medium: %d | low: %d | miss: %d",
        total,
        stats.get("high", 0), stats.get("high", 0) / max(total, 1) * 100,
        stats.get("medium", 0),
        stats.get("low", 0),
        stats.get("miss", 0),
    )

    # Write quick stats JSON for reporting
    stats_path = output_path.with_suffix(".stats.json")
    with stats_path.open("w", encoding="utf-8") as f:
        json.dump({"total": total, **stats}, f, ensure_ascii=False, indent=2)
    logger.info("Stats written to %s", stats_path)


if __name__ == "__main__":
    main()  # pragma: no cover
