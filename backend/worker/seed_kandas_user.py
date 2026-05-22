"""Создаёт системный аккаунт kandas/kandas2026 с ролью admin_kandas."""
import asyncio, sys
sys.path.insert(0, "/app")

from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.db.models import User, UserRole
from app.core.security import hash_password

USERS_TO_CREATE = [
    {"login": "kandas", "password": "kandas2026", "role": UserRole.admin_kandas},
]

async def main():
    async with AsyncSessionLocal() as db:
        for u in USERS_TO_CREATE:
            result = await db.execute(select(User).where(User.login == u["login"]))
            existing = result.scalar_one_or_none()
            if existing:
                existing.role = u["role"]
                existing.password_hash = hash_password(u["password"])
                print(f"  Updated: {u['login']} role={u['role']}")
            else:
                db.add(User(
                    login=u["login"],
                    password_hash=hash_password(u["password"]),
                    role=u["role"],
                    is_active=True,
                ))
                print(f"  Created: {u['login']} role={u['role']}")
        await db.commit()
    print("Done.")

if __name__ == "__main__":
    asyncio.run(main())
