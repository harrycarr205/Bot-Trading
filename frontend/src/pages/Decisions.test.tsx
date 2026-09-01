import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Decisions from "./Decisions";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { decisions: vi.fn() } }));

describe("Decisions page", () => {
  it("lists runs with links to ticker and decision detail", async () => {
    vi.mocked(api.decisions).mockResolvedValue({
      runs: [{ id: "r1", ticker: "AAPL", run_type: "pre_market", started_at: "2026-08-24T09:00:00", outcome: "decision_recorded", decision: { id: "d1", rating: "Buy", decision: "buy", reasoning_summary: "x" } }],
    });

    render(<MemoryRouter><Decisions /></MemoryRouter>);

    expect(await screen.findByRole("link", { name: "AAPL" })).toHaveAttribute("href", "/ticker/AAPL");
  });
});
