import os
from pathlib import Path
import sqlite3

from alembic.config import Config
from alembic import command

project_root = Path(__file__).resolve().parents[2]
database_url = os.getenv("DATABASE_URL", "sqlite:///storage/db/docflow.db")
sqlite_path: Path | None = None

if database_url.startswith("sqlite:///"):
    raw_path = database_url[len("sqlite:///"):]
    sqlite_path = Path(raw_path)
    if not sqlite_path.is_absolute():
        sqlite_path = (project_root / raw_path).resolve()
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    database_url = f"sqlite:///{sqlite_path}"

os.environ["DATABASE_URL"] = database_url

cfg = Config()
cfg.set_main_option("script_location", "migration")
cfg.set_main_option("sqlalchemy.url", database_url)

if sqlite_path is not None and sqlite_path.exists():
    with sqlite3.connect(sqlite_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        version_rows = []
        team_run_columns = set()
        if "alembic_version" in tables:
            version_rows = conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchall()
        if "team_runs" in tables:
            team_run_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(team_runs)").fetchall()
            }
    needs_team_run_repair = (
        "team_runs" in tables
        and (
            "rag_config" not in team_run_columns
            or "review_mode" not in team_run_columns
        )
    )
    if tables and needs_team_run_repair:
        command.stamp(cfg, "20260324_0013")
        command.upgrade(cfg, "head")
        print("Migration complete (repaired legacy SQLite schema)")
    elif tables and not version_rows:
        command.stamp(cfg, "head")
        print("Migration complete (stamped existing SQLite schema)")
    else:
        command.upgrade(cfg, "head")
        print("Migration complete")
else:
    command.upgrade(cfg, "head")
    print("Migration complete")
