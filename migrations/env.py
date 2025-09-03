"""Alembic environment. Online migrations only; DSN comes from the environment.

This is infrastructure/composition code — it is the one place allowed to read a
connection string, because migrations run outside the service layer entirely.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL must be set for migrations")
    # Force the psycopg (v3) driver; SQLAlchemy would otherwise reach for psycopg2.
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    elif dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql+psycopg://", 1)
    return dsn


def run_migrations_online() -> None:
    engine = create_engine(_dsn(), poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported for this app")
run_migrations_online()
