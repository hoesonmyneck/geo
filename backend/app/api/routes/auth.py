"""Auth endpoints: login (1FA), login-2fa (ЭЦП), refresh, logout, me.

Флоу авторизации:
1. POST /api/auth/login {login, password}
   - если пароль неверен → 401
   - если у юзера НЕТ iin → выдаём cookies сразу, возвращаем UserOut с requires_2fa=false
   - если у юзера ЕСТЬ iin → возвращаем requires_2fa=true + challenge (cookies НЕ выдаём)
2. POST /api/auth/login-2fa {challenge, signature}
   - проверяем что challenge валидный и не истёк
   - проверяем CMS-подпись через NCALayer
   - извлекаем ИИН из сертификата и сравниваем с user.iin
   - выдаём cookies, возвращаем UserOut
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.eds import EDSError, decode_challenge, generate_challenge, verify_eds_signature
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.core.config import settings
from app.db.models import User
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

ACCESS_MAX_AGE  = settings.JWT_ACCESS_EXPIRE_MINUTES * 60
REFRESH_MAX_AGE = settings.JWT_REFRESH_EXPIRE_DAYS * 86400


class LoginRequest(BaseModel):
    login: str
    password: str


class TwoFARequest(BaseModel):
    challenge: str   # выданный из /login (содержит user_id)
    signature: str   # base64 CMS от NCALayer


class UserOut(BaseModel):
    id: int
    login: str
    role: str
    fio: str | None = None

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    """Ответ /login: либо полный успех (user + cookies), либо требование 2FA."""
    requires_2fa: bool
    challenge: str | None = None   # если requires_2fa=true — подписать через NCALayer
    user: UserOut | None = None    # если requires_2fa=false — полный успех


def _set_tokens(response: Response, user: User) -> None:
    access  = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id)
    response.set_cookie(
        "access_token", access,
        max_age=ACCESS_MAX_AGE, httponly=True, samesite="lax", secure=False,
    )
    response.set_cookie(
        "refresh_token", refresh,
        max_age=REFRESH_MAX_AGE, httponly=True, samesite="lax", secure=False,
        path="/api/auth",
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    result = await db.execute(select(User).where(User.login == body.login))
    user = result.scalar_one_or_none()
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    # У пользователя есть ИИН → требуем 2FA через ЭЦП
    if user.iin:
        challenge = generate_challenge(user_id=user.id)
        return LoginResponse(requires_2fa=True, challenge=challenge)

    # ИИН нет → пускаем сразу (например, для admin без привязки)
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    _set_tokens(response, user)
    return LoginResponse(requires_2fa=False, user=UserOut.model_validate(user))


@router.post("/login-2fa", response_model=UserOut)
async def login_2fa(
    body: TwoFARequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """Второй фактор: проверяем CMS-подпись и что ИИН совпадает с аккаунтом."""
    # 1. валидируем challenge и достаём user_id из него
    try:
        payload = decode_challenge(body.challenge)
    except EDSError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Challenge: {e}")
    user_id = payload.get("uid")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Challenge has no user_id")

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.iin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User has no IIN bound")

    # 2. проверяем CMS-подпись (что в ней наш challenge, извлекаем ИИН и ФИО)
    try:
        eds = verify_eds_signature(body.challenge, body.signature)
    except EDSError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"EDS: {e}")

    # 3. проверяем что ИИН в сертификате совпадает с аккаунтом
    if eds.iin != user.iin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"ИИН в ЭЦП ({eds.iin}) не совпадает с аккаунтом",
        )

    # 4. обновляем ФИО из сертификата (на случай если изменилось)
    user.fio = eds.fio
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)

    _set_tokens(response, user)
    return UserOut.model_validate(user)


@router.post("/refresh")
async def refresh(
    response: Response,
    refresh_token: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    from fastapi import Cookie
    exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if not refresh_token:
        raise exc
    try:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise exc
        user_id = int(payload["sub"])
    except Exception:
        raise exc

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise exc

    _set_tokens(response, user)
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token", path="/api/auth")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current_user)
