"""FastAPI application entrypoint."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, cossu, imports, kandas, places, users
from app.core.config import settings
from app.core.security import hash_password
from app.db.models import User, UserRole
from app.db.session import AsyncSessionLocal
from sqlalchemy import select


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Создаём первого admin-пользователя если БД пустая."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.role == UserRole.admin).limit(1))
        if not result.scalar_one_or_none():
            admin = User(
                login=settings.FIRST_ADMIN_LOGIN,
                password_hash=hash_password(settings.FIRST_ADMIN_PASSWORD),
                role=UserRole.admin,
            )
            db.add(admin)
            await db.commit()
            print(f"[startup] Created admin user: {settings.FIRST_ADMIN_LOGIN}")
    yield


app = FastAPI(
    title="Geo Platform API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,    prefix="/api")
app.include_router(users.router,   prefix="/api")
app.include_router(places.router,  prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(kandas.router,  prefix="/api")
app.include_router(cossu.router,   prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
