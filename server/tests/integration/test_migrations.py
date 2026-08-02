from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine
from testforge import models  # noqa: F401  ensures all tables register on Base.metadata
from testforge.db.base import Base

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def test_migrations_produce_the_schema_the_models_declare(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "head")

    engine = create_engine(url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], f"models and migrations disagree: {diff}"
