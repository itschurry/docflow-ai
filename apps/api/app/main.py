from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from app.api.routes import router
from app.core.config import settings
from app.core.database import Base, engine
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


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/workspace")


@app.get("/workspace", include_in_schema=False)
def workspace_page():
    workspace_html = Path(__file__).resolve().parents[2] / "web" / "templates" / "team_chat.html"
    return FileResponse(str(workspace_html))
