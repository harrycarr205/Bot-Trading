import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Config from "./Config";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { config: vi.fn(), saveCandidateUniverse: vi.fn(), saveRiskConfig: vi.fn(), saveEnvSettings: vi.fn() },
}));

const CONFIG = {
  tickers: ["AAPL"],
  risk_config: { max_position_pct: 0.1, cash_reserve_pct: 0.2, stop_loss_pct: 0.08, daily_drawdown_breaker_pct: 0.03, weekly_drawdown_breaker_pct: 0.08, stale_data_max_age_minutes: 15 },
  env_values: {
    discovery_slots_per_cycle: "2",
    watchdog_check_interval_minutes: "20",
    pre_market_cron: "35 9 * * 1-5",
    midday_cron: "30 12 * * 1-5",
    tradingagents_deep_think_model: "gpt-oss:120b-cloud",
    tradingagents_quick_think_model: "qwen2.5:7b-instruct",
  },
};

// Card order on the page: candidate universe, risk config, env settings.
const CANDIDATES_SAVE = 0;
const RISK_SAVE = 1;
const ENV_SAVE = 2;

describe("Config page", () => {
  beforeEach(() => {
    vi.mocked(api.config).mockResolvedValue(CONFIG);
  });

  it("requires a justification note before saving risk config, and says so", async () => {
    render(<Config />);

    const saveButtons = await screen.findAllByRole("button", { name: "Save" });
    fireEvent.click(saveButtons[RISK_SAVE]);

    expect(api.saveRiskConfig).not.toHaveBeenCalled();
    expect(await screen.findByText(/justification is required/i)).toBeInTheDocument();
  });

  it("surfaces a save failure instead of silently discarding it", async () => {
    vi.mocked(api.saveCandidateUniverse).mockRejectedValue(new Error("422: tickers must be uppercase"));

    render(<Config />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Save" }))[CANDIDATES_SAVE]);

    expect(await screen.findByText(/422: tickers must be uppercase/)).toBeInTheDocument();
  });

  it("renders an env settings card populated from the fetched env_values", async () => {
    render(<Config />);

    expect(await screen.findByText("Env settings")).toBeInTheDocument();
    expect(await screen.findByLabelText(/discovery_slots_per_cycle/)).toHaveValue(2);
    expect(screen.getByLabelText(/watchdog_check_interval_minutes/)).toHaveValue(20);
    expect(screen.getByLabelText(/pre_market_cron/)).toHaveValue("35 9 * * 1-5");
    expect(screen.getByLabelText(/midday_cron/)).toHaveValue("30 12 * * 1-5");
    expect(screen.getByLabelText(/tradingagents_deep_think_model/)).toHaveValue("gpt-oss:120b-cloud");
    expect(screen.getByLabelText(/tradingagents_quick_think_model/)).toHaveValue("qwen2.5:7b-instruct");
  });

  it("saves edited env settings, coercing the numeric fields", async () => {
    vi.mocked(api.saveEnvSettings).mockResolvedValue({ saved: true });

    render(<Config />);

    fireEvent.change(await screen.findByLabelText(/discovery_slots_per_cycle/), { target: { value: "5" } });
    fireEvent.click((await screen.findAllByRole("button", { name: "Save" }))[ENV_SAVE]);

    expect(api.saveEnvSettings).toHaveBeenCalledWith({
      discovery_slots_per_cycle: 5,
      watchdog_check_interval_minutes: 20,
      pre_market_cron: "35 9 * * 1-5",
      midday_cron: "30 12 * * 1-5",
      tradingagents_deep_think_model: "gpt-oss:120b-cloud",
      tradingagents_quick_think_model: "qwen2.5:7b-instruct",
    });
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("shows a 422 from an invalid cron string rather than failing silently", async () => {
    vi.mocked(api.saveEnvSettings).mockRejectedValue(new Error("422: invalid pre_market_cron: bad value"));

    render(<Config />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Save" }))[ENV_SAVE]);

    expect(await screen.findByText(/invalid pre_market_cron/)).toBeInTheDocument();
  });
});
