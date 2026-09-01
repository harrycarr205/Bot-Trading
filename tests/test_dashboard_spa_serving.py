"""Tests for SPA static serving and fallback routing."""

from __future__ import annotations


def test_spa_fallback_serves_index_html_for_unknown_frontend_route(client, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>placeholder shell</html>")

    from tradingsystem.dashboard import app as app_module
    monkeypatch.setattr(app_module, "_FRONTEND_DIST", dist)

    response = client.get("/positions")

    assert response.status_code == 200
    assert "placeholder shell" in response.text


def test_api_routes_are_not_swallowed_by_spa_fallback(client, db_session):
    response = client.get("/api/overview")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
