"""
db/connection.py — PostgreSQL connection pool for TraceIQ.

Provides a single SQLAlchemy engine and a session factory used by
every module that needs database access.  Connection parameters come
exclusively from the DATABASE_URL environment variable — never
hardcoded — per CLAUDE.md §4 and 16_DEPLOYMENT_AND_TELEMETRY.md §3.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

load_dotenv()  # loads .env when running locally

_DATABASE_URL: str = os.environ["DATABASE_URL"]

# Pool settings appropriate for a single-process prototype.
engine = create_engine(
    _DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,  # re-validates stale connections automatically
    echo=False,          # set True for SQL debug logging during development
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Yield a database session, rolling back on error and closing on exit."""
    db: Session = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ping() -> bool:
    """Return True if the database is reachable, False otherwise."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
