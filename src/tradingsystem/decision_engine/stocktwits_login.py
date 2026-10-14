"""One-time StockTwits MCP login: creates the token file the bot refreshes from.

Run on a machine with a browser (your PC), then copy the token file to the
droplet:

    python -m tradingsystem.decision_engine.stocktwits_login
    scp run/stocktwits_token.json deploy@<droplet>:~/Bot-Trading/run/

Registers an OAuth client (dynamic client registration), opens StockTwits'
consent page, catches the redirect on a localhost port, exchanges the code
(PKCE) for tokens, writes them, then lists the server's tools as a check.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import httpx

from tradingsystem.decision_engine.stocktwits_mcp import (
    AUTH_BASE, MCP_URL, TOKEN_PATH, StocktwitsMcpClient, save_token_file, token_payload,
)

_PORT = 8765
_REDIRECT_URI = f"http://127.0.0.1:{_PORT}/callback"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _wait_for_code(expected_state: str) -> str:
    received: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            received.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("StockTwits authorized — you can close this tab.".encode())
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", _PORT), Handler)
    server.serve_forever()
    if received.get("state") != expected_state:
        sys.exit("state mismatch in OAuth callback — aborting")
    if "code" not in received:
        sys.exit(f"authorization failed: {received}")
    return received["code"]


def main() -> None:
    with httpx.Client(timeout=30) as http:
        registration = http.post(f"{AUTH_BASE}/register", json={
            "client_name": "Bot-Trading (personal paper-trading bot)",
            "redirect_uris": [_REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "read",
        })
        registration.raise_for_status()
        client_id = registration.json()["client_id"]

        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)
        authorize_url = f"{AUTH_BASE}/authorize?" + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": _REDIRECT_URI,
            "scope": "read",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": MCP_URL,
        })
        print(f"Opening StockTwits login in your browser. If it doesn't open, visit:\n{authorize_url}\n")
        webbrowser.open(authorize_url)
        code = _wait_for_code(state)

        token_response = http.post(f"{AUTH_BASE}/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": MCP_URL,
        })
        token_response.raise_for_status()
        save_token_file(TOKEN_PATH, token_payload(client_id, token_response.json()))
    print(f"Saved token to {TOKEN_PATH}\n")

    tools = StocktwitsMcpClient().list_tools()
    print(f"{len(tools)} StockTwits MCP tools available:")
    for tool in tools:
        print(f"\n- {tool['name']}: {tool.get('description', '').strip()}")
        print(f"  input schema: {json.dumps(tool.get('inputSchema', {}))}")


if __name__ == "__main__":
    main()
