-- Runs automatically on first container init (fresh volume only — see
-- docker-compose.yml's docker-entrypoint-initdb.d mount). Creates the
-- dedicated test database tests/conftest.py's db_session fixture uses,
-- kept separate from the "trading" dev database the live orchestration
-- scheduler writes real data to.
CREATE DATABASE trading_test OWNER trading;
