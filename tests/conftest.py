"""Shared pytest fixtures.

db_session opens a real transaction against the dedicated trading_test
database (Settings.test_database_url) — not the "trading" dev database the
live orchestration scheduler writes real data to — and rolls it back on
teardown, so tests exercise real Order/Fill ORM writes without leaving
residue, and without being polluted by (or polluting) the scheduler's data.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tradingsystem.config import Settings
from tradingsystem.dashboard.app import app
from tradingsystem.dashboard.dependencies import get_db
from tradingsystem.db.models import Base


@pytest.fixture(scope="session", autouse=True)
def _test_database_schema():
    """Ensure trading_test's schema exists before any test runs.

    Safe to run every session: CREATE EXTENSION IF NOT EXISTS and
    create_all() are both idempotent, and this only ever touches
    Settings().test_database_url — never the dev database.
    """
    engine = create_engine(Settings().test_database_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    engine.dispose()


@pytest.fixture
def db_session():
    engine = create_engine(Settings().test_database_url)
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection)
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
