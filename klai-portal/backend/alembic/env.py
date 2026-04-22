import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import settings
from app.models.base import Base
from app.models.groups import PortalGroup, PortalGroupMembership  # noqa: F401 - registers models
from app.models.portal import PortalOrg, PortalUser  # noqa: F401 - registers models
from app.models.products import PortalUserProduct  # noqa: F401 - registers models

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

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


# After alembic finishes, surface any post_deploy_*.sql files that the
# operator must apply manually as the klai superuser. portal_api (the
# alembic role) cannot CREATE POLICY / CREATE FUNCTION, so policy and
# helper-function changes ship as separate scripts. Forgetting to run
# them was the root cause of the 2026-04-21 RLS production incident.
def _print_post_deploy_warning() -> None:
    import sys
    from pathlib import Path

    versions_dir = Path(__file__).parent / "versions"
    scripts = sorted(p.name for p in versions_dir.glob("post_deploy_*.sql"))
    # The rollback script is operator-on-demand only — never part of the
    # routine deploy.
    scripts = [s for s in scripts if "_rollback_" not in s]
    if not scripts:
        return
    banner = "=" * 72
    msg = [
        "",
        banner,
        "POST-DEPLOY SQL: alembic does NOT run these — apply manually as klai:",
        "",
        *[f"  - alembic/versions/{name}" for name in scripts],
        "",
        "Idempotent helper:",
        "  ./scripts/apply_post_deploy_sql.sh",
        "",
        "Skipping these leaves RLS policies / functions out of sync with code.",
        banner,
        "",
    ]
    sys.stderr.write("\n".join(msg))
    sys.stderr.flush()


_print_post_deploy_warning()
