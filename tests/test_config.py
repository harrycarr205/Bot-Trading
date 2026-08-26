from tradingsystem.config import Settings, load_candidate_universe


def test_load_candidate_universe_reads_real_file():
    universe = load_candidate_universe()
    assert len(universe.tickers) > 0
    assert all(isinstance(t, str) and t.isupper() for t in universe.tickers)
    assert len(universe.tickers) == len(set(universe.tickers))  # no duplicates


def test_discovery_slots_per_cycle_default():
    # _env_file=None isolates this from whatever the real repo .env currently
    # sets DISCOVERY_SLOTS_PER_CYCLE to — that field is explicitly meant to be
    # tuned by the operator, so this test must not break when they use it.
    assert Settings(_env_file=None).discovery_slots_per_cycle == 4
