"""Alembic environment configuration for NewsBot."""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Import the DeclarativeBase so autogenerate can detect models
from db.models import Base  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url_for_migrations() -> str:
    """Resolve DB URL, honoring `alembic -x sqlalchemy.url=...`."""
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("sqlalchemy.url"):
        return x_args["sqlalchemy.url"]
    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without DB connection)."""
    url = _url_for_migrations()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (with a live DB connection)."""
    section = dict(config.get_section(config.config_ini_section, {}) or {})
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("sqlalchemy.url"):
        section["sqlalchemy.url"] = x_args["sqlalchemy.url"]
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
