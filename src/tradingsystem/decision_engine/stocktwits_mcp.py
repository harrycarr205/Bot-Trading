"""Minimal synchronous client for StockTwits' official remote MCP server.

https://ai.stocktwits.com/ — `https://mcp.stocktwits.com/mcp`, OAuth 2.1
(dynamic client registration, PKCE, public client, refresh tokens). Replaces
the public api.stocktwits.com stream endpoint, which Cloudflare blocks from
the droplet's datacenter IP (2026-10-14).

MCP's streamable-HTTP transport is plain JSON-RPC over POST (responses come
back as JSON or a short SSE stream), so this speaks it directly with httpx
rather than pulling in the async `mcp` SDK for a synchronous pipeline.

Credentials live in a JSON token file (see stocktwits_login.py, which creates
it), not .env: the server may rotate the refresh token on every refresh, and
the bot has to persist the new one or lose access.
"""

from __future__ import annotations

import html
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from tradingsystem.config import REPO_ROOT

log = logging.getLogger(__name__)

MCP_URL = "https://mcp.stocktwits.com/mcp"
AUTH_BASE = "https://mcp.stocktwits.com"
TOKEN_PATH = REPO_ROOT / "run" / "stocktwits_token.json"
PROTOCOL_VERSION = "2025-06-18"
_REFRESH_MARGIN_SECONDS = 60


class StocktwitsMcpError(RuntimeError):
    pass


def save_token_file(path: Path, data: dict[str, Any]) -> None:
    """Atomic write, owner-only (0600): the refresh token grants account access."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    if hasattr(os, "fchmod"):  # POSIX only; tightens a pre-existing looser tmp file
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def token_payload(client_id: str, token_response: dict[str, Any], previous_refresh: str | None = None) -> dict[str, Any]:
    return {
        "client_id": client_id,
        "access_token": token_response["access_token"],
        # Servers that don't rotate omit refresh_token from refresh responses.
        "refresh_token": token_response.get("refresh_token") or previous_refresh,
        "expires_at": time.time() + float(token_response.get("expires_in", 3600)),
    }


def _parse_jsonrpc_response(response: httpx.Response, request_id: int) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                message = json.loads(line[5:].strip())
                if message.get("id") == request_id:
                    return message
        raise StocktwitsMcpError(f"no JSON-RPC response with id {request_id} in SSE stream")
    return response.json()


class StocktwitsMcpClient:
    def __init__(self, token_path: Path = TOKEN_PATH, http: httpx.Client | None = None, timeout: float = 20.0):
        self._token_path = token_path
        self._http = http or httpx.Client(timeout=timeout)
        self._session_id: str | None = None
        self._next_id = 1

    # --- auth ---------------------------------------------------------------

    def _load_token(self) -> dict[str, Any]:
        if not self._token_path.exists():
            raise StocktwitsMcpError(
                f"no StockTwits token at {self._token_path} — run "
                "`python -m tradingsystem.decision_engine.stocktwits_login` once"
            )
        return json.loads(self._token_path.read_text())

    def _access_token(self) -> str:
        token = self._load_token()
        if token.get("expires_at", 0) - _REFRESH_MARGIN_SECONDS > time.time():
            return token["access_token"]
        response = self._http.post(f"{AUTH_BASE}/token", data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": token["client_id"],
            "resource": MCP_URL,
        })
        if response.status_code != 200:
            raise StocktwitsMcpError(f"token refresh failed: HTTP {response.status_code} {response.text[:200]}")
        refreshed = token_payload(token["client_id"], response.json(), previous_refresh=token["refresh_token"])
        save_token_file(self._token_path, refreshed)
        return refreshed["access_token"]

    # --- JSON-RPC -------------------------------------------------------------

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        response = self._http.post(MCP_URL, json=payload, headers=headers)
        if response.status_code >= 400:
            raise StocktwitsMcpError(f"MCP {payload.get('method')} failed: HTTP {response.status_code} {response.text[:200]}")
        return response

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._session_id is None and method != "initialize":
            self._initialize()
        request_id = self._next_id
        self._next_id += 1
        response = self._post({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        message = _parse_jsonrpc_response(response, request_id)
        if "error" in message:
            raise StocktwitsMcpError(f"MCP {method} error: {message['error']}")
        return message["result"]

    def _initialize(self) -> None:
        request_id = self._next_id
        self._next_id += 1
        response = self._post({
            "jsonrpc": "2.0", "id": request_id, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "bot-trading", "version": "1.0"},
            },
        })
        _parse_jsonrpc_response(response, request_id)
        self._session_id = response.headers.get("mcp-session-id") or ""
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[dict[str, Any]]:
        return self._request("tools/list")["tools"]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Returns the tool's text content blocks joined; raises on tool error."""
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        text = "\n".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")
        if result.get("isError"):
            raise StocktwitsMcpError(f"tool {name} returned an error: {text[:200]}")
        return text


# --- sentiment-analyst adapter ------------------------------------------------
# Replaces tradingagents' fetch_stocktwits_messages (see ta_patches.py). Same
# contract: always returns a prompt-ready string, never raises.

_MAX_BODY_CHARS = 280
_client: StocktwitsMcpClient | None = None


def _get_client() -> StocktwitsMcpClient:
    global _client
    if _client is None:
        _client = StocktwitsMcpClient()
    return _client


def _call_json(client: StocktwitsMcpClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return json.loads(client.call_tool(name, arguments))


def format_sentiment(sentiment: dict[str, Any], history: dict[str, Any]) -> str:
    line = (
        f"Sentiment score: {sentiment.get('score')}/100 ({sentiment.get('label')}) · "
        f"tagged messages {sentiment.get('bullish_pct', 0):.0f}% bullish / {sentiment.get('bearish_pct', 0):.0f}% bearish"
    )
    values = [point["value"] for point in history.get("series", []) if point.get("value") is not None]
    if values:
        line += f"\n1-week sentiment trend: {values[0]} → {values[-1]} (range {min(values)}–{max(values)})"
    return line


def format_volume(volume: dict[str, Any]) -> str:
    by_timeframe = {point.get("timeframe"): point for point in volume.get("series", [])}
    parts = [
        f"{tf} {by_timeframe[tf].get('normalized_value')}/100 ({by_timeframe[tf].get('normalized_label')})"
        for tf in ("now", "1W", "1M") if tf in by_timeframe
    ]
    return "Message volume: " + " · ".join(parts) if parts else "Message volume: unavailable"


def format_messages(messages: list[dict[str, Any]], ticker: str) -> str:
    if not messages:
        return f"<no StockTwits messages found for ${ticker.upper()}>"
    lines = []
    counts = {"Bullish": 0, "Bearish": 0, "no-label": 0}
    for message in messages:
        tag = message.get("sentiment") if message.get("sentiment") in ("Bullish", "Bearish") else "no-label"
        counts[tag] += 1
        body = " ".join(html.unescape(message.get("body") or "").split())
        if len(body) > _MAX_BODY_CHARS:
            body = body[:_MAX_BODY_CHARS] + "…"
        user = (message.get("user") or {}).get("username", "?")
        lines.append(f"[{message.get('created_at', '')} · @{user} · {tag}] {body}")
    total = len(messages)
    summary = (
        f"Recent messages — Bullish: {counts['Bullish']} ({round(100 * counts['Bullish'] / total)}%) · "
        f"Bearish: {counts['Bearish']} ({round(100 * counts['Bearish'] / total)}%) · "
        f"Unlabeled: {counts['no-label']} · Total: {total}"
    )
    return summary + "\n\n" + "\n".join(lines)


def fetch_stocktwits_block(ticker: str, limit: int = 30, timeout: float = 10.0) -> str:
    from tradingagents.dataflows.stocktwits import _stocktwits_symbol  # crypto -> BTC.X convention

    symbol = _stocktwits_symbol(ticker)
    for attempt in (1, 2):
        try:
            client = _get_client()
            sentiment = _call_json(client, "get_sentiment", {"symbol": symbol})
            history = _call_json(client, "get_sentiment_history", {"symbol": symbol, "zoom": "1W"})
            volume = _call_json(client, "get_message_volume", {"symbol": symbol})
            messages = _call_json(client, "get_symbol_messages", {"symbol": symbol, "limit": min(limit, 50)})
            return "\n".join([
                format_sentiment(sentiment, history),
                format_volume(volume),
                "",
                format_messages(messages.get("messages", []), ticker),
            ])
        except Exception as exc:  # noqa: BLE001 - sentiment input must never block a research run
            if attempt == 1 and _client is not None:
                _client._session_id = None  # MCP sessions can expire; re-initialize once
                continue
            log.warning("StockTwits MCP fetch failed for %s: %s", ticker, exc)
            return f"<stocktwits unavailable: {type(exc).__name__}>"
    raise AssertionError("unreachable")
