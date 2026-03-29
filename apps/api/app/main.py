from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.routes import router
from app.core.database import Base, engine
from app.core.config import settings
from app import models  # noqa: F401
from app import conversation_models  # noqa: F401


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title=settings.app_name,
              version=settings.app_version, lifespan=lifespan)

app.include_router(router)
