from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import config

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.files_dir.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{config.db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    return _engine


def init_db() -> None:
    from . import models  # noqa: F401  (registers tables)

    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine: Engine) -> None:
    """Upgrade databases created by older versions in place.

    create_all() never alters existing tables, so columns added to a model
    later are added here. SQLite cannot add a NOT NULL column without a
    default, so new columns are nullable unless they have a scalar default.
    """
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(dialect=engine.dialect)}'
                default = getattr(col.default, "arg", None)
                if default is not None and not callable(default):
                    literal = int(default) if isinstance(default, bool) else default
                    ddl += f" DEFAULT {literal!r}" if isinstance(literal, str) else f" DEFAULT {literal}"
                conn.execute(text(ddl))
                if col.index:
                    conn.execute(text(f'CREATE INDEX IF NOT EXISTS "ix_{table.name}_{col.name}" ON "{table.name}" ("{col.name}")'))


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """For background work outside a request."""
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session
