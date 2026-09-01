import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Config from "./Config";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { config: vi.fn(), saveCandidateUniverse: vi.fn(), saveRiskConfig: vi.fn(), saveEnvSettings: vi.fn() },
}));

describe("Config page", () => {
  it("requires a justification note before saving risk config", async () => {
    vi.mocked(api.config).mockResolvedValue({
      tickers: ["AAPL"],
      risk_config: { max_position_pct: 0.1, cash_reserve_pct: 0.2, stop_loss_pct: 0.08, daily_drawdown_breaker_pct: 0.03, weekly_drawdown_breaker_pct: 0.08, stale_data_max_age_minutes: 15 },
      env_values: { discovery_slots_per_cycle: "2" },
    });

    render(<Config />);

    const saveButtons = await screen.findAllByRole("button", { name: "Save" });
    fireEvent.click(saveButtons[1]); // risk config's Save button

    expect(api.saveRiskConfig).not.toHaveBeenCalled();
  });
});
