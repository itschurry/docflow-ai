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
os.environ["EXECUTION_BACKEND"] = "inline"
os.environ["UPLOAD_DIR"] = str(UPLOAD_PATH)
os.environ["LLM_PROVIDER"] = "stub"


@pytest.fixture(scope="session")
def client() -> TestClient:
    if DB_PATH.exists():
        DB_PATH.unlink()
    if UPLOAD_PATH.exists():
        shutil.rmtree(UPLOAD_PATH)

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    if DB_PATH.exists():
        DB_PATH.unlink()
    if UPLOAD_PATH.exists():
        shutil.rmtree(UPLOAD_PATH)
