import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DB_PATH = Path("./test_api.db")
UPLOAD_PATH = Path("./test_storage")

# Ensure settings singleton reads test env values during import/collection time.
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["UPLOAD_DIR"] = str(UPLOAD_PATH)
os.environ["LLM_PROVIDER"] = "stub"
os.environ["OLLAMA_HOST"] = "http://localhost:11434"
os.environ["OLLAMA_MODEL"] = "qwen-test"


def _cleanup_sqlite_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}-shm"), Path(f"{path}-wal")):
        if candidate.exists():
            candidate.unlink()


@pytest.fixture(scope="session")
def client() -> TestClient:
    _cleanup_sqlite_files(DB_PATH)
    if UPLOAD_PATH.exists():
        shutil.rmtree(UPLOAD_PATH)

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    _cleanup_sqlite_files(DB_PATH)
    if UPLOAD_PATH.exists():
        shutil.rmtree(UPLOAD_PATH)
