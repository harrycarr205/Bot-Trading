import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://127.0.0.1:8787" },
  webServer: {
    command: "cd .. && python -m tradingsystem.dashboard",
    url: "http://127.0.0.1:8787",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
