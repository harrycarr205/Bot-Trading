"""Engine/session factory built from Settings.database_url."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradingsystem.config import Settings


def make_engine(settings: Settings | None = None) -> Engine:
    settings = settings or Settings()
    return create_engine(settings.database_url)


def make_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    engine = engine or make_engine()
    return sessionmaker(bind=engine)
