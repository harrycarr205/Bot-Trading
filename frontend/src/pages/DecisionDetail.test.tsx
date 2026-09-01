import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import DecisionDetail from "./DecisionDetail";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { decisionDetail: vi.fn() } }));

describe("DecisionDetail page", () => {
  it("renders the transcript in role order with a link back to the ticker", async () => {
    vi.mocked(api.decisionDetail).mockResolvedValue({
      id: "r1", ticker: "AAPL", run_type: "pre_market", started_at: "2026-08-24T09:00:00",
      outcome: "decision_recorded", market_status: "open",
      decision: { id: "d1", rating: "Buy", decision: "buy", reasoning_summary: "strong buy" },
      transcripts: [{ role: "market_analyst", content: "trending up" }, { role: "trader", content: "buy it" }],
      order_id: "o1",
    });

    render(
      <MemoryRouter initialEntries={["/decisions/r1"]}>
        <Routes><Route path="/decisions/:id" element={<DecisionDetail />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("trending up")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "AAPL" })).toHaveAttribute("href", "/ticker/AAPL");
  });
});
