import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TickerStrip } from "./TickerStrip";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { overview: vi.fn() },
}));

describe("TickerStrip", () => {
  it("shows equity and LIVE once the overview loads", async () => {
    vi.mocked(api.overview).mockResolvedValue({
      snapshot: { snapshot_date: "2026-08-24", equity: 102340, cash: 80000 },
      heartbeat: { last_seen_at: "2026-08-24T09:00:00", last_run_type: "pre_market", last_ticker: "AAPL" },
      heartbeat_stale: false,
      active_breakers: [],
    });

    render(<TickerStrip />);

    expect(await screen.findByText(/102,340/)).toBeInTheDocument();
    expect(screen.getByText(/LIVE/i)).toBeInTheDocument();
  });

  it("flags a failed overview fetch instead of silently rendering nothing", async () => {
    vi.mocked(api.overview).mockRejectedValue(new Error("network down"));

    render(<TickerStrip />);

    expect(await screen.findByText("DATA UNAVAILABLE")).toBeInTheDocument();
  });
});
