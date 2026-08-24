"""Shared pytest fixtures.

db_session opens a real transaction against the already-running trading_postgres
container and rolls it back on teardown, so tests exercise real Order/Fill ORM
writes without leaving residue in the dev database.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tradingsystem.db.session import make_engine


@pytest.fixture
def db_session():
    engine = make_engine()
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
