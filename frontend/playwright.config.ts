import { defineConfig } from "@playwright/test";

// Matches Settings.test_database_url in src/tradingsystem/config.py and the
// connection string tests/conftest.py already uses: same host and credentials
// as the dev database, a dedicated `trading_test` database name. Kept as a
// literal rather than read from Settings because Playwright's config is
// evaluated by node, not python.
const TEST_DATABASE_URL = "postgresql+psycopg://trading:trading@localhost:5432/trading_test";

// A port of its own, so an E2E run never silently attaches to (or collides
// with) an operator's live dashboard already serving the real `trading`
// database on 8787.
const E2E_PORT = 8788;

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: `http://127.0.0.1:${E2E_PORT}` },
  webServer: {
    // Explicit venv interpreter, not bare `python`: on PATH, `python` can
    // resolve to an entirely different install with `tradingsystem` on a
    // different source tree (e.g. the outer repo, not this worktree),
    // silently serving stale code to the tests.
    command: "cd .. && .\\.venv\\Scripts\\python.exe -m tradingsystem.dashboard",
    url: `http://127.0.0.1:${E2E_PORT}`,
    // DATABASE_URL is read at import time by dashboard/dependencies.py's module-level
    // session factory, so setting it here is enough to point every route at the test
    // database. Without it the suite runs against the same `trading` database the live
    // production scheduler writes to — harmless while all tests are read-only, but no
    // barrier at all against a future mutating test touching real trading state.
    // pydantic-settings gives a real env var precedence over the repo's .env file.
    env: {
      DATABASE_URL: TEST_DATABASE_URL,
      DASHBOARD_PORT: String(E2E_PORT),
    },
    // Deliberately false: reusing an already-running server would silently discard
    // the env overrides above, which is precisely the isolation this exists to give.
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
