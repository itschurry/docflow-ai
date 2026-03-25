import os
os.environ["DATABASE_URL"] = "sqlite:///storage/db/docflow.db"

from alembic.config import Config
from alembic import command

cfg = Config()
cfg.set_main_option("script_location", "migration")
cfg.set_main_option("sqlalchemy.url", "sqlite:///storage/db/docflow.db")
command.upgrade(cfg, "head")
print("Migration complete")
