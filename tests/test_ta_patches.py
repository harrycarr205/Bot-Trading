import pytest
from tradingagents.agents.analysts import sentiment_analyst
from tradingagents.dataflows import fred, stocktwits

from tradingsystem.decision_engine import ta_patches


@pytest.fixture(autouse=True)
def _patched():
    ta_patches.apply_ta_patches()


def test_stocktwits_uses_a_browser_user_agent():
    # Cloudflare 403s the package's default bot UA on every request.
    assert stocktwits._UA.startswith("Mozilla/5.0")


def test_reddit_is_disabled_with_a_placeholder():
    assert sentiment_analyst.fetch_reddit_posts("AAPL") == ta_patches.REDDIT_DISABLED_PLACEHOLDER


@pytest.mark.parametrize("guess, series_id", [
    ("inflation", "CPIAUCSL"),
    ("10y_treasury_yield", "DGS10"),
    ("10-year treasury", "DGS10"),
    ("Oil", "DCOILWTICO"),
    ("jobless_claims", "ICSA"),
    ("high_yield_spread", "BAMLH0A0HYM2"),
    ("cpi", "CPIAUCSL"),  # original aliases still work
])
def test_common_llm_guesses_resolve_to_real_fred_series(guess, series_id):
    assert fred._resolve_series_id(guess) == series_id


def test_unknown_indicator_is_logged_and_passed_through(caplog):
    with caplog.at_level("INFO"):
        assert fred._resolve_series_id("UNKNOWNXYZ") == "UNKNOWNXYZ"
    assert "'UNKNOWNXYZ' is not a known alias" in caplog.text


def test_apply_is_idempotent(caplog):
    ta_patches.apply_ta_patches()
    ta_patches.apply_ta_patches()
    with caplog.at_level("INFO"):
        fred._resolve_series_id("UNKNOWNXYZ")
    assert caplog.text.count("is not a known alias") == 1


def test_sentiment_analyst_uses_mcp_when_token_exists(monkeypatch, tmp_path):
    from tradingsystem.decision_engine import stocktwits_mcp

    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(stocktwits_mcp, "TOKEN_PATH", token)
    monkeypatch.setattr(stocktwits_mcp, "fetch_stocktwits_block", lambda ticker, limit, timeout: f"mcp:{ticker}")

    assert sentiment_analyst.fetch_stocktwits_messages("AAPL", limit=30) == "mcp:AAPL"


def test_sentiment_analyst_falls_back_to_public_endpoint_without_token(monkeypatch, tmp_path):
    from tradingsystem.decision_engine import stocktwits_mcp

    monkeypatch.setattr(stocktwits_mcp, "TOKEN_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(ta_patches, "_original_fetch_stocktwits", lambda ticker, limit, timeout: f"public:{ticker}")

    assert sentiment_analyst.fetch_stocktwits_messages("AAPL", limit=30) == "public:AAPL"
