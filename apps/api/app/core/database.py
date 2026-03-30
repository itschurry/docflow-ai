from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings


class Base(DeclarativeBase):
    pass


_is_sqlite = settings.database_url.startswith("sqlite")

engine_kwargs = {"pool_pre_ping": True}
if _is_sqlite:
    # Reduce long request stalls when concurrent writes happen in inline mode.
    engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 5}
    # SQLite in this app runs inline with short-lived sessions; disabling the
    # queue pool avoids request pile-ups timing out while waiting for a pooled
    # connection during board polling bursts.
    engine_kwargs["poolclass"] = NullPool

engine = create_engine(settings.database_url, **engine_kwargs)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    # type: ignore[unused-argument]
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
