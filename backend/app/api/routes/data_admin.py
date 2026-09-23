"""
Управление данными (только admin).

Обновление ЦОССУ одной кнопкой: сервис-выгружатель забирает данные из
корпоративной базы proon, дальше отрабатывает обычный пайплайн — загрузка,
удаление отсутствующих, геокодирование.

Задача выполняется в фоне, интерфейс опрашивает её состояние: пайплайн идёт
десятки секунд, а геокодирование и того дольше — в один HTTP-запрос это
не укладывается.

Состояние задач держим в памяти процесса. Это допустимо: uvicorn запущен
одним воркером (см. backend/Dockerfile), и задача переживать перезапуск
не обязана — после рестарта её просто запускают заново.
"""
import asyncio
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.core.deps import require_admin
from app.db.models import User
from app.db.session import AsyncSessionLocal

router = APIRouter(prefix="/admin/data", tags=["data-admin"])

EXPORTER_URL = os.environ.get("EXPORTER_URL", "http://cossu-exporter:8081")
EXPORTER_TOKEN = os.environ.get("EXPORTER_TOKEN", "")
INPUT_DIR = "/app/data/input"

# Шаги пайплайна: ключ, подпись для интерфейса, доля прогресса.
STEPS = [
    ("export",  "Выгрузка из корпоративной базы", 40),
    ("backup",  "Резервная копия таблицы",        10),
    ("seed",    "Загрузка новых и изменённых",    20),
    ("stale",   "Удаление отсутствующих",         10),
    ("geocode", "Геокодирование новых адресов",   20),
]

_jobs: dict[str, dict] = {}
_job_lock = asyncio.Lock()

# Ключ 2ГИС правится из интерфейса и живёт в файле на томе geo_cache, а не в
# переменных окружения: ключи выдают на время, и менять их без пересборки
# образа и перезапуска контейнера — обязательное требование.
SETTINGS_PATH = Path(os.environ.get(
    "DATA_ADMIN_SETTINGS", "/app/data/cache/data_admin_settings.json"))


def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_settings(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def _twogis_key() -> str:
    """Ключ из настроек, иначе из окружения (как было раньше)."""
    return (_load_settings().get("twogis_key")
            or os.environ.get("TWOGIS_API_KEY", "")).strip()


def _mask_key(key: str) -> str:
    """Для показа в интерфейсе: видно начало и конец, середина скрыта."""
    if not key:
        return ""
    if len(key) <= 12:
        return key[:2] + "…" + key[-2:]
    return "%s…%s" % (key[:8], key[-6:])


class RefreshRequest(BaseModel):
    # Геокодирование можно отключить — например, когда истёк ключ 2ГИС.
    geocode: bool = True
    # Разовый ключ: если передан, используется вместо сохранённого.
    twogis_key: str | None = None
    # Пороги предохранителей выгрузки. Поднимать вручную стоит только тогда,
    # когда источник действительно сократился.
    min_rows: int | None = None
    max_drop: float | None = None


def _new_job() -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "kind": "cossu",
        "status": "running",
        "progress": 0,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "steps": [{"key": k, "label": lbl, "status": "pending", "detail": ""}
                  for k, lbl, _ in STEPS],
        "result": {},
        "error": None,
        "log": [],
    }


def _step(job: dict, key: str, status: str, detail: str = "") -> None:
    """Отмечает состояние шага и пересчитывает общий прогресс."""
    for item in job["steps"]:
        if item["key"] == key:
            item["status"] = status
            if detail:
                item["detail"] = detail
            break
    done = 0
    for k, _lbl, weight in STEPS:
        st = next(s["status"] for s in job["steps"] if s["key"] == k)
        if st in ("done", "skipped"):
            done += weight
        elif st == "running":
            done += weight // 2
    job["progress"] = min(done, 100)


def _log(job: dict, line: str) -> None:
    job["log"].append("%s  %s" % (datetime.now().strftime("%H:%M:%S"), line))
    # Лог показывается в интерфейсе целиком, ограничиваем на всякий случай.
    if len(job["log"]) > 400:
        del job["log"][:-400]


async def _counts() -> dict:
    """Текущее состояние таблицы cossu — для отчёта «было / стало»."""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(text(
            "SELECT count(*) AS rows, "
            "       count(DISTINCT org_bin) AS orgs, "
            "       count(*) FILTER (WHERE lat IS NULL) AS no_coords "
            "  FROM cossu"
        ))).mappings().one()
        return dict(row)


async def _run_script(job: dict, key: str, args: list[str],
                      env: dict | None = None, timeout: int = 1800) -> str:
    """Запускает воркер-скрипт и отдаёт его вывод. Падение — исключение."""
    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)
    proc = await asyncio.create_subprocess_exec(
        "python", *args, cwd="/app",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env=proc_env,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("шаг «%s» не уложился в %d секунд" % (key, timeout))
    text_out = (out or b"").decode("utf-8", "replace")
    for line in text_out.strip().splitlines()[-40:]:
        _log(job, line)
    if proc.returncode != 0:
        raise RuntimeError("шаг «%s» завершился с кодом %d" % (key, proc.returncode))
    return text_out


async def _do_export(job: dict, req: RefreshRequest) -> str:
    """Просит сервис-выгружатель поднять VPN и забрать данные из proon."""
    _step(job, "export", "running")
    payload: dict = {}
    if req.min_rows is not None:
        payload["min_rows"] = req.min_rows
    if req.max_drop is not None:
        payload["max_drop"] = req.max_drop

    headers = {"X-Exporter-Token": EXPORTER_TOKEN} if EXPORTER_TOKEN else {}
    async with httpx.AsyncClient(timeout=600) as client:
        try:
            resp = await client.post("%s/export" % EXPORTER_URL,
                                     json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "сервис выгрузки недоступен (%s). Проверьте, что контейнер "
                "cossu-exporter запущен." % exc)

    data = resp.json() if resp.content else {}
    if resp.status_code == 422:
        # Предохранители выгрузки: данных пришло подозрительно мало.
        raise RuntimeError("выгрузка отклонена проверкой: %s"
                           % data.get("error", "причина не указана"))
    if resp.status_code != 200 or not data.get("ok"):
        raise RuntimeError("выгрузка не удалась: %s"
                           % data.get("error", "HTTP %d" % resp.status_code))

    stats = data.get("stats", {})
    job["result"]["file"] = data.get("file")
    job["result"]["source_rows"] = stats.get("rows")
    job["result"]["source_orgs"] = stats.get("orgs")
    _log(job, "выгружено строк: %s, учреждений: %s (запрос %s c)"
         % (stats.get("rows"), stats.get("orgs"), data.get("query_seconds")))
    _step(job, "export", "done",
          "%s строк, %s учреждений" % (stats.get("rows"), stats.get("orgs")))
    return data["file"]


async def _do_backup(job: dict) -> str:
    """Копия таблицы перед необратимым удалением."""
    _step(job, "backup", "running")
    name = "cossu_backup_%s" % datetime.now().strftime("%Y%m%d_%H%M")
    async with AsyncSessionLocal() as db:
        await db.execute(text("DROP TABLE IF EXISTS %s" % name))
        await db.execute(text("CREATE TABLE %s AS SELECT * FROM cossu" % name))
        await db.commit()
    _log(job, "создана копия таблицы: %s" % name)
    _step(job, "backup", "done", name)
    return name


async def _run_pipeline(job_id: str, req: RefreshRequest) -> None:
    job = _jobs[job_id]
    try:
        before = await _counts()
        job["result"]["before"] = before
        _log(job, "сейчас в базе: %d строк, %d учреждений, без координат %d"
             % (before["rows"], before["orgs"], before["no_coords"]))

        file_name = await _do_export(job, req)
        path = "%s/%s" % (INPUT_DIR, file_name)

        job["result"]["backup_table"] = await _do_backup(job)

        _step(job, "seed", "running")
        out = await _run_script(job, "seed", ["worker/seed_cossu.py", path], timeout=900)
        m = re.search(r"Done:\s*(\d+)\s+inserted,\s*(\d+)\s+updated", out)
        inserted, updated = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        job["result"]["inserted"] = inserted
        job["result"]["updated"] = updated
        _step(job, "seed", "done", "добавлено %s, обновлено %s" % (inserted, updated))

        _step(job, "stale", "running")
        out = await _run_script(job, "stale", ["worker/cossu_remove_stale.py", path],
                                timeout=900)
        m = re.search(r"удалено:\s*(\d+)", out)
        deleted = int(m.group(1)) if m else None
        job["result"]["deleted"] = deleted
        _step(job, "stale", "done", "удалено %s" % deleted)

        key = (req.twogis_key or "").strip() or _twogis_key()
        if not req.geocode:
            _step(job, "geocode", "skipped", "пропущено по запросу")
            _log(job, "геокодирование пропущено по запросу")
        elif not key:
            _step(job, "geocode", "skipped", "нет ключа 2ГИС")
            _log(job, "геокодирование пропущено: ключ 2ГИС не задан")
        else:
            _step(job, "geocode", "running")
            # Сбой геокодирования (просроченный ключ, исчерпанный лимит, сеть)
            # не должен помечать всю задачу ошибкой: данные к этому моменту уже
            # обновлены и корректны, не хватает только координат у новых точек.
            # Их добьёт следующий запуск, когда ключ починят.
            try:
                out = await _run_script(job, "geocode", ["worker/geocode_cossu_2gis.py"],
                                        env={"TWOGIS_API_KEY": key}, timeout=3600)
                m = re.search(r"ИТОГО:\s*успешно=(\d+),\s*не найдено=(\d+)", out)
                ok, miss = (int(m.group(1)), int(m.group(2))) if m else (None, None)
                job["result"]["geocoded"] = ok
                job["result"]["geocode_missed"] = miss
                _step(job, "geocode", "done", "найдено %s, не найдено %s" % (ok, miss))
            except Exception as exc:               # noqa: BLE001
                job["result"]["geocode_error"] = str(exc)
                _log(job, "геокодирование не выполнено: %s" % exc)
                _step(job, "geocode", "error", "не выполнено — проверьте ключ 2ГИС")

        after = await _counts()
        job["result"]["after"] = after
        _log(job, "стало: %d строк, %d учреждений, без координат %d"
             % (after["rows"], after["orgs"], after["no_coords"]))

        job["status"] = "done"
        job["progress"] = 100
    except Exception as exc:                       # noqa: BLE001 — показываем любую
        job["status"] = "error"
        job["error"] = str(exc)
        _log(job, "ОШИБКА: %s" % exc)
        for item in job["steps"]:
            if item["status"] == "running":
                item["status"] = "error"
    finally:
        job["finished_at"] = datetime.now().isoformat(timespec="seconds")


@router.post("/cossu/refresh")
async def refresh_cossu(req: RefreshRequest,
                        user: User = Depends(require_admin)) -> dict:
    """Запускает обновление ЦОССУ. Возвращает идентификатор задачи."""
    async with _job_lock:
        running = [j for j in _jobs.values() if j["status"] == "running"]
        if running:
            raise HTTPException(status_code=409,
                                detail="Обновление уже идёт (задача %s)" % running[0]["id"])
        job = _new_job()
        _jobs[job["id"]] = job
        # Старые задачи не копим — держим последние двадцать.
        if len(_jobs) > 20:
            for old in sorted(_jobs.values(), key=lambda j: j["started_at"])[:-20]:
                _jobs.pop(old["id"], None)

    asyncio.create_task(_run_pipeline(job["id"], req))
    return {"job_id": job["id"]}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, user: User = Depends(require_admin)) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return job


@router.get("/cossu/state")
async def cossu_state(user: User = Depends(require_admin)) -> dict:
    """Текущее состояние таблицы и доступность сервиса выгрузки —
    показывается при открытии окна, до запуска обновления."""
    counts = await _counts()
    exporter_ok, exporter_error = False, None
    headers = {"X-Exporter-Token": EXPORTER_TOKEN} if EXPORTER_TOKEN else {}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("%s/health" % EXPORTER_URL, headers=headers)
            exporter_ok = r.status_code == 200
    except httpx.HTTPError as exc:
        exporter_error = str(exc)

    last = None
    if _jobs:
        last = sorted(_jobs.values(), key=lambda j: j["started_at"])[-1]

    key = _twogis_key()
    from_env = bool(os.environ.get("TWOGIS_API_KEY", "").strip()) \
        and not _load_settings().get("twogis_key")
    return {
        "counts": counts,
        "exporter_ok": exporter_ok,
        "exporter_error": exporter_error,
        "has_2gis_key": bool(key),
        "twogis_key_masked": _mask_key(key),
        "twogis_key_from_env": from_env,
        "last_job": last,
    }


class TwogisKeyRequest(BaseModel):
    key: str | None = None      # пустая строка или null — стереть сохранённый


@router.put("/settings/twogis-key")
async def set_twogis_key(req: TwogisKeyRequest,
                         user: User = Depends(require_admin)) -> dict:
    """Сохраняет или стирает ключ 2ГИС. Ключи выдают на срок, поэтому менять
    его нужно без пересборки образа."""
    key = (req.key or "").strip()
    if key and (len(key) < 16 or any(c.isspace() for c in key)):
        raise HTTPException(status_code=400,
                            detail="Ключ выглядит некорректно: ожидается строка "
                                   "без пробелов длиной от 16 символов")
    settings = _load_settings()
    if key:
        settings["twogis_key"] = key
        settings["twogis_key_set_at"] = datetime.now().isoformat(timespec="seconds")
    else:
        settings.pop("twogis_key", None)
        settings.pop("twogis_key_set_at", None)
    _save_settings(settings)

    effective = _twogis_key()
    return {"ok": True, "has_key": bool(effective),
            "masked": _mask_key(effective)}
