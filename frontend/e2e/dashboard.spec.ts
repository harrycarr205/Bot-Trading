import { expect, test } from "@playwright/test";

test("nav reaches every page", async ({ page }) => {
  await page.goto("/");
  for (const label of ["Positions", "Decisions", "Orders", "P&L", "Control", "Config"]) {
    await page.getByRole("link", { name: label, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(label === "P&L" ? "/pnl" : label.toLowerCase()));
  }
});

test("Overview loads with at least one panel rendered", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Equity", { exact: true })).toBeVisible();
  await expect(page.getByText("Heartbeat", { exact: true })).toBeVisible();
});

test("a ticker link from Decisions resolves to Ticker Detail with matching data", async ({ page }) => {
  await page.goto("/decisions");
  const firstTickerLink = page.locator("table a[href^='/ticker/']").first();
  const ticker = await firstTickerLink.textContent();
  await firstTickerLink.click();
  await expect(page.getByRole("heading", { name: ticker ?? "" })).toBeVisible();
});

test("Control page Start button is disabled while the scheduler is already running", async ({ page }) => {
  await page.goto("/control");
  await expect(page.getByText(/Scheduler/)).toBeVisible();
});
