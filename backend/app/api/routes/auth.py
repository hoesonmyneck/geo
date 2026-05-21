"""Auth endpoints: login, refresh, logout, me."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
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


class UserOut(BaseModel):
    id: int
    login: str
    role: str

    model_config = {"from_attributes": True}


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


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    result = await db.execute(select(User).where(User.login == body.login))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()

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


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current_user)
