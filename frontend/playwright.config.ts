import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://127.0.0.1:8787" },
  webServer: {
    // Explicit venv interpreter, not bare `python`: on PATH, `python` can
    // resolve to an entirely different install with `tradingsystem` on a
    // different source tree (e.g. the outer repo, not this worktree),
    // silently serving stale code to the tests.
    command: "cd .. && .\\.venv\\Scripts\\python.exe -m tradingsystem.dashboard",
    url: "http://127.0.0.1:8787",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
