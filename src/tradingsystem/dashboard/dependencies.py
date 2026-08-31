"""Shared FastAPI dependencies for dashboard routers — separated from app.py
so route modules can depend on these without importing app.py itself
(avoids a circular import between app.py and routes/*.py).
"""

from __future__ import annotations

import urllib.parse

from fastapi import HTTPException
from fastapi.requests import Request

from tradingsystem.config import Settings
from tradingsystem.db.session import make_session_factory
from tradingsystem.execution.alpaca_client import AlpacaClient, AlpacaClientProtocol

_session_factory = make_session_factory()


def get_db():
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def get_alpaca_client() -> AlpacaClientProtocol:
    return AlpacaClient(Settings())


# This dashboard is documented and designed as localhost-only (Settings.dashboard_host
# defaults to "127.0.0.1") — pinned here rather than derived from the request's own Host
# header, which uvicorn does not validate and a DNS-rebinding attacker could spoof.
_ALLOWED_ORIGIN_HOSTS = {"127.0.0.1", "localhost"}


def require_same_origin(request: Request) -> None:
    """Rejects cross-origin POSTs to the write routes (CSRF guard)."""
    origin = request.headers.get("origin")
    if origin is None:
        return
    origin_host = urllib.parse.urlsplit(origin).hostname
    if origin_host not in _ALLOWED_ORIGIN_HOSTS:
        raise HTTPException(status_code=403, detail="Cross-origin request rejected")
