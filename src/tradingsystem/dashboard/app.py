"""FastAPI dashboard app over the second-brain database — JSON API under /api/*
plus static serving of the built React frontend (see
docs/superpowers/specs/2026-08-31-dashboard-redesign-design.md).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from tradingsystem.config import REPO_ROOT
from tradingsystem.dashboard.routes import config as config_routes
from tradingsystem.dashboard.routes import control, decisions, orders, overview, pnl, positions, tickers

app = FastAPI(title="Bot-Trading Dashboard")

app.include_router(overview.router, prefix="/api")
app.include_router(positions.router, prefix="/api")
app.include_router(tickers.router, prefix="/api")
app.include_router(decisions.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(pnl.router, prefix="/api")
app.include_router(control.router, prefix="/api")
app.include_router(config_routes.router, prefix="/api")

_FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

if (_FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="frontend-assets")


@app.get("/{full_path:path}")
def spa_fallback(full_path: str) -> FileResponse:
    return FileResponse(str(_FRONTEND_DIST / "index.html"))
