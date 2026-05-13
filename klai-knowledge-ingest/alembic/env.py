"""Alembic async environment configuration for klai-knowledge-ingest.

Mirrors klai-connector/alembic/env.py. Uses a schema-isolated alembic_version
table (version_table_schema="knowledge") so migration history is separate from
portal-api's public.alembic_version and connector's connector.alembic_version.
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from DATABASE_URL env var (required in Docker).
# Falls back to POSTGRES_DSN for local dev parity with knowledge_ingest.config.
if db_url := os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN"):
    config.set_main_option("sqlalchemy.url", db_url)

# knowledge-ingest does not use a SQLAlchemy declarative Base for its schema
# (tables are created via op.execute raw SQL in the baseline migration).
# Setting target_metadata to None disables autogenerate — new migrations must
# be written by hand.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine.
    Calls to ``context.execute()`` emit the given string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version",
        version_table_schema="knowledge",
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    """Run migrations using the provided connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Isolate version tracking from portal-api alembic (shared DB).
        # knowledge schema is owned by klai superuser; this table is created
        # by alembic itself and is owned by the connecting role (portal_api or
        # knowledge_ingest service account, whichever runs the migration).
        version_table="alembic_version",
        version_table_schema="knowledge",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
