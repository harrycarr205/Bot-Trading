"""Create the second-brain schema against the configured Postgres database.

Run with: python -m tradingsystem.db.init_db

Uses Base.metadata.create_all() rather than a migration tool (Alembic) — see
ARCHITECTURE.md-adjacent implementation plan note: single-operator system, schema
is stable per the signed-off architecture, revisit if/when it needs to evolve
under real data.
"""

from __future__ import annotations

from sqlalchemy import text

from tradingsystem.db.models import Base
from tradingsystem.db.session import make_engine


def init_db() -> None:
    engine = make_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


if __name__ == "__main__":
    init_db()
    print("Second-brain schema created.")
