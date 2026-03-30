from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.routes import router
from app.routes.web_runs import recover_team_runs_on_startup
from app.core.database import Base, engine
from app.core.config import settings
from app import models  # noqa: F401
from app import conversation_models  # noqa: F401


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)
    await recover_team_runs_on_startup()
    yield

app = FastAPI(title=settings.app_name,
              version=settings.app_version, lifespan=lifespan)

app.include_router(router)
