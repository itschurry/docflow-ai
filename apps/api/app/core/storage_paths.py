from __future__ import annotations

from pathlib import Path

from app.core.config import settings


def _project_root() -> Path:
    return Path(settings.upload_dir).resolve().parent


def absolute_storage_path(path: str | Path) -> Path:
    raw = Path(path)
    if raw.is_absolute():
        if raw.exists():
            return raw
        parts = raw.parts
        if "storage" in parts:
            storage_index = parts.index("storage")
            return (_project_root() / Path(*parts[storage_index:])).resolve()
        return raw
    return (_project_root() / raw).resolve()


def storage_path_for_db(path: str | Path) -> str:
    resolved = Path(path).resolve()
    project_root = _project_root()
    try:
        return str(resolved.relative_to(project_root))
    except ValueError:
        return str(resolved)
