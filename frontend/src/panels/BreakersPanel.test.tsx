import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { panel } from "./BreakersPanel";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { overview: vi.fn() } }));

describe("BreakersPanel", () => {
  it("shows an active breaker's type and reason", async () => {
    vi.mocked(api.overview).mockResolvedValue({
      snapshot: null, heartbeat: null, heartbeat_stale: false,
      active_breakers: [{ id: "1", breaker_type: "daily", trigger_reason: "4% drawdown", tripped_at: "2026-08-24T09:00:00" }],
    });

    render(<panel.Component />);

    expect(await screen.findByText(/daily/)).toBeInTheDocument();
    expect(screen.getByText(/4% drawdown/)).toBeInTheDocument();
  });

  it("shows a clear message when there are no active breakers", async () => {
    vi.mocked(api.overview).mockResolvedValue({ snapshot: null, heartbeat: null, heartbeat_stale: false, active_breakers: [] });

    render(<panel.Component />);

    expect(await screen.findByText(/no active circuit breakers/i)).toBeInTheDocument();
  });
});
