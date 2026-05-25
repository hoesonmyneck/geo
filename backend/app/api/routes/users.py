"""User management (admin only)."""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.core.security import hash_password
from app.db.models import User, UserRole
from app.db.session import get_db

router = APIRouter(prefix="/users", tags=["users"])

IIN_RE = re.compile(r"^\d{12}$")


def _normalize_iin(iin: str | None) -> str | None:
    """Принимает '' / пробелы как None, валидирует 12 цифр."""
    if iin is None:
        return None
    s = iin.strip()
    if not s:
        return None
    if not IIN_RE.match(s):
        raise HTTPException(status_code=400, detail="ИИН должен состоять ровно из 12 цифр")
    return s


class UserCreate(BaseModel):
    login: str
    password: str
    role: UserRole = UserRole.viewer
    iin: str | None = None        # если указан — будет требоваться 2FA через ЭЦП
    fio: str | None = None        # человеческое имя для отображения


class UserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = None
    iin: str | None = None       # пустая строка = убрать привязку к ЭЦП
    fio: str | None = None


class UserOut(BaseModel):
    id: int
    login: str
    role: str
    is_active: bool
    iin: str | None = None
    fio: str | None = None

    model_config = {"from_attributes": True}


@router.get("", dependencies=[Depends(require_admin)])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[UserOut]:
    result = await db.execute(select(User).order_by(User.id))
    return [UserOut.model_validate(u) for u in result.scalars()]


@router.post("", dependencies=[Depends(require_admin)], status_code=201)
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db)) -> UserOut:
    existing = await db.execute(select(User).where(User.login == body.login))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Login already taken")

    iin = _normalize_iin(body.iin)
    if iin:
        # Проверим что такой ИИН ещё не привязан к другому аккаунту
        dup = await db.execute(select(User).where(User.iin == iin))
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="ИИН уже привязан к другому аккаунту")

    user = User(
        login=body.login,
        password_hash=hash_password(body.password),
        role=body.role,
        iin=iin,
        fio=(body.fio or "").strip() or None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.patch("/{user_id}", dependencies=[Depends(require_admin)])
async def update_user(
    user_id: int, body: UserUpdate, db: AsyncSession = Depends(get_db)
) -> UserOut:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.iin is not None:  # пустая строка — снимаем привязку
        new_iin = _normalize_iin(body.iin)
        if new_iin and new_iin != user.iin:
            dup = await db.execute(select(User).where(User.iin == new_iin, User.id != user_id))
            if dup.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="ИИН уже привязан к другому аккаунту")
        user.iin = new_iin
    if body.fio is not None:
        user.fio = body.fio.strip() or None
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.delete("/{user_id}", dependencies=[Depends(require_admin)], status_code=204)
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await db.delete(user)
    await db.commit()
