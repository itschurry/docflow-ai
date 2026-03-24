from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

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

react_dist = Path(__file__).resolve().parents[2] / "web-react" / "dist"

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}

def _react_index() -> FileResponse:
    return FileResponse(str(react_dist / "index.html"), headers=_NO_CACHE)

# Serve /assets/* (JS/CSS bundles)
if (react_dist / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(react_dist / "assets")), name="assets")

app.include_router(router)


# ── React app entry points ────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return _react_index()

@app.get("/workspace", include_in_schema=False)
def workspace_page():
    return _react_index()

# Serve root-level static files from dist (favicon.svg, icons.svg, etc.)
@app.get("/{filename:path}", include_in_schema=False)
def static_or_spa(filename: str, request: Request):
    """
    1. Serve known static files from the React dist root (favicon, icons…).
    2. For any other unknown path return the React SPA index so client-side
       routing works (avoids 404 on browser refresh).
    """
    candidate = (react_dist / filename).resolve()
    # Security: only serve files inside the dist directory
    try:
        candidate.relative_to(react_dist.resolve())
        if candidate.exists() and candidate.is_file():
            return FileResponse(str(candidate))
    except ValueError:
        pass
    # SPA fallback — let React Router handle it
    return _react_index()
