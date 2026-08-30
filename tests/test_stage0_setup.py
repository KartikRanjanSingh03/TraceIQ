"""
tests/test_stage0_setup.py — Stage 0 required test (17_ROADMAP.md §2 Stage 0).

Test: DB connects, empty schema created successfully.

Passes when:
  1. ping() returns True  (DB is reachable)
  2. The three target schemas (raw, kpi, pipeline) exist in Postgres
  3. No application tables exist yet (Stage 1 creates them — we verify Stage 0 is clean)

Run with:
    pytest tests/test_stage0_setup.py -v
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from db.connection import engine, ping


def test_db_ping() -> None:
    """DB is reachable via the connection pool."""
    assert ping() is True, (
        "Database ping failed. "
        "Ensure Postgres is running: docker compose up -d  "
        "and DATABASE_URL in .env matches docker-compose.yml."
    )


def test_schemas_created() -> None:
    """The three target schemas (raw, kpi, pipeline) exist after init migration."""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name IN ('raw', 'kpi', 'pipeline') "
                "ORDER BY schema_name;"
            )
        )
        found = {row[0] for row in result}

    assert found == {"raw", "kpi", "pipeline"}, (
        f"Expected schemas {{raw, kpi, pipeline}}, found: {found}. "
        "Run the init migration: psql $DATABASE_URL -f db/migrations/000_create_schemas.sql"
    )


@pytest.mark.skip(
    reason=(
        "Stale post-Stage-1: this assertion is only valid before Stage 1 migrations run. "
        "Stages 1–15 are now complete; the 14 tables present in raw/kpi/pipeline schemas "
        "are correct and expected. Skipping to keep the full suite green."
    )
)
def test_no_application_tables_yet() -> None:
    """Stage 0 ends with schemas only — no application tables should exist yet.

    This guards against accidentally running Stage 1 migrations before Stage 0
    is confirmed complete, per CLAUDE.md §3 build-order discipline.
    """
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema IN ('raw', 'kpi', 'pipeline');"
            )
        )
        table_count: int = result.scalar()

    assert table_count == 0, (
        f"Expected 0 application tables in Stage 0, found {table_count}. "
        "Stage 1 migrations must not run until Stage 0 is confirmed complete."
    )
