"""ЭЦП Казахстан — парсинг CMS-подписи от NCALayer, извлечение ИИН.

Поток:
1. Сервер генерирует JWT-challenge через generate_challenge()
2. Клиент через NCALayer подписывает этот challenge → получает CMS в base64
3. Сервер вызывает verify_eds_signature(challenge, cms_b64) →
   парсит CMS, извлекает сертификат, проверяет что подписан именно наш challenge,
   возвращает ИИН и ФИО.

ВАЖНО: полноценная математическая проверка GOST 34.10-подписи
       требует библиотеки pygost + тестирования с реальным NCALayer.
       Текущая реализация проверяет:
       - корректность структуры CMS
       - что в подписанных данных лежит наш свежий challenge (с защитой по JWT)
       - извлекает ИИН/ФИО из сертификата
       TODO: добавить криптографическую проверку signerInfo.signature
             против publicKey из сертификата через pygost.
"""
from __future__ import annotations

import base64
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from asn1crypto import cms as asn1_cms
from jose import jwt, JWTError

from app.core.config import settings

CHALLENGE_TTL_MINUTES = 5

# OID'ы для извлечения данных из Subject сертификата НУЦ РК
OID_SERIAL_NUMBER = "2.5.4.5"     # содержит "IIN123456789012"
OID_SURNAME       = "2.5.4.4"     # фамилия
OID_GIVEN_NAME    = "2.5.4.42"    # имя + отчество
OID_COMMON_NAME   = "2.5.4.3"     # ФИО целиком


class EDSError(Exception):
    """Ошибка проверки ЭЦП."""


@dataclass
class EDSResult:
    iin: str
    fio: str
    raw_subject: str


# ─── Challenge ───────────────────────────────────────────────────────────────

def generate_challenge(user_id: int | None = None) -> str:
    """Генерирует подписанный JWT-challenge, который клиент должен подписать через NCALayer.

    Если указан user_id — challenge привязывается к конкретному пользователю
    (используется в 2FA-сценарии: первый фактор пароль → выдаём challenge с user_id
    → второй фактор ЭЦП → проверяем что ИИН в сертификате совпадает с этим user_id).
    """
    payload = {
        "sub": "eds_challenge",
        "nonce": secrets.token_urlsafe(32),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=CHALLENGE_TTL_MINUTES),
    }
    if user_id is not None:
        payload["uid"] = user_id
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_challenge(challenge: str) -> dict:
    """Декодирует и валидирует challenge, возвращает payload.
    Бросает EDSError если challenge невалиден или истёк.
    """
    try:
        payload = jwt.decode(challenge, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as e:
        raise EDSError(f"Challenge invalid or expired: {e}")
    if payload.get("sub") != "eds_challenge":
        raise EDSError("Challenge has wrong subject")
    return payload


def _verify_challenge_token(challenge: str) -> None:
    decode_challenge(challenge)


# ─── CMS parsing ─────────────────────────────────────────────────────────────

def verify_eds_signature(challenge: str, cms_b64: str) -> EDSResult:
    """Главная функция: проверяет что CMS подписан нашим challenge,
    извлекает ИИН/ФИО из сертификата подписавшего.

    Args:
        challenge: JWT-challenge выданный сервером (вернулся из /eds-challenge)
        cms_b64:   base64-CMS подписанный NCALayer (формат signData + format='cms')

    Returns:
        EDSResult с ИИН и ФИО владельца сертификата

    Raises:
        EDSError: если challenge невалиден, CMS невалиден,
                  signed-data не содержит наш challenge, нет ИИН в сертификате.
    """
    # 1. challenge свежий и валидный
    _verify_challenge_token(challenge)

    # 2. парсим CMS
    try:
        cms_bytes = base64.b64decode(cms_b64)
        content_info = asn1_cms.ContentInfo.load(cms_bytes)
    except Exception as e:
        raise EDSError(f"Cannot parse CMS: {e}")

    if content_info["content_type"].native != "signed_data":
        raise EDSError("CMS is not SignedData")

    signed_data = content_info["content"]

    # 3. достаём сертификат подписавшего
    certs = signed_data["certificates"]
    if not certs or len(certs) == 0:
        raise EDSError("CMS does not contain certificates")
    cert = certs[0].chosen  # x509.Certificate

    # 4. проверяем что в encapContentInfo лежит именно наш challenge
    encap = signed_data["encap_content_info"]
    if encap["content"].native is None:
        raise EDSError(
            "CMS has no encapsulated content — sign with encapsulate=true in NCALayer"
        )
    original = encap["content"].native
    if isinstance(original, bytes):
        try:
            original_str = original.decode("utf-8")
        except UnicodeDecodeError:
            raise EDSError("Encapsulated content is not UTF-8")
    else:
        original_str = str(original)

    if original_str.strip() != challenge.strip():
        raise EDSError("Signed data does not match issued challenge")

    # 5. извлекаем ИИН / ФИО из Subject сертификата
    iin = _extract_iin(cert)
    fio = _extract_fio(cert)
    raw_subject = cert.subject.human_friendly if hasattr(cert.subject, "human_friendly") else str(cert.subject.native)

    # TODO: математическая проверка signerInfo.signature через pygost
    #       (нужны: алгоритм подписи из cert, публичный ключ, signed attributes)

    return EDSResult(iin=iin, fio=fio, raw_subject=raw_subject)


def _extract_iin(cert) -> str:
    """Извлекает 12-значный ИИН из Subject сертификата.
    В сертификатах НУЦ РК ИИН лежит в поле serialNumber (OID 2.5.4.5)
    в формате 'IIN123456789012' или просто '123456789012'.
    """
    for rdn in cert.subject.chosen:
        for attr in rdn:
            type_oid = attr["type"].dotted
            if type_oid != OID_SERIAL_NUMBER:
                continue
            value = str(attr["value"].native)
            # формат: "IIN123456789012" или "123456789012"
            m = re.search(r"(\d{12})", value)
            if m:
                return m.group(1)
    raise EDSError("IIN not found in certificate (serialNumber field)")


def _extract_fio(cert) -> str:
    """Извлекает ФИО из Subject сертификата НУЦ РК.

    В сертификатах НУЦ РК:
    - commonName (CN, OID 2.5.4.3) — содержит ПОЛНОЕ ФИО ('ФАМИЛИЯ ИМЯ ОТЧЕСТВО')
    - surname (OID 2.5.4.4) — фамилия
    - givenName (OID 2.5.4.42) — в РК часто содержит ТОЛЬКО отчество (без имени!)

    Поэтому склейка surname+givenName даёт 'Фамилия Отчество', без имени.
    Правильнее всегда брать CN, если он есть.
    """
    cn = None
    surname = None
    given_name = None
    for rdn in cert.subject.chosen:
        for attr in rdn:
            type_oid = attr["type"].dotted
            value = str(attr["value"].native)
            if type_oid == OID_COMMON_NAME:
                cn = value
            elif type_oid == OID_SURNAME:
                surname = value
            elif type_oid == OID_GIVEN_NAME:
                given_name = value

    # 1. CN обычно содержит полное "ФАМИЛИЯ ИМЯ ОТЧЕСТВО" — это приоритет
    if cn:
        return cn
    # 2. Фоллбэк: surname + givenName (может не содержать имя)
    if surname and given_name:
        return f"{surname} {given_name}"
    if surname:
        return surname
    return "Не указано"
