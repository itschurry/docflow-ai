#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from testcontainers.postgres import PostgresContainer


def to_sqlalchemy_url(raw_url: str) -> str:
    if raw_url.startswith("postgresql+psycopg2://"):
        return raw_url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if raw_url.startswith("postgresql://"):
        return raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if raw_url.startswith("postgres://"):
        return raw_url.replace("postgres://", "postgresql+psycopg://", 1)
    return raw_url


def wait_for_health(base_url: str, timeout_sec: int = 30) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urlopen(f"{base_url}/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("API health check timed out")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    env["AUTO_CREATE_TABLES"] = "false"
    env.setdefault("LLM_PROVIDER", "stub")
    env.setdefault("EXECUTION_BACKEND", "inline")

    with PostgresContainer("postgres:16-alpine") as pg:
        env["DATABASE_URL"] = to_sqlalchemy_url(pg.get_connection_url())

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=root,
            env=env,
            check=True,
        )

        api_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8004"],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            wait_for_health("http://127.0.0.1:8004", timeout_sec=40)
            subprocess.run(
                ["bash", "./scripts/postgres_e2e_smoke.sh"],
                cwd=root,
                env={**env, "BASE_URL": "http://127.0.0.1:8004"},
                check=True,
            )
            print("testcontainers-full-check: success")
            return 0
        finally:
            api_proc.terminate()
            try:
                api_proc.wait(timeout=5)
            except Exception:
                api_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
