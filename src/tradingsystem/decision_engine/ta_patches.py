"""Runtime fixes to the pinned tradingagents package's data vendors.

Same approach as friction_context.py: adjust tradingagents from our side
rather than forking the pinned release. All three come from the 2026-10-14
droplet check-in, where these sources failed on almost every research run:

- StockTwits: the package's public stream endpoint is Cloudflare-blocked from
  the droplet's datacenter IP. When a StockTwits MCP token exists
  (stocktwits_login.py), the sentiment analyst gets StockTwits' official MCP
  data instead (sentiment score + trend, message volume, recent posts);
  otherwise it falls back to the public endpoint with a browser User-Agent,
  which works from residential IPs.
- Reddit: rate-limited (429) on nearly every request from the droplet, each
  failure costing a 5s backoff. Disabled outright — the sentiment analyst is
  prompted to treat an unavailable source as such.
- FRED: the macro tool only knows ~30 aliases, and the LLM's natural guesses
  ("inflation", "10y_treasury_yield", "oil", "jobless_claims", ...) went
  straight to FRED as raw series IDs and 400'd. Extra aliases cover them, and
  any non-alias request is logged so remaining misses can be traced.

apply_ta_patches() is idempotent; runner.run_research calls it before every run.
"""

from __future__ import annotations

import logging

from tradingagents.agents.analysts import sentiment_analyst
from tradingagents.dataflows import fred, stocktwits

from tradingsystem.decision_engine import stocktwits_mcp

log = logging.getLogger(__name__)

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

REDDIT_DISABLED_PLACEHOLDER = "<reddit unavailable: source disabled (persistently rate-limited)>"

# Each ID verified against the live FRED API on 2026-10-14. Keys are in the
# normalized form fred._resolve_series_id looks up (lowercase, underscores).
EXTRA_FRED_ALIASES = {
    # Inflation
    "inflation": "CPIAUCSL",
    "inflation_rate": "CPIAUCSL",
    "headline_inflation": "CPIAUCSL",
    "core_inflation": "CPILFESL",
    "pce_inflation": "PCEPI",
    "core_pce_inflation": "PCEPILFE",
    "ppi": "PPIFIS",
    "producer_prices": "PPIFIS",
    "10y_breakeven": "T10YIE",
    "breakeven_inflation": "T10YIE",
    # Rates & yields
    "interest_rate": "FEDFUNDS",
    "interest_rates": "FEDFUNDS",
    "policy_rate": "FEDFUNDS",
    "fed_rate": "FEDFUNDS",
    "effective_fed_funds_rate": "DFF",
    "3m_treasury": "DGS3MO",
    "3_month_treasury": "DGS3MO",
    "2y": "DGS2",
    "2y_treasury_yield": "DGS2",
    "2_year_treasury": "DGS2",
    "2_year_treasury_yield": "DGS2",
    "5y_treasury": "DGS5",
    "5y_treasury_yield": "DGS5",
    "10y": "DGS10",
    "10y_yield": "DGS10",
    "10y_treasury_yield": "DGS10",
    "10_year_treasury": "DGS10",
    "10_year_treasury_yield": "DGS10",
    "treasury_10y": "DGS10",
    "30y_treasury_yield": "DGS30",
    "30_year_treasury": "DGS30",
    "2y_10y_spread": "T10Y2Y",
    "10y2y_spread": "T10Y2Y",
    "yield_curve_10y2y": "T10Y2Y",
    "yield_spread": "T10Y2Y",
    "10y_3m_spread": "T10Y3M",
    "mortgage_rate": "MORTGAGE30US",
    "mortgage_rates": "MORTGAGE30US",
    "30y_mortgage_rate": "MORTGAGE30US",
    # Credit
    "credit_spread": "BAMLH0A0HYM2",
    "credit_spreads": "BAMLH0A0HYM2",
    "corporate_spreads": "BAMLH0A0HYM2",
    "high_yield_spread": "BAMLH0A0HYM2",
    "hy_spread": "BAMLH0A0HYM2",
    "investment_grade_spread": "BAMLC0A0CM",
    "ig_spread": "BAMLC0A0CM",
    "financial_conditions": "NFCI",
    # Growth
    "gdp_growth": "A191RL1Q225SBEA",
    "real_gdp_growth": "A191RL1Q225SBEA",
    "recession_probability": "RECPROUSM156N",
    # Labor
    "jobless_claims": "ICSA",
    "initial_jobless_claims": "ICSA",
    "weekly_jobless_claims": "ICSA",
    "continuing_claims": "CCSA",
    "nfp": "PAYEMS",
    "non_farm_payrolls": "PAYEMS",
    "nonfarm_payroll": "PAYEMS",
    "job_openings": "JTSJOL",
    "jolts": "JTSJOL",
    "wage_growth": "CES0500000003",
    "average_hourly_earnings": "CES0500000003",
    # Markets & commodities
    "oil": "DCOILWTICO",
    "oil_price": "DCOILWTICO",
    "crude_oil": "DCOILWTICO",
    "wti": "DCOILWTICO",
    "wti_crude": "DCOILWTICO",
    "brent": "DCOILBRENTEU",
    "brent_crude": "DCOILBRENTEU",
    "dxy": "DTWEXBGS",
    "usd_index": "DTWEXBGS",
    "us_dollar_index": "DTWEXBGS",
    # Sentiment & housing
    "consumer_confidence": "UMCSENT",
    "housing": "HOUST",
    "home_prices": "CSUSHPINSA",
    "case_shiller": "CSUSHPINSA",
}


_original_fetch_stocktwits = stocktwits.fetch_stocktwits_messages


def _fetch_stocktwits(ticker: str, limit: int = 30, timeout: float = 10.0) -> str:
    # Checked per call so a token copied onto the droplet is picked up without a restart.
    if stocktwits_mcp.TOKEN_PATH.exists():
        return stocktwits_mcp.fetch_stocktwits_block(ticker, limit=limit, timeout=timeout)
    return _original_fetch_stocktwits(ticker, limit=limit, timeout=timeout)


def _disabled_reddit(ticker: str, *args, **kwargs) -> str:
    return REDDIT_DISABLED_PLACEHOLDER


def _logging_resolve_series_id(original):
    def resolve(indicator: str) -> str:
        key = indicator.strip().lower().replace(" ", "_").replace("-", "_")
        if key not in fred.MACRO_SERIES:
            log.info("FRED indicator %r is not a known alias — passing to FRED as a raw series ID", indicator)
        return original(indicator)

    resolve._ta_patched = True
    return resolve


def apply_ta_patches() -> None:
    stocktwits._UA = BROWSER_USER_AGENT
    sentiment_analyst.fetch_stocktwits_messages = _fetch_stocktwits
    sentiment_analyst.fetch_reddit_posts = _disabled_reddit
    fred.MACRO_SERIES.update(EXTRA_FRED_ALIASES)
    if not getattr(fred._resolve_series_id, "_ta_patched", False):
        fred._resolve_series_id = _logging_resolve_series_id(fred._resolve_series_id)
