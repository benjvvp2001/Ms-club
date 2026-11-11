"""Shared helpers for the tests on real PostgreSQL (CLUBS_TEST_DATABASE_URL, the isolated qa_tests database).

Each test builds its own pair of schemas (<name> and <name>_api, the latter for clubs_api.notification_outbox of
migration 004) and drops them at the end, so nothing is left behind. The migration files name `public.` and
`clubs_api` explicitly; they are applied with those names replaced by the test schemas."""
from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg_pool import ConnectionPool

DB = Path(__file__).parents[1] / "db"
MIGRATIONS = sorted((DB / "migrations").glob("*.sql"))


def apply(conn, schema: str, path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\bclubs_api\b", f"{schema}_api", text)
    text = re.sub(r"\bpublic\.", f"{schema}.", text)
    conn.execute(text)


@pytest.fixture
def base():
    dsn = os.environ.get("CLUBS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set CLUBS_TEST_DATABASE_URL for isolated PostgreSQL tests")
    schema = "planes_test_" + uuid4().hex[:16]
    with psycopg.connect(dsn, autocommit=True) as setup:
        for name in (schema, f"{schema}_api"):
            setup.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        try:
            setup.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            yield setup, schema, dsn
        finally:
            setup.execute("RESET search_path")
            for name in (schema, f"{schema}_api"):
                setup.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def build(setup, schema, upto: str | None = None) -> None:
    apply(setup, schema, DB / "00_base_schema.sql")
    for path in MIGRATIONS:
        if upto is not None and path.name > upto:
            break
        apply(setup, schema, path)


def pool_for(dsn: str, schema: str) -> ConnectionPool:
    pool = ConnectionPool(dsn, kwargs={"options": f"-c search_path={schema}"}, min_size=1, max_size=4, open=True)
    pool.wait()
    return pool
