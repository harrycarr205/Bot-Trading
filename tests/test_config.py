from tradingsystem.config import Settings, load_candidate_universe


def test_load_candidate_universe_reads_real_file():
    universe = load_candidate_universe()
    assert "AAPL" in universe.tickers
    assert "SPY" in universe.tickers
    assert "QQQ" in universe.tickers
    assert len(universe.tickers) >= 30


def test_discovery_slots_per_cycle_default():
    assert Settings().discovery_slots_per_cycle == 4
