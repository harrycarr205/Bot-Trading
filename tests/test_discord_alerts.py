import httpx
import pytest

from tradingsystem.config import Settings
from tradingsystem.orchestration import discord_alerts


class _CapturingPost:
    def __init__(self, raise_error=None):
        self.calls = []
        self.raise_error = raise_error

    def __call__(self, url, json=None, timeout=None):
        self.calls.append((url, json, timeout))
        if self.raise_error is not None:
            raise self.raise_error
        return httpx.Response(204, request=httpx.Request("POST", url))


def test_send_alert_posts_to_configured_webhook(monkeypatch):
    fake_post = _CapturingPost()
    monkeypatch.setattr(discord_alerts.httpx, "post", fake_post)
    settings = Settings(discord_webhook_url="https://discord.example/webhook")

    discord_alerts.send_alert(settings, "order placed", level="info")

    assert len(fake_post.calls) == 1
    url, payload, _ = fake_post.calls[0]
    assert url == "https://discord.example/webhook"
    assert "[INFO] order placed" in payload["content"]


def test_send_alert_noop_when_webhook_not_configured(monkeypatch):
    fake_post = _CapturingPost()
    monkeypatch.setattr(discord_alerts.httpx, "post", fake_post)
    settings = Settings(discord_webhook_url="")

    discord_alerts.send_alert(settings, "should not send", level="critical")

    assert fake_post.calls == []


def test_send_alert_swallows_http_errors(monkeypatch):
    fake_post = _CapturingPost(raise_error=httpx.ConnectError("refused"))
    monkeypatch.setattr(discord_alerts.httpx, "post", fake_post)
    settings = Settings(discord_webhook_url="https://discord.example/webhook")

    discord_alerts.send_alert(settings, "network is down", level="critical")  # must not raise

    assert len(fake_post.calls) == 1
