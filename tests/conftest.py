"""Shared fixtures. `store_factory` is what makes the policy suite run against BOTH stores
(PLAN.md §4.1): SQLite in memory always, and Postgres whenever `ATEZAIN_TEST_DSN` names one.

Without the variable the Postgres arm SKIPS — it does not silently pass. `make mutate` and
`make sabotage` unset it in the child they run, because what they measure is whether a check can
fail, not which database it fails on, and a mutation pass over a network round-trip is minutes of
nothing.

The Postgres arm shares one schema for the whole session and truncates between tests: creating a
schema per test is six round trips to Frankfurt for nothing.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from policy import Store                       # noqa: E402
from records import Records                    # noqa: E402
from policy.store import GENESIS               # noqa: E402

TABLES = "audit, proposals, executions, audit_head, fuse"
RECORD_TABLES = "invoices, notes, emails"


@pytest.fixture(scope="session")
def pg_schema():
    """One schema for the session, dropped at the end. Skips the whole arm when there is no DSN."""
    dsn = os.environ.get("ATEZAIN_TEST_DSN")
    if not dsn:
        pytest.skip("ATEZAIN_TEST_DSN is not set: the Postgres arm is skipped (PLAN.md §4.1)")
    from policy.store_pg import PgStore
    schema = f"atezain_test_{uuid.uuid4().hex[:10]}"
    keeper = PgStore(dsn, schema=schema)       # creates the schema and the tables
    yield dsn, schema
    keeper.drop_schema()
    keeper.close()


@pytest.fixture(params=["sqlite", "postgres"])
def store_factory(request):
    """A callable that returns a fresh, empty store of the parametrised kind."""
    if request.param == "sqlite":
        yield lambda: Store(":memory:")
        return

    dsn, schema = request.getfixturevalue("pg_schema")
    from policy.store_pg import PgStore
    opened: list = []

    def factory():
        store = PgStore(dsn, schema=schema)
        c = store._c()
        c.execute(f"TRUNCATE {TABLES}")
        c.execute("INSERT INTO audit_head (id, seq, hash) VALUES (1, 0, ?) ON CONFLICT (id) DO NOTHING", (GENESIS,))
        c.execute("INSERT INTO fuse (id, tripped) VALUES (1, 0) ON CONFLICT (id) DO NOTHING")
        opened.append(store)
        return store

    yield factory
    for store in opened:
        store.close()


@pytest.fixture(params=["sqlite", "postgres"])
def records_factory(request):
    """The same, for the CUSTOMER's records: a callable returning a fresh, empty record store.
    Shares the session's schema with the policy store — different tables, one namespace, which is
    also the shape `api/app.py` gives a session."""
    if request.param == "sqlite":
        yield lambda: Records(":memory:")
        return

    dsn, schema = request.getfixturevalue("pg_schema")
    from records.store_pg import PgRecords
    opened: list = []

    def factory():
        records = PgRecords(dsn, schema=schema)
        records.conn.execute(f"TRUNCATE {RECORD_TABLES}")
        opened.append(records)
        return records

    yield factory
    for records in opened:
        records.close()
