from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routes import router
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

project_root = Path(__file__).resolve().parents[3]
react_root = project_root / "apps" / "web"
react_dist = react_root / "dist"
react_assets = react_dist / "assets"
react_index = react_dist / "index.html"
legacy_workspace = react_root / "assets" / "legacy-workspace.html"

if react_assets.exists():
    app.mount("/assets", StaticFiles(directory=str(react_assets)),
              name="react-assets")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return JSONResponse(status_code=204, content=None)


@app.get("/", include_in_schema=False)
def root():
    if react_index.exists():
        return FileResponse(str(react_index))
    if legacy_workspace.exists():
        return FileResponse(str(legacy_workspace))
    return JSONResponse({"detail": "React UI is not available"}, status_code=503)


@app.get("/workspace", include_in_schema=False)
def workspace_page():
    if react_index.exists():
        return FileResponse(str(react_index))
    if legacy_workspace.exists():
        return FileResponse(str(legacy_workspace))
    return JSONResponse({"detail": "React UI is not available"}, status_code=503)


@app.get("/legacy-workspace", include_in_schema=False)
def legacy_workspace_page():
    if legacy_workspace.exists():
        return FileResponse(str(legacy_workspace))
    return JSONResponse({"detail": "Legacy workspace is not available"}, status_code=503)
