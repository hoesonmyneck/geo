"""FastAPI dependencies: get current user, role guards."""
from fastapi import Cookie, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.models import EDIT_ROLES, User, UserRole, effective_sections
from app.db.session import get_db


async def get_current_user(
    access_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )
    if not access_token:
        raise exc
    try:
        payload = decode_token(access_token)
        if payload.get("type") != "access":
            raise exc
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise exc

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise exc
    return user


def require_role(*roles: UserRole):
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user
    return _check


require_editor = require_role(UserRole.editor, UserRole.admin)
require_admin  = require_role(UserRole.admin)


def require_section(section: str):
    """Доступ к разделу (любой уровень — просмотр разрешён)."""
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if section not in effective_sections(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No access to section '{section}'",
            )
        return current_user
    return _check


def require_section_edit(section: str):
    """Правки в разделе: нужен доступ к разделу И уровень editor/admin."""
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if section not in effective_sections(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No access to section '{section}'",
            )
        if current_user.role not in EDIT_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Editor access required",
            )
        return current_user
    return _check
