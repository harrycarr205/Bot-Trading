"""Minimal Discord webhook sender — ARCHITECTURE.md §7.

Alerts immediately on: circuit breaker trips, order rejections/API errors, every
trade placed, and unhandled per-ticker cycle errors. Never raises — a Discord/network
failure must not take down the trading loop that's trying to report through it.
"""

from __future__ import annotations

import logging

import httpx

from tradingsystem.config import Settings

log = logging.getLogger(__name__)


def send_alert(settings: Settings, message: str, level: str = "info") -> None:
    if not settings.discord_webhook_url:
        log.info("[discord alert suppressed, no webhook configured] [%s] %s", level.upper(), message)
        return
    try:
        httpx.post(settings.discord_webhook_url, json={"content": f"[{level.upper()}] {message}"}, timeout=10)
    except httpx.HTTPError as exc:
        log.warning("failed to send Discord alert: %s", exc)
