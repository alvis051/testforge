import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from testforge import models  # noqa: F401  registers all tables
from testforge.config import get_settings
from testforge.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# CI (and any other caller) can redirect migrations to a different database by
# setting TESTFORGE_DATABASE_URL in the environment. This takes priority over
# both the ini's static default and any config-API override (e.g. the one
# test_migrations.py applies via config.set_main_option), because it's an
# explicit, intentional signal from the caller's environment. We check the
# env var directly rather than via get_settings() so that this only fires
# when the env var is actually set — get_settings() would otherwise always
# return a value (its own default) and unconditionally clobber per-test
# overrides that never touch the environment.
_env_database_url = os.environ.get("TESTFORGE_DATABASE_URL")
if _env_database_url:
    config.set_main_option("sqlalchemy.url", _env_database_url)

if not config.get_main_option("sqlalchemy.url", "").strip():
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
