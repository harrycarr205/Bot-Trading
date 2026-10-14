import json
import os
import time

import httpx
import pytest

from tradingsystem.decision_engine import stocktwits_mcp
from tradingsystem.decision_engine.stocktwits_mcp import StocktwitsMcpClient, StocktwitsMcpError


def write_token(path, expires_in=3600, refresh="r1"):
    path.write_text(json.dumps({
        "client_id": "cid", "access_token": "a1", "refresh_token": refresh, "expires_at": time.time() + expires_in,
    }))


class FakeServer:
    """Answers the token endpoint and MCP JSON-RPC like mcp.stocktwits.com."""

    def __init__(self, tool_results=None, rotate_refresh=True, sse=False):
        self.tool_results = tool_results or {}
        self.rotate_refresh = rotate_refresh
        self.sse = sse
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/token":
            body = {"access_token": "a2", "expires_in": 3600}
            if self.rotate_refresh:
                body["refresh_token"] = "r2"
            return httpx.Response(200, json=body)
        payload = json.loads(request.content)
        if "id" not in payload:
            return httpx.Response(202)
        if payload["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {}}
        elif payload["method"] == "tools/call":
            name = payload["params"]["name"]
            outcome = self.tool_results[name]
            if isinstance(outcome, Exception):
                result = {"content": [{"type": "text", "text": str(outcome)}], "isError": True}
            else:
                result = {"content": [{"type": "text", "text": json.dumps(outcome)}]}
        else:
            result = {"tools": []}
        message = {"jsonrpc": "2.0", "id": payload["id"], "result": result}
        headers = {"mcp-session-id": "sess-1"}
        if self.sse:
            return httpx.Response(200, text=f"event: message\ndata: {json.dumps(message)}\n\n",
                                  headers={**headers, "content-type": "text/event-stream"})
        return httpx.Response(200, json=message, headers=headers)


def make_client(tmp_path, server, **token_kwargs):
    token_path = tmp_path / "token.json"
    write_token(token_path, **token_kwargs)
    return StocktwitsMcpClient(token_path=token_path, http=httpx.Client(transport=httpx.MockTransport(server))), token_path


def test_call_tool_initializes_session_and_returns_text(tmp_path):
    server = FakeServer({"get_sentiment": {"score": 36}})
    client, _ = make_client(tmp_path, server)

    assert json.loads(client.call_tool("get_sentiment", {"symbol": "AAPL"})) == {"score": 36}
    methods = [json.loads(r.content).get("method") for r in server.requests]
    assert methods == ["initialize", "notifications/initialized", "tools/call"]
    assert server.requests[-1].headers["Mcp-Session-Id"] == "sess-1"
    assert server.requests[-1].headers["Authorization"] == "Bearer a1"


def test_sse_responses_are_parsed(tmp_path):
    client, _ = make_client(tmp_path, FakeServer({"get_sentiment": {"score": 50}}, sse=True))
    assert json.loads(client.call_tool("get_sentiment", {"symbol": "AAPL"})) == {"score": 50}


def test_expired_token_is_refreshed_and_rotated_refresh_token_persisted(tmp_path):
    server = FakeServer({"get_sentiment": {"score": 1}})
    client, token_path = make_client(tmp_path, server, expires_in=-10)

    client.call_tool("get_sentiment", {"symbol": "AAPL"})

    saved = json.loads(token_path.read_text())
    assert saved["access_token"] == "a2"
    assert saved["refresh_token"] == "r2"  # losing a rotated token would lock the bot out
    assert server.requests[-1].headers["Authorization"] == "Bearer a2"


def test_refresh_without_rotation_keeps_previous_refresh_token(tmp_path):
    client, token_path = make_client(tmp_path, FakeServer({"get_sentiment": {}}, rotate_refresh=False), expires_in=-10)
    client.call_tool("get_sentiment", {"symbol": "AAPL"})
    assert json.loads(token_path.read_text())["refresh_token"] == "r1"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_token_file_is_owner_only(tmp_path):
    path = tmp_path / "run" / "token.json"
    stocktwits_mcp.save_token_file(path, {"refresh_token": "secret"})
    assert (path.stat().st_mode & 0o777) == 0o600


def test_tool_error_raises(tmp_path):
    client, _ = make_client(tmp_path, FakeServer({"get_sentiment": ValueError("unknown symbol")}))
    with pytest.raises(StocktwitsMcpError, match="unknown symbol"):
        client.call_tool("get_sentiment", {"symbol": "ZZZZ"})


def test_missing_token_file_explains_how_to_log_in(tmp_path):
    client = StocktwitsMcpClient(token_path=tmp_path / "absent.json")
    with pytest.raises(StocktwitsMcpError, match="stocktwits_login"):
        client.call_tool("get_sentiment", {"symbol": "AAPL"})


def test_fetch_block_formats_all_sections(tmp_path, monkeypatch):
    server = FakeServer({
        "get_sentiment": {"score": 36, "label": "BEARISH", "bullish_pct": 63.3, "bearish_pct": 36.7},
        "get_sentiment_history": {"series": [{"value": 42}, {"value": 30}, {"value": 36}]},
        "get_message_volume": {"series": [
            {"timeframe": "now", "normalized_value": 49, "normalized_label": "NORMAL"},
            {"timeframe": "1W", "normalized_value": 40, "normalized_label": "LOW"},
        ]},
        "get_symbol_messages": {"messages": [
            {"body": "$AAPL it&#39;s going up", "created_at": "2026-10-14T14:00:00Z",
             "sentiment": "Bullish", "user": {"username": "bob"}},
            {"body": "meh", "created_at": "2026-10-14T13:00:00Z", "user": {"username": "amy"}},
        ]},
    })
    client, _ = make_client(tmp_path, server)
    monkeypatch.setattr(stocktwits_mcp, "_client", client)

    block = stocktwits_mcp.fetch_stocktwits_block("AAPL")

    assert "Sentiment score: 36/100 (BEARISH)" in block
    assert "1-week sentiment trend: 42 → 36 (range 30–42)" in block
    assert "now 49/100 (NORMAL) · 1W 40/100 (LOW)" in block
    assert "Bullish: 1 (50%)" in block
    assert "[2026-10-14T14:00:00Z · @bob · Bullish] $AAPL it's going up" in block


def test_fetch_block_degrades_to_placeholder_and_never_raises(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, FakeServer({"get_sentiment": ValueError("boom")}))
    monkeypatch.setattr(stocktwits_mcp, "_client", client)

    assert stocktwits_mcp.fetch_stocktwits_block("AAPL") == "<stocktwits unavailable: StocktwitsMcpError>"
