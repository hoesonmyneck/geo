# ЭЦП-авторизация РК через NCALayer (2FA) — полный гайд

Воспроизводимое описание двухфакторной авторизации (пароль + ЭЦП Казахстана)
через NCALayer. Собрано из рабочей реализации: `backend/app/core/eds.py`,
`backend/app/api/routes/auth.py`, фронтенд (WebSocket к NCALayer), `Caddyfile`.

---

## Архитектура потока

```
1. POST /api/auth/login {login, password}
   → сервер проверяет пароль
   → если у юзера есть ИИН: генерит JWT-challenge (с uid внутри),
     отдаёт {requires_2fa: true, challenge}
   → если ИИН нет: сразу выдаёт куки (для admin без привязки)

2. Фронт: открывает WebSocket к NCALayer, отдаёт challenge на подпись
   → NCALayer показывает окно выбора ключа ЭЦП, юзер подписывает
   → получаем CMS-подпись (base64)

3. POST /api/auth/login-2fa {challenge, signature}
   → сервер парсит CMS, проверяет что подписан ИМЕННО его challenge,
     извлекает ИИН из сертификата, сверяет с аккаунтом
   → выдаёт httponly-куки (access/refresh JWT)
```

**Ключевая идея:** challenge — это **подписанный сервером JWT** с коротким TTL
(5 мин) и `uid` внутри. Сервер НЕ хранит challenge в БД — он самопроверяемый
(подпись JWT-секретом). Защита от replay (TTL + nonce) и от подмены пользователя
(uid зашит в подписанный токен).

---

## Backend

### Зависимости
```
asn1crypto      # парсинг CMS/ASN.1 (НЕ pyOpenSSL — он не даёт удобного доступа к encapContentInfo)
python-jose     # JWT challenge
```

### Challenge (генерация / проверка)
```python
import secrets
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError

CHALLENGE_TTL_MINUTES = 5

def generate_challenge(user_id: int | None = None) -> str:
    payload = {
        "sub": "eds_challenge",
        "nonce": secrets.token_urlsafe(32),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=CHALLENGE_TTL_MINUTES),
    }
    if user_id is not None:
        payload["uid"] = user_id          # привязка ко 2FA
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_challenge(challenge: str) -> dict:
    try:
        payload = jwt.decode(challenge, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as e:
        raise EDSError(f"Challenge invalid or expired: {e}")
    if payload.get("sub") != "eds_challenge":
        raise EDSError("Challenge has wrong subject")
    return payload
```

### Парсинг CMS-подписи и проверка
```python
import base64, re
from asn1crypto import cms as asn1_cms

# OID для извлечения данных из Subject сертификата НУЦ РК
OID_SERIAL_NUMBER = "2.5.4.5"     # содержит "IIN123456789012"
OID_SURNAME       = "2.5.4.4"     # фамилия
OID_GIVEN_NAME    = "2.5.4.42"    # имя+отчество (в РК ЧАСТО только отчество!)
OID_COMMON_NAME   = "2.5.4.3"     # ФИО целиком — приоритет

def verify_eds_signature(challenge: str, cms_b64: str) -> EDSResult:
    # 1. challenge свежий и валидный
    decode_challenge(challenge)

    # 2. парсим CMS
    content_info = asn1_cms.ContentInfo.load(base64.b64decode(cms_b64))
    if content_info["content_type"].native != "signed_data":
        raise EDSError("CMS is not SignedData")
    signed_data = content_info["content"]

    # 3. сертификат подписавшего
    certs = signed_data["certificates"]
    if not certs:
        raise EDSError("CMS does not contain certificates")
    cert = certs[0].chosen   # x509.Certificate

    # 4. проверяем что в encapContentInfo лежит ИМЕННО наш challenge
    encap = signed_data["encap_content_info"]
    if encap["content"].native is None:
        raise EDSError("CMS has no encapsulated content — sign with attached=true")
    original = encap["content"].native
    original_str = original.decode("utf-8") if isinstance(original, bytes) else str(original)
    if original_str.strip() != challenge.strip():
        raise EDSError("Signed data does not match issued challenge")

    # 5. извлекаем ИИН / ФИО
    return EDSResult(iin=_extract_iin(cert), fio=_extract_fio(cert), ...)

def _extract_iin(cert) -> str:
    for rdn in cert.subject.chosen:
        for attr in rdn:
            if attr["type"].dotted == OID_SERIAL_NUMBER:
                m = re.search(r"(\d{12})", str(attr["value"].native))
                if m:
                    return m.group(1)
    raise EDSError("IIN not found in certificate (serialNumber field)")

def _extract_fio(cert) -> str:
    cn = surname = given = None
    for rdn in cert.subject.chosen:
        for attr in rdn:
            oid, val = attr["type"].dotted, str(attr["value"].native)
            if   oid == OID_COMMON_NAME: cn = val
            elif oid == OID_SURNAME:     surname = val
            elif oid == OID_GIVEN_NAME:  given = val
    if cn: return cn                              # CN = полное ФИО, приоритет
    if surname and given: return f"{surname} {given}"
    return surname or "Не указано"
```

### login-2fa (сверка ИИН)
```python
@router.post("/login-2fa")
async def login_2fa(body, response, db):
    payload = decode_challenge(body.challenge)
    user_id = payload.get("uid")                 # uid из подписанного challenge
    user = await db.get(User, user_id)
    eds = verify_eds_signature(body.challenge, body.signature)
    if eds.iin != user.iin:                       # сертификат принадлежит этому аккаунту?
        raise HTTPException(401, f"ИИН в ЭЦП ({eds.iin}) не совпадает с аккаунтом")
    user.fio = eds.fio                            # обновляем ФИО из серта
    _set_tokens(response, user)                   # httponly access/refresh
    return UserOut.model_validate(user)
```

---

## Frontend (WebSocket к NCALayer)

```javascript
// NCALayer слушает локально на двух портах:
//   wss://127.0.0.1:13579 — для HTTPS-сайтов (самоподписанный серт)
//   ws://127.0.0.1:14579  — для HTTP-сайтов
const NCALAYER_URLS = ['wss://127.0.0.1:13579/', 'ws://127.0.0.1:14579/'];

// Открытие с фоллбэком: пробуем wss, таймаут 3с → ws
function _openNCALayer() {
  return new Promise((resolve, reject) => {
    let i = 0;
    (function tryNext() {
      if (i >= NCALAYER_URLS.length) return reject(new Error('NCALayer недоступен'));
      const ws = new WebSocket(NCALAYER_URLS[i++]);
      const t = setTimeout(() => { try{ws.close();}catch{} tryNext(); }, 3000);
      ws.addEventListener('open',  () => { clearTimeout(t); resolve(ws); });
      ws.addEventListener('error', () => { clearTimeout(t); try{ws.close();}catch{} tryNext(); });
    })();
  });
}

async function runEdsSecondFactor(challenge) {
  const ws = await _openNCALayer();
  let handshakeReceived = false;

  function sendSignRequest() {
    // Старое API NCALayer 1.x — позиционные аргументы:
    // args = [storageType, keyType, base64Data, attached]
    const base64Data = btoa(unescape(encodeURIComponent(challenge)));
    ws.send(JSON.stringify({
      module: 'kz.gov.pki.knca.commonUtils',
      method: 'createCAdESFromBase64',
      args: ['PKCS12', 'SIGNATURE', base64Data, true]   // attached=true ОБЯЗАТЕЛЕН
    }));
  }

  ws.onmessage = async (event) => {
    const response = JSON.parse(event.data);

    // 1) ПЕРВОЕ сообщение — handshake {"result":{"version":"1.4"}}.
    //    Запрос на подпись шлём ТОЛЬКО после него.
    if (!handshakeReceived && response.result?.version) {
      handshakeReceived = true;
      sendSignRequest();
      return;
    }
    // 2) ошибка
    if (response.code && response.code !== '200') { /* показать response.message */ return; }
    // 3) подпись: старое API → response.result (строка), новое → response.responseObject
    const signature = response.responseObject ||
                      (typeof response.result === 'string' ? response.result : null);
    ws.close();

    // отправляем на сервер
    await fetch('/api/auth/login-2fa', {
      method: 'POST', credentials: 'include',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ challenge, signature }),
    });
  };
}
```

---

## HTTPS (Caddy `tls internal`)

NCALayer по `wss://` требует, чтобы сайт открывался по **HTTPS** (origin-проверка).
Поднимаем самоподписанный серт через Caddy:

```caddyfile
{
    # НЕ "auto_https off" — это отключит internal CA и сломает tls internal!
    auto_https disable_redirects
}

# HTTP (локалка)
:80 {
    import common
}

# HTTPS — ЯВНО указываем хосты, иначе tls internal не знает имя для серта
interaction.enbek.kz, localhost {
    tls internal
    import common
}
```

---

## ⚠️ Подводные камни (на каждый потратили время)

1. **NCALayer требует HTTPS-origin для `wss://13579`.** По HTTP-сайту wss не
   подключится. Нужен HTTPS (Caddy `tls internal`, можно на нестандартном порту 4443).

2. **`auto_https off` ломает `tls internal`** → `ERR_SSL_PROTOCOL_ERROR`.
   Использовать `auto_https disable_redirects` (отключает только HTTP→HTTPS редирект,
   но оставляет генерацию сертов).

3. **`tls internal` на `:443` без имени хоста не работает** — Caddy не знает, на какое
   имя выпускать серт. Перечислить хосты явно: `interaction.enbek.kz, localhost { tls internal }`.

4. **NCALayer шлёт handshake ПЕРВЫМ** (`{"result":{"version":"1.4"}}`). Если отправить
   запрос на подпись до handshake — он потеряется. Дождаться handshake, потом слать.

5. **Метода `signData` нет в NCALayer 1.4.** Старые версии используют
   `createCAdESFromBase64` с **позиционными** аргументами `['PKCS12','SIGNATURE',base64,true]`.

6. **Формат ответа отличается между версиями.** Старое API → `response.result` (строка),
   новое → `response.responseObject`. Поддерживать оба.

7. **`attached=true` (encapsulate) обязателен.** Без него подписанные данные не
   вкладываются в CMS, и сервер не сможет проверить, что подписан именно его challenge
   (`encap_content_info` будет пустой).

8. **`givenName` в сертификатах РК часто содержит ТОЛЬКО ОТЧЕСТВО, без имени.**
   Склейка `surname + givenName` даёт «Фамилия Отчество». **Всегда брать CN** — там полное ФИО.

9. **wss-серт NCALayer самоподписанный.** Браузер может блокировать `wss://127.0.0.1:13579`,
   пока юзер один раз вручную не примет серт (открыть `https://127.0.0.1:13579`).
   Фоллбэк на `ws://14579` обходит это для HTTP.

10. **Полная криптопроверка GOST-подписи не делается.** Реализация проверяет: структуру
    CMS + что внутри свежий challenge + ИИН из серта. Математическую проверку
    `signerInfo.signature` против publicKey (GOST 34.10) оставили TODO — нужна `pygost`.
    Для большинства задач достаточно: подделать CMS с валидным сертом НУЦ и нашим свежим
    challenge невозможно без приватного ключа владельца.

---

## Чеклист интеграции в новый проект

- [ ] `pip install asn1crypto python-jose`
- [ ] Эндпоинты `/login` (1FA + выдача challenge), `/login-2fa` (проверка CMS)
- [ ] `generate_challenge` / `decode_challenge` (JWT, TTL 5 мин, uid внутри)
- [ ] `verify_eds_signature` (asn1crypto, проверка encapContentInfo == challenge)
- [ ] Извлечение ИИН (OID 2.5.4.5, regex `\d{12}`) и ФИО (CN, OID 2.5.4.3)
- [ ] Фронт: WebSocket wss→ws фоллбэк, ждать handshake, `createCAdESFromBase64` attached=true
- [ ] Caddy: `auto_https disable_redirects` + `tls internal` с явными хостами
- [ ] Привязка ИИН к аккаунту в БД (поле `users.iin`)
