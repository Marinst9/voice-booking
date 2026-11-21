"""
Alembic setup: tells Alembic which database to change and what our tables look like.

Usage (from the project folder):
    alembic upgrade head                        # apply all migrations
    alembic revision --autogenerate -m "..."    # create a new migration after changing models
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.calendars.postgres import Base
from app.config import Settings

config = context.config

# Use the logging setup from alembic.ini, unless a caller (like the tests) asked us not to.
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The tables Alembic compares against when autogenerating migrations.
target_metadata = Base.metadata


def get_url() -> str:
    # 1. A URL passed in by code (the tests do this), otherwise 2. DATABASE_URL from env / .env
    url = config.get_main_option("sqlalchemy.url") or Settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set, so Alembic doesn't know which database to migrate.")
    return url


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    context.configure(url=get_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and apply the migrations."""
    engine = create_engine(get_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
